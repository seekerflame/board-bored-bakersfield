#!/usr/bin/env python3
"""revenue_desk: money-path health, collections aging, prospect fact sheets, grounded drafts.

Reads only: the pricing file, the public ledger URL and data/board.json. Writes a local
report. NEVER sends anything to anyone: drafts are for a human to read and send.
Every number and business name in a draft must trace to the fact sheet or the draft is rejected.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netfetch  # noqa: E402,F401  (side effect: IPv4-first resolver)
from textutil import cover, norm  # noqa: E402

HOME = os.path.expanduser("~")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_PRICING = os.path.join(HOME, "bbk-app", "data", "pricing.json")
DEFAULT_LEDGER = "https://board-bored-civic-ops-240816253301.us-central1.run.app/api/ledger"
DEFAULT_BOARD = os.path.join(REPO, "data", "board.json")
PUBLIC_SECTOR = ("library", "county", "city of", "college", "university", "csub", "school", "district", "park")
SUFFIX = re.compile(r"\b(co|company|inc|llc|ltd|corp)$")
ALLOWED_WORDS = {"board", "bored", "bakersfield", "kern", "first", "friday", "nick", "square", "hi", "hey",
                 "thanks", "map", "free", "featured", "listed", "anchor", "monthly", "season", "once"}


def http_get(url, opener=urllib.request.urlopen, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "BoardBoredRevenueDesk/1.0"})
    try:
        with opener(req, timeout=timeout) as r:
            return (r.status if hasattr(r, "status") else 200), r.geturl(), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception:
        return None, url, ""


def payments_paused(pricing):
    """Nick's switch (pricing.json -> payments.paused): while on, no payment link is checked, offered or chased."""
    return bool((pricing.get("payments") or {}).get("paused"))


def check_money_path(pricing, opener=urllib.request.urlopen):
    rows = []
    if payments_paused(pricing):
        return rows
    for c in pricing.get("combos", []):
        url = c.get("square_checkout_url")
        if not url:
            continue
        status, final, _ = http_get(url, opener)
        ok = status == 200 and "square" in final.lower()
        rows.append({"tier": c["tier"], "cadence": c["cadence"], "price": c["price"], "url": url,
                     "status": status, "ok": ok})
    return rows


def ledger_snapshot(ledger, today):
    pledges = []
    for p in ledger.get("pipeline_pledges", []):
        try:
            age = (today - dt.date.fromisoformat(p["date"])).days
        except Exception:
            age = None
        pledges.append({"id": p.get("id"), "name": p.get("sponsor_name"), "tier": p.get("tier"),
                        "cadence": p.get("cadence"), "amount": p.get("amount"), "age_days": age,
                        "status": p.get("status")})
    s = ledger.get("summary", {})
    return {"collected": s.get("total_collected", 0), "pledged": s.get("total_pledged_pipeline", 0),
            "transactions": len(ledger.get("transactions", [])), "pledges": pledges}


def venue_key(name):
    return SUFFIX.sub("", norm(name)).strip()


def prospects(board, exclude_names=(), top=5):
    agg = collections.defaultdict(lambda: {"names": collections.Counter(), "weekly": 0, "dated": 0,
                                           "listings": [], "address": None})
    for e in board.get("city_events", []):
        v = e.get("venue")
        if not v or any(w in v.lower() for w in PUBLIC_SECTOR):
            continue
        a = agg[venue_key(v)]
        a["names"][v] += 1
        s = e.get("schedule") or {}
        if "weekday" in s or s.get("ongoing"):
            a["weekly"] += 1
        else:
            a["dated"] += 1
        a["listings"].append(e.get("name"))
        a["address"] = a["address"] or e.get("address")
    out = []
    for key, a in agg.items():
        name = a["names"].most_common(1)[0][0]
        if any(cover(name, x) >= 0.8 or cover(x, name) >= 0.8 for x in exclude_names):
            continue
        out.append({"name": name, "address": a["address"], "weekly_slots": a["weekly"],
                    "dated_events": a["dated"], "listings": list(dict.fromkeys(a["listings"]))[:4]})
    out.sort(key=lambda p: (-(p["weekly_slots"] * 3 + p["dated_events"]), p["name"]))
    return out[:top]


