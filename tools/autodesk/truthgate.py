"""truthgate: decides what is true. No model is ever consulted here.

A candidate event (from any voice: a deterministic parser, a publisher's JSON-LD, a local LLM)
carries a verbatim `quote` from its source. Every claimed field must be located in that quote
(or in a governing heading) by the parsers in textutil. Then independent voices are compared:

  tier A  every required field grounded, >= min_families independent voices agree, no conflicts,
          nothing stripped        -> safe to publish
  tier B  grounded by at least one voice but not corroborated / conflicting / a field stripped
                                  -> one-tap human review
  drop    nothing grounded, past, or not an event

Required fields: name, venue, date. Optional (stripped, not fatal, tier capped at B): start, end, cost, link.
"""
import datetime as dt
import html
import re

from textutil import cover, find_dates, find_times, governing_heading, hhmm_to_min, locate_quote, locate_quote_fuzzy, norm, squash, tokens

REQUIRED = ("name", "venue", "date")
OPTIONAL = ("start", "end", "cost", "link", "address")
NOT_AN_EVENT = re.compile(r"(?i)\b(season pass|gift card|gift certificate|parking|membership|voucher|donation|merch(andise)?|vip upgrade|"
                          r"ticket protection|add[- ]?on)\b")
MAX_FUTURE_DAYS = 548


def clean(c):
    c = dict(c)
    for k in ("name", "venue", "cost", "heading"):
        if isinstance(c.get(k), str):
            c[k] = re.sub(r"\s+", " ", html.unescape(c[k])).strip()
    for k in OPTIONAL + ("date",):
        if c.get(k) in ("", "null", "None", "N/A", "n/a", "unknown"):
            c[k] = None
    return c


def gate(c, sources, today, default_venue=None, skip=()):
    """Return (clean_candidate, hard_failures, stripped_fields). Hard failures list is empty on pass.
    skip: required fields this voice does not vouch for (e.g. venue from a JSON-LD block known to carry the wrong one)."""
    c = clean(c)
    fails, stripped = [], []
    src = sources.get(c.get("source_id"))
    if src is None:
        return c, ["no_source"], stripped
    quote = re.sub(r"\s+", " ", c.get("quote") or "").strip()
    span = locate_quote(src, quote) or locate_quote(src, html.unescape(quote))
    if span is None:
        fz = locate_quote_fuzzy(src, quote)
        if fz is None:
            return c, ["quote_not_in_source"], stripped
        span, quote = (fz[0], fz[1]), re.sub(r"\s+", " ", fz[2]).strip()  # judge against the page's own words
        c["quote"], c["quote_repaired"] = quote, True
    if len(re.findall(r"[\u2022\u25aa\u25cf]", quote)) > 1:
        return c, ["quote_spans_records"], stripped  # two bullets in one quote: name from one, venue/time from the other
    name = c.get("name") or ""
    if not (3 <= len(name) <= 140) or re.search(r"[<>{}]", name):
        fails.append("name_malformed")
    elif cover(name, quote) < 0.8:
        fails.append("name_not_in_quote")
    if NOT_AN_EVENT.search(name):
        fails.append("not_an_event")

    # date: claimed ISO date must be produced by a date token in the quote (or a heading located earlier in the source)
    try:
        d = dt.date.fromisoformat(c.get("date") or "")
    except ValueError:
        d = None
        fails.append("date_malformed")
    if d:
        gh = governing_heading(src, span[0], today)
        if gh:
            # The document is organised by weekday headings: the heading above the record is its date. A date found
            # inside the quote (e.g. the NEXT section's heading swept in by a long window) can never override it.
            if d != gh[1]:
                fails.append("date_not_in_quote")
        else:
            pool = [t.date for t in find_dates(quote, today)]
            h = c.get("heading")
            if d not in pool and h:
                hs = locate_quote(src, h)
                if hs and hs[0] <= span[0]:
                    pool += [t.date for t in find_dates(h, today)]
            if d not in pool:
                fails.append("date_not_in_quote")
        if d < today:
            fails.append("past")
        elif (d - today).days > MAX_FUTURE_DAYS:
            fails.append("date_too_far")

    venue = c.get("venue") or ""
    if "venue" in skip:
        c["venue"] = None
    elif not venue:
        fails.append("venue_missing")
    elif cover(venue, quote) < 0.75:
        ok = False
        h = c.get("heading")
        if h and cover(venue, h) >= 0.75:
            hs = locate_quote(src, h)
            ok = bool(hs and hs[0] <= span[0])
        if not ok and default_venue and cover(venue, default_venue) >= 0.75 and cover(default_venue, venue) >= 0.75:
            ok = True  # single-venue source: the source itself is the venue
        if not ok:
            fails.append("venue_not_in_quote")

    times = {m for m, _, _ in find_times(quote)}
    for k in ("start", "end"):
        v = c.get(k)
        if v is None:
            continue
        if hhmm_to_min(v) is None or hhmm_to_min(v) not in times:
            stripped.append(k)
            c[k] = None
    if c.get("end") and not c.get("start"):
        c["end"] = None
    if c.get("cost"):
        digits = re.findall(r"\d+(?:\.\d+)?", c["cost"])
        low = c["cost"].lower()
        if (digits and not all(x in quote for x in digits)) or ("free" in low and "free" not in quote.lower()):
            stripped.append("cost")
            c["cost"] = None
    if c.get("address") and squash(c["address"]) not in squash(quote):
        stripped.append("address")
        c["address"] = None
    if c.get("link") and c["link"] not in src:
        stripped.append("link")
        c["link"] = None
    return c, fails, stripped