def offer_for(pricing, tier="Featured", cadence="monthly"):
    if payments_paused(pricing):
        return None
    for c in pricing.get("combos", []):
        if c["tier"] == tier and c["cadence"] == cadence:
            return {"tier": tier, "cadence": cadence, "price": c["price"]}
    return None


def allowed_numbers(sheet):
    nums = set()
    for k in ("weekly_slots", "dated_events"):
        nums.add(str(sheet.get(k, "")))
    if sheet.get("offer"):
        nums.add(str(sheet["offer"]["price"]))
    nums.update(re.findall(r"\d+", sheet.get("address") or ""))
    nums.update(re.findall(r"\d+", sheet.get("name") or ""))
    nums.update(re.findall(r"\d+", " ".join(sheet.get("listings", []))))
    return nums


def verify_draft(text, sheet):
    """Return list of violations; empty list means every number and name traces to the sheet."""
    bad = []
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    ok_nums = allowed_numbers(sheet)
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        if n.replace(",", "").rstrip(".") not in ok_nums:
            bad.append("number not in fact sheet: " + n)
    known = set(norm(sheet.get("name", "")).split()) | set(norm(" ".join(sheet.get("listings", []))).split()) | ALLOWED_WORDS
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        words = re.findall(r"[A-Za-z][A-Za-z'&]*", sent)
        for w in words[1:]:
            if w[0].isupper() and norm(w) and not set(norm(w).split()) <= known:
                bad.append("name not in fact sheet: " + w)
    return bad


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def template_draft(sheet):
    offer = sheet.get("offer")
    have = []
    if sheet["weekly_slots"]:
        have.append(_plural(sheet["weekly_slots"], "weekly slot"))
    if sheet["dated_events"]:
        have.append(_plural(sheet["dated_events"], "dated event"))
    parts = ["Hi %s team, it's Nick from Board Bored." % sheet["name"],
             "Right now we list %s for you on the Bakersfield map, free." % " and ".join(have)]
    names = list(dict.fromkeys(sheet["listings"]))[:3]
    if names:
        parts.append("That includes %s." % ", ".join(names))
    if offer:
        parts.append("If you want a Featured spot, it is $%s %s. Listing stays free either way." % (offer["price"], offer["cadence"]))
    return " ".join(parts)


def draft_with_model(sheet, model, ask, retries=2):
    """ask(model, prompt) -> str. Local model writes; verify_draft decides. Falls back to the template."""
    prompt = ("Write a friendly 3-sentence note from Nick (Board Bored, a free Bakersfield events map) to the owner of "
              "%s. Use ONLY these facts and no other numbers or business names:\n%s\nPlain human tone, no hype." %
              (sheet["name"], json.dumps(sheet)))
    for _ in range(retries + 1):
        try:
            text = ask(model, prompt).strip()
        except Exception:
            break
        bad = verify_draft(text, sheet)
        if not bad:
            return text, "model:" + model
        prompt += "\nYour last draft was rejected: " + "; ".join(bad[:4]) + ". Fix those."
    return template_draft(sheet), "template"


def build_report(pricing, ledger, board, today, stops=(), ask=None, model=None, opener=urllib.request.urlopen):
    money = check_money_path(pricing, opener)
    snap = ledger_snapshot(ledger, today)
    excl = list(stops) + [p["name"] for p in snap["pledges"] if p["name"]]
    sheets = []
    for p in prospects(board, excl):
        p["offer"] = offer_for(pricing)
        text, how = draft_with_model(p, model, ask) if (ask and model) else (template_draft(p), "template")
        assert not verify_draft(text, p), "template draft must always verify"
        sheets.append({"sheet": p, "draft": text, "drafted_by": how})
    live = {(m["tier"], m["cadence"]): m for m in money}
    paused = payments_paused(pricing)
    nudges = []
    for p in snap["pledges"]:
        if paused:
            nudges.append("%s said yes to %s (%s) at $%s %s days ago. ON HOLD: payments are paused, do not ask for money. Keep the relationship warm." %
                          (p["name"], p["tier"], p["cadence"], p["amount"], p["age_days"]))
            continue
        m = live.get((p["tier"], p["cadence"]))
        link = m["url"] if m and m["ok"] else "[PAYMENT LINK NOT LIVE YET]"
        nudges.append("Hi %s: you said yes to %s (%s) at $%s %s days ago. Pay here: %s" %
                      (p["name"], p["tier"], p["cadence"], p["amount"], p["age_days"], link))
    return {"money_path": money, "ledger": snap, "nudges": nudges, "prospects": sheets, "asof": today.isoformat(),
            "payments_paused": paused, "pause": (pricing.get("payments") or {}) if paused else {}}


def render(rep):
    m = rep["money_path"]
    good = sum(1 for x in m if x["ok"])
    lines = ["# Revenue desk %s" % rep["asof"], ""]
    if rep.get("payments_paused"):
        lines += ["## Money path: PAUSED since %s" % (rep["pause"].get("since") or "unknown"), "- %s" % (rep["pause"].get("reason") or "paused by Nick")]
    else:
        lines += ["## Money path: %d of %d payment links work" % (good, len(m))]
        lines += ["- %s %s $%s -> %s %s" % (x["tier"], x["cadence"], x["price"], x["status"], "OK" if x["ok"] else "DEAD") for x in m]
    l = rep["ledger"]
    lines += ["", "## Ledger: collected $%s, pledged $%s, %d transactions" % (l["collected"], l["pledged"], l["transactions"]), ""]
    lines += ["- " + n for n in rep["nudges"]]
    lines += ["", "## Prospects (from our own listings)"]
    for p in rep["prospects"]:
        s = p["sheet"]
        lines += ["", "### %s (%d weekly, %d dated)" % (s["name"], s["weekly_slots"], s["dated_events"]), p["draft"],
                  "_drafted by %s; every number and name verified against the fact sheet_" % p["drafted_by"]]
    return "\n".join(lines) + "\n"


def summary(rep):
    m = rep["money_path"]
    good = sum(1 for x in m if x["ok"])
    l = rep["ledger"]
    top = ", ".join("%s (%d weekly)" % (p["sheet"]["name"], p["sheet"]["weekly_slots"]) for p in rep["prospects"][:3])
    oldest = max([p["age_days"] or 0 for p in l["pledges"]] or [0])
    if rep.get("payments_paused"):
        head = "MONEY PATH: PAUSED since %s (no links checked, none offered)" % (rep["pause"].get("since") or "unknown")
    else:
        head = "MONEY PATH: %d/%d payment links work%s" % (good, len(m), "" if good == len(m) else " (pay buttons are dead)")
    return ("%s\nCollected $%s, pledged $%s (oldest pledge %d days)\nTop prospects: %s"
            % (head, l["collected"], l["pledged"], oldest, top))


def ollama_ask(model, prompt, host="http://localhost:11434"):
    req = urllib.request.Request(host + "/api/generate", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                                                  "options": {"temperature": 0.2}}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pricing", default=DEFAULT_PRICING)
    ap.add_argument("--ledger-url", default=DEFAULT_LEDGER)
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--today", default=None)
    ap.add_argument("--out", default=os.path.join(HOME, ".boardbored", "reports"))
    ap.add_argument("--llm", default=None, help="local Ollama model for drafts (template used if omitted)")
    ap.add_argument("--check-money-path", action="store_true", help="exit 1 if any payment link is dead")
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    pricing = json.load(open(a.pricing))
    if a.check_money_path:
        if payments_paused(pricing):
            print("payments paused: no payment links are checked or offered")
            return 0
        rows = check_money_path(pricing)
        for r in rows:
            print("%-9s %-8s $%-4s %s %s" % (r["tier"], r["cadence"], r["price"], r["status"], "OK" if r["ok"] else "DEAD"))
        return 0 if all(r["ok"] for r in rows) else 1
    status, _, body = http_get(a.ledger_url)
    ledger = json.loads(body) if status == 200 else {}
    board = json.load(open(a.board))
    stops = []
    bs, _, bbody = http_get(a.ledger_url.rsplit("/api/", 1)[0] + "/api/board")
    if bs == 200:
        try:
            stops = [x.get("name", "") for x in json.loads(bbody).get("stops", [])]
        except Exception:
            pass
    rep = build_report(pricing, ledger, board, today, stops=stops, ask=ollama_ask if a.llm else None, model=a.llm)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "revenue_desk_%s.md" % today.isoformat())
    open(path, "w").write(render(rep))
    print(summary(rep))
    print("report: " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