def same_event(a, b):
    """Same date, same title, and (when both name one) a compatible venue. A shared link is NOT identity:
    a fair's homepage is linked from every event at the fair."""
    if a["date"] != b["date"]:
        return False
    if not (cover(a["name"], b["name"]) >= 0.75 and cover(b["name"], a["name"]) >= 0.75):
        return False
    if a.get("venue") and b.get("venue"):
        return cover(a["venue"], b["venue"]) >= 0.6 or cover(b["venue"], a["venue"]) >= 0.6
    return True


def consensus(passed, min_families=2, untrusted=None):
    """passed: list of (candidate, stripped_fields, family). untrusted: {family: {field,...}} fields a family may not vouch for.
    Returns list of events: {fields..., tier, families, conflicts, stripped, evidence}."""
    untrusted = untrusted or {}
    groups = []

    def adj(c, f):  # a family that may not vouch for venue also may not influence who counts as "the same event"
        return dict(c, venue=None) if "venue" in untrusted.get(f, ()) else c
    for c, stripped, fam in passed:
        for g in groups:
            if any(same_event(adj(m[0], m[2]), adj(c, fam)) for m in g["members"]):
                g["members"].append((c, stripped, fam))
                break
        else:
            groups.append({"members": [(c, stripped, fam)]})
    out = []
    for g in groups:
        mem = g["members"]
        fams = sorted({f for _, _, f in mem})
        conflicts = []

        def vouch(field):
            return [(c, f) for c, _, f in mem if c.get(field) and field not in untrusted.get(f, ())]
        ev = {"evidence": [{"family": f, "quote": c.get("quote"), "source_id": c.get("source_id")} for c, _, f in mem]}
        for field in ("name", "venue", "date", "start", "end", "cost", "link", "address"):
            vs = vouch(field)
            if not vs:
                ev[field] = None
                continue
            # prefer the value held by the most families; ties -> first (deterministic order by family name)
            vals = {}
            for c, f in sorted(vs, key=lambda x: x[1]):
                key = c[field] if field in ("date", "start", "end", "link") else norm(c[field])  # address/cost/name/venue compare normalised
                vals.setdefault(key, []).append((c[field], f))
            if len(vals) > 1:
                compat = False
                if field in ("name", "venue"):
                    ks = list(vals)
                    compat = all(cover(ks[0], k) >= 0.75 and cover(k, ks[0]) >= 0.75 for k in ks[1:])
                if not compat:
                    conflicts.append({"field": field, "values": {k: sorted({f for _, f in v}) for k, v in vals.items()}})
            best = max(vals.values(), key=lambda v: (len({f for _, f in v}), -len(v[0][0])))
            ev[field] = best[0][0]
        allstripped = sorted({s for _, st, _ in mem for s in st})
        # a field one voice could not ground is only 'stripped' for the event if no other voice grounded it
        allstripped = [s for s in allstripped if not ev.get(s)]
        agree_name_date = len(fams) >= min_families
        venue_ok = bool(ev.get("venue"))
        tier = "A" if (agree_name_date and venue_ok and not conflicts and not allstripped) else "B"
        reasons = []
        if len(fams) < min_families:
            reasons.append("single_voice" if len(fams) == 1 else "insufficient_agreement")
        if not venue_ok:
            reasons.append("venue_unvouched")
        if conflicts:
            reasons.append("conflict")
        if allstripped:
            reasons.append("stripped:" + ",".join(allstripped))
        ev.update({"tier": tier, "families": fams, "conflicts": conflicts, "stripped": allstripped, "reasons": reasons})
        out.append(ev)
    return out


def event_id(ev):
    """Stable across runs: venue+date+name tokens (not model output ordering, not link-less hashes of prose)."""
    import hashlib
    key = "|".join([ev["date"], norm(ev["venue"] or "")[:30], " ".join(sorted(set(tokens(ev["name"]))))])
    return "ad_" + hashlib.sha256(key.encode()).hexdigest()[:12]
