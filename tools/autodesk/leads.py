#!/usr/bin/env python3
"""leads: one append-only registry of every Bakersfield business we know, ranked by how warm they already are.

Sources (all read-only): our own board (live + archive listings, corridor storefronts), the newsletter outreach report,
the pledge ledger and the outreach log. `recon` reads a business's OWN website (robots honored, polite, cached) and records
only contact facts printed on that page, each with its source URL and date. NOTHING is ever sent to anyone.
Stage changes, notes and recon findings are append-only events; sync never deletes a lead or resets its stage/notes.
Lead data stays in ~/.boardbored/leads/ (never in the public site repo).

  python3 leads.py sync                       merge every source into the registry
  python3 leads.py recon [--limit N] [--id X] read each lead's own website, record literal contact facts
  python3 leads.py mark "<name>" contacted|replied|in_talks|partner|declined|do_not_contact [--note ...]
  python3 leads.py note "<name>" "text"
  python3 leads.py set "<name>" website|instagram|facebook|email|phone <value> --source "<where you saw it>"
  python3 leads.py list [--tier A,B] [--stage new] [--all] [--top 25]
  python3 leads.py export [--copy-to DIR]     leads.csv + leads.md
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
from html import unescape
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netfetch import FetchRefused, PoliteFetcher  # noqa: E402
from revenue_desk import PUBLIC_SECTOR, venue_key  # noqa: E402
from textutil import cover, html_to_text, norm, tokens  # noqa: E402

HOME = os.path.expanduser("~")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
STORE = os.environ.get("BB_LEADS_DIR") or os.path.join(HOME, ".boardbored", "leads")
BOARD = os.path.join(REPO, "data", "board.json")
LEDGER = os.path.join(HOME, "bbk-app", "data", "ledger.json")
REPORTS = os.path.join(HOME, ".boardbored", "reports")
OUTREACH_LOG = os.path.join(HOME, ".boardbored", "outreach", "log.jsonl")
CACHE = os.path.join(HOME, ".boardbored", "autodesk", "cache")
STAGES = ("new", "contacted", "replied", "in_talks", "partner", "declined", "do_not_contact")
OUTREACH_STAGE = {"contacted": "contacted", "replied": "replied", "joined": "partner", "declined": "declined"}
CONTACT_KINDS = ("website", "instagram", "facebook", "email", "phone")
NOT_OWN_SITE = ("tixr.", "eventbrite.", "simpletix.", "axs.", "ticketmaster.", "allevents.", "universe.com", "runsignup.", "givebutter.",
                "qgiv.", "eventcreate.", "regmovies.", "facebook.com", "instagram.com", "google.", "bing.com", "duckduckgo.com",
                "linktr.ee", "meetup.com", "youtube.com", "tiktok.com", "x.com", "twitter.com")
NOISE_MAIL = ("example.com", "sentry", "wixpress", "godaddy", "domain.com", "yourdomain", "email.com", "u003e")
IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "share", "tv", "stories", "direct", "about", "legal", "web", "developer"}
FB_SKIP = {"sharer", "sharer.php", "dialog", "tr", "plugins", "events", "groups", "share", "login", "watch", "photo", "photo.php",
           "profile.php", "pages", "hashtag", "policies", "help", "marketplace", "permalink.php", "story.php"}
RECON_FRESH_DAYS = 30
EXTRA_PUBLIC = ("church", "elementary", "high school", "middle school", "chamber of commerce", "fire department", "police", "fire station")


# ---------- small helpers ----------
def now_iso(now=None):
    return (now or dt.datetime.now()).isoformat(timespec="seconds")


def slug(name):
    return re.sub(r"\s+", "-", venue_key(name)).strip("-") or "unknown"


STREET = r"(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|way|ln|lane|ct|court|hwy|highway|pkwy|parkway|pl|place)"
PLACEHOLDER = re.compile(r"^(tbd|tba|online|virtual|various( locations?)?|location provided.*|private (residence|home)|your home|n/?a|"
                         r"see (website|link).*|multiple locations?|call for (location|details)|by appointment)$", re.I)
ACRONYM = re.compile(r"^[A-Z0-9]{2,5}$")  # shared by unrelated orgs (two different 'ACK's): never used to join entities
FOODISH = re.compile(r"\b(cafe|café|grill|bar|pub|kitchen|diner)\b", re.I)


def looks_like_address(s):
    s = s.strip().rstrip(".,")
    if re.match(r"^\d{1,6}\s+\S", s) and re.search(r"\b" + STREET + r"\b\.?(?:[\s,#].*)?$", s, re.I):
        return True
    toks = s.split()
    return 1 < len(toks) <= 4 and bool(re.search(r"\b(?:st|ave|blvd|rd|dr|ln|hwy|pkwy|ct|pl)\.?$", s, re.I)) and not FOODISH.search(s)


def clean_name(raw):
    """(business name or None, address hint). Strips `[url]` markup, `Name | address` and `Name (123 Main St)` forms;
    returns None for bare street addresses and placeholders such as 'Location provided after RSVP'."""
    s = re.sub(r"\[[^\]]*\]", "", (raw or "").replace("’", "'")).strip()
    addr = None
    if " | " in s:
        s, addr = (x.strip() for x in s.split(" | ", 1))
    m = re.match(r"^(.*?)\s*\((\d[^)]*)\)\s*$", s)
    if m:
        s, addr = m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.*?)\s+with\s+[A-Z][\w' .&-]*$", s)       # "Jerry's Pizza & Pub with Kev King": the performer is not the venue
    if m and len([t for t in norm(m.group(1)).split() if len(t) >= 3]) >= 2:  # but "Art with Heart" is the whole name
        s = m.group(1)
    s = re.sub(r"\s+SOLD OUT$|\s+(?:parking\s+)?lot$", "", s, flags=re.I).strip()
    if not s or PLACEHOLDER.match(s):
        return None, None
    if looks_like_address(s):
        return None, s
    return s, addr


def split_names(name):
    """Every spelling worth matching on: 'BAA (Bakersfield Art Association)' and 'Prairie Fire, The Padre Hotel' each
    yield their parts. Acronyms are dropped; the comma tail ('Tin Cup Coffee, Shafter') is a location, not the name."""
    s, _ = clean_name(name)
    if not s:
        return []
    names = [s]
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", s)
    if m:
        outer, inner = m.group(1).strip(), m.group(2).strip()
        if ACRONYM.match(outer):
            names = [inner]  # 'BAA (Bakersfield Art Association)': the long form is the name
        else:
            names = [outer] + ([inner] if len(inner.split()) >= 2 else [])
    if "," in names[0]:
        head = names[0].split(",", 1)[0].strip()
        if len(head) >= 4:
            names.append(head)
    return [n for n in dict.fromkeys(names) if n and not ACRONYM.match(n)]


MERGE_EXTRA = {"bakersfield", "co", "company", "inc", "llc", "the", "hotel", "theatre", "theater", "center", "centre", "event", "events",
               "lounge", "pub", "community", "tasting", "room", "by", "hilton", "central", "administration", "studio", "studios", "venue"}


def same_business(a, b):
    """One name is the other plus only filler words (Jerry's Pizza / Jerry's Pizza & Pub, Sky Zone / Sky Zone Bakersfield).
    Anything that adds a real word (Kevin Harvick's Kern Raceway, The Underground at The Ovation) stays a separate lead."""
    ta, tb = set(tokens(a)), set(tokens(b))
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small) >= 2 and small <= big and (big - small) <= MERGE_EXTRA


def is_public(name):
    n = (name or "").lower()
    return any(w in n for w in PUBLIC_SECTOR + EXTRA_PUBLIC)


def own_site_candidate(url):
    h = urlparse(url or "").netloc.lower()
    return bool(h) and not any(t in h for t in NOT_OWN_SITE)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


class Store:
    def __init__(self, root=STORE):
        self.root = root
        self.path = os.path.join(root, "leads.json")
        self.log_path = os.path.join(root, "events.jsonl")
        self.leads = load_json(self.path, {}).get("leads", {})

    def event(self, lead_id, kind, detail, now=None):
        os.makedirs(self.root, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:  # append-only
            f.write(json.dumps({"ts": now_iso(now), "id": lead_id, "type": kind, "detail": detail}, ensure_ascii=False) + "\n")

    def save(self):
        save_json(self.path, {"version": 1, "leads": self.leads})

    def find(self, *names):
        names = [n for n in names if norm(n)]
        keys = {slug(n) for n in names}
        for k in keys:
            if k in self.leads:
                return self.leads[k]
        for n in names:
            for lead in self.leads.values():
                for a in [lead["name"]] + lead.get("aliases", []):
                    if (cover(n, a) >= 0.8 and cover(a, n) >= 0.8) or same_business(n, a):
                        return lead
        return None

    def ensure(self, name, now=None, **fields):
        names = split_names(name)
        if not names:
            return None
        lead = self.find(*names)
        if lead is None:
            main = names[0]
            lead = {"id": slug(main), "name": main, "aliases": [], "sector": "public" if is_public(main) else "private",
                    "stage": "new", "address": None, "lat": None, "lng": None, "area": None, "categories": [],
                    "on_map": {"weekly": 0, "dated": 0, "listings": []}, "past_events": 0,
                    "newsletter": {"issues": 0, "appearances": 0, "last_seen": ""}, "pledge": None, "corridor": False,
                    "candidates": [], "contact": {}, "recon": {}, "notes": [], "first_seen": (now or dt.datetime.now()).date().isoformat()}
            self.leads[lead["id"]] = lead
            self.event(lead["id"], "created", {"name": main, "sector": lead["sector"]}, now)
        for n in names:
            if n != lead["name"] and n not in lead["aliases"]:
                lead["aliases"].append(n)
        if fields.get("address") in (None, ""):
            fields["address"] = clean_name(name)[1]
        for k in ("address", "lat", "lng", "area"):
            if lead.get(k) in (None, "") and fields.get(k) not in (None, ""):
                lead[k] = fields[k]
        return lead

    def by_ref(self, ref):
        lead = self.leads.get(ref) or self.find(*split_names(ref))
        if lead is None:
            raise KeyError("no lead matches %r (run `list --all` to see names)" % ref)
        return lead


def full_url(url):
    url = (url or "").strip()
    return url if re.match(r"^https?://", url, re.I) or not url else "https://" + url.lstrip("/")


def origin_of(url):
    p = urlparse(full_url(url))
    return "%s://%s/" % (p.scheme, p.netloc) if p.netloc else ""


def merge_leads(store, keep, drop, now=None):
    for n in [drop["name"]] + drop.get("aliases", []):
        if n != keep["name"] and n not in keep["aliases"]:
            keep["aliases"].append(n)
    for c in drop["candidates"]:
        add_candidate(keep, c["kind"], c["url"], c["source"])
    for k, v in drop["contact"].items():
        keep["contact"].setdefault(k, v)
    keep["notes"] = sorted(keep["notes"] + drop["notes"], key=lambda x: x["ts"])
    keep.setdefault("superseded", []).extend(drop.get("superseded", []))
    if keep.get("pledge") is None and drop.get("pledge"):
        keep["pledge"] = drop["pledge"]
    if keep["stage"] == "new" and drop["stage"] != "new":
        keep["stage"] = drop["stage"]
    keep["corridor"] = keep.get("corridor") or drop.get("corridor")
    keep["first_seen"] = min(keep["first_seen"], drop["first_seen"])
    for k in ("address", "lat", "lng", "area"):
        if keep.get(k) in (None, "") and drop.get(k) not in (None, ""):
            keep[k] = drop[k]
    store.leads.pop(drop["id"], None)
    store.event(keep["id"], "merged", {"dropped": drop}, now)  # the full dropped record stays in the log


def merge_duplicates(store, now=None):
    merged = 0
    while True:
        pair = None
        leads = sorted(store.leads.values(), key=lambda l: (-l.get("score", 0), -len(l["name"]), l["id"]))
        for i, a in enumerate(leads):
            for b in leads[i + 1:]:
                if a["sector"] == b["sector"] and any(same_business(x, y) for x in [a["name"]] + a["aliases"] for y in [b["name"]] + b["aliases"]):
                    pair = (a, b)
                    break
            if pair:
                break
        if not pair:
            return merged
        merge_leads(store, pair[0], pair[1], now)
        merged += 1


def add_candidate(lead, kind, url, source):
    url = full_url(url) if kind == "website" else url
    if url and not any(c["kind"] == kind and c["url"] == url for c in lead["candidates"]):
        lead["candidates"].append({"kind": kind, "url": url, "source": source})


# ---------- sources ----------
def sync_board(store, board, now=None):
    acc = collections.defaultdict(lambda: {"weekly": 0, "dated": 0, "listings": [], "past": 0, "cats": collections.Counter()})
    seen = set()
    for e in board.get("city_events", []):
        v = e.get("venue")
        if not v:
            continue
        lead = store.ensure(v, now, address=e.get("address"), lat=e.get("lat"), lng=e.get("lng"), area=e.get("area"))
        if lead is None:
            continue
        a = acc[lead["id"]]
        s = e.get("schedule") or {}
        if "weekday" in s or s.get("ongoing"):
            a["weekly"] += 1
        else:
            a["dated"] += 1
        if e.get("name") and e["name"] not in a["listings"]:
            a["listings"].append(e["name"])
        if e.get("category"):
            a["cats"][e["category"]] += 1
        if e.get("link") and own_site_candidate(e["link"]):
            add_candidate(lead, "website", e["link"], "link on our listing '%s'" % e.get("name"))
        seen.add(lead["id"])
    for e in board.get("city_events_archive", []):
        v = e.get("venue")
        if not v:
            continue
        lead = store.ensure(v, now, address=e.get("address"), lat=e.get("lat"), lng=e.get("lng"), area=e.get("area"))
        if lead is None:
            continue
        acc[lead["id"]]["past"] += 1
        if e.get("category"):
            acc[lead["id"]]["cats"][e["category"]] += 1
        seen.add(lead["id"])
    for ev in board.get("events", []):
        for b in ev.get("vendors", []) or []:
            if not b.get("name"):
                continue
            lead = store.ensure(b["name"], now, address=b.get("address"), lat=b.get("lat"), lng=b.get("lng"))
            if lead is None:
                continue
            lead["corridor"] = True
            if b.get("category"):
                acc[lead["id"]]["cats"][b["category"]] += 1
            if b.get("website"):
                add_candidate(lead, "website", b["website"], "downtown corridor storefront record")
            if b.get("instagram"):
                add_candidate(lead, "instagram", b["instagram"], "downtown corridor storefront record")
            seen.add(lead["id"])
    for lead in store.leads.values():
        a = acc.get(lead["id"])
        if a:
            lead["on_map"] = {"weekly": a["weekly"], "dated": a["dated"], "listings": a["listings"][:5]}
            lead["past_events"] = a["past"]
            lead["categories"] = [c for c, _ in a["cats"].most_common(3)]
        elif lead["id"] not in seen:
            lead["on_map"] = {"weekly": 0, "dated": 0, "listings": lead["on_map"].get("listings", [])}


def sync_pledges(store, ledger, now=None):
    for p in (ledger or {}).get("pipeline_pledges", []):
        if not p.get("sponsor_name"):
            continue
        lead = store.ensure(p["sponsor_name"], now)
        if lead is None:
            continue
        lead["pledge"] = {"tier": p.get("tier"), "cadence": p.get("cadence"), "amount": p.get("amount"), "date": p.get("date"),
                          "status": p.get("status"), "note": "verbal yes only; payments are paused"}
        if lead["stage"] == "new":
            set_stage(store, lead, "in_talks", "verbal yes on %s (%s)" % (p.get("date"), p.get("tier")), now)


def sync_newsletter(store, rows, now=None):
    for r in rows:
        v = (r.get("venue") or "").strip()
        if not v:
            continue
        lead = store.ensure(v, now, address=r.get("address") or None)
        if lead is None:
            continue
        n = lead["newsletter"]
        n["issues"] = max(n["issues"], int(r.get("issues_featured") or 0))
        n["appearances"] = max(n["appearances"], int(r.get("appearances") or 0))
        n["last_seen"] = max(n["last_seen"], r.get("last_seen") or "")
        kind, url = r.get("contact_kind"), r.get("contact")
        if kind == "website" and own_site_candidate(url):
            add_candidate(lead, "website", url, "link printed with their listing in The Bakersfield Guy's newsletter")
        elif kind == "instagram_profile":
            add_candidate(lead, "instagram", url, "profile link printed in the newsletter")
        elif kind == "facebook_page":
            add_candidate(lead, "facebook", url, "page link printed in the newsletter")


def sync_outreach_log(store, now=None):
    last = {}
    try:
        for ln in open(OUTREACH_LOG, encoding="utf-8"):
            r = json.loads(ln)
            last[r["key"]] = r
    except Exception:
        return
    for key, r in last.items():
        lead = store.find(r.get("venue") or key)
        st = OUTREACH_STAGE.get(r.get("status"))
        if lead and st and lead["stage"] == "new":
            set_stage(store, lead, st, "from outreach log: %s" % (r.get("note") or r.get("status")), now)


def latest_outreach_csv(reports=REPORTS):
    files = sorted(glob.glob(os.path.join(reports, "outreach_*.csv")))
    return files[-1] if files else None


def sync_all(store, board, ledger, outreach_csv=None, now=None):
    before = len(store.leads)
    merge_duplicates(store, now)
    sync_board(store, board, now)
    sync_pledges(store, ledger, now)
    if outreach_csv and os.path.exists(outreach_csv):
        with open(outreach_csv, newline="", encoding="utf-8") as f:
            sync_newsletter(store, list(csv.DictReader(f)), now)
    sync_outreach_log(store, now)
    for lead in store.leads.values():
        lead["score"], lead["tier"], lead["why"] = score_lead(lead)
        lead["updated"] = (now or dt.datetime.now()).date().isoformat()
    store.save()
    return len(store.leads) - before


# ---------- scoring ----------
def score_lead(l):
    s, why = 0, []
    p = l.get("pledge")
    if p:
        s += 10
        why.append("said yes to %s %s ($%s) on %s" % (p.get("tier"), p.get("cadence"), p.get("amount"), p.get("date")))
    om = l["on_map"]
    if om["weekly"]:
        s += 3 * min(om["weekly"], 3)
        why.append("%d weekly regular%s already on our map" % (om["weekly"], "" if om["weekly"] == 1 else "s"))
    if om["dated"]:
        s += min(om["dated"], 4)
        why.append("%d dated event%s on our map" % (om["dated"], "" if om["dated"] == 1 else "s"))
    ni = l["newsletter"]["issues"]
    if ni:
        s += min(ni, 6)
        why.append("featured in %d newsletter issue%s" % (ni, "" if ni == 1 else "s"))
    if l["past_events"]:
        s += 1
        why.append("%d past event%s we archived" % (l["past_events"], "" if l["past_events"] == 1 else "s"))
    if any(l["contact"].get(k) for k in CONTACT_KINDS):
        s += 1
        why.append("we have a verified way to reach them")
    if l.get("corridor"):
        why.append("downtown storefront")
    return s, ("A" if s >= 10 else "B" if s >= 5 else "C" if s >= 2 else "D"), why


# ---------- stage / notes (append-only events) ----------
def set_stage(store, lead, stage, note="", now=None):
    if stage not in STAGES:
        raise ValueError("stage must be one of %s" % ", ".join(STAGES))
    old = lead["stage"]
    lead["stage"] = stage
    store.event(lead["id"], "stage", {"from": old, "to": stage, "note": note}, now)
    if note:
        lead["notes"].append({"ts": now_iso(now), "note": "[%s] %s" % (stage, note)})


def add_note(store, lead, text, now=None):
    lead["notes"].append({"ts": now_iso(now), "note": text})
    store.event(lead["id"], "note", {"note": text}, now)


def set_fact(store, lead, kind, value, source, now=None, auto=False):
    if kind not in CONTACT_KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(CONTACT_KINDS))
    if not source:
        raise ValueError("a source is required: where did you see it?")
    lead["contact"][kind] = {"value": value, "source": source, "verified_at": now_iso(now)}
    if auto:
        lead["contact"][kind]["auto"] = True  # read by recon, not typed by a person
    store.event(lead["id"], "fact", {"kind": kind, "value": value, "source": source}, now)


# ---------- recon: read the business's own site ----------
EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}")


def extract_emails(html):
    """Real addresses only: the last label must be letters (so `bootstrap@5.0.1` and `leaflet@1.9.4` in JS are not emails)."""
    html = unescape(html)
    out = []
    for m in re.findall(r"mailto:([^\"'?\s<>&;\\]+)", html, re.I) + EMAIL.findall(html):
        mm = EMAIL.fullmatch(m.strip().rstrip(".,;")) or EMAIL.search(m)
        e = mm.group(0).lower() if mm else ""
        if e and not any(n in e for n in NOISE_MAIL) and not re.search(r"\.(png|jpg|jpeg|gif|svg|webp|css|js)$", e) and e not in out:
            out.append(e)
    return out


def extract_phones(html):
    """Bakersfield numbers only (661): a chain's HQ line in another area code is not this business's phone."""
    out = []
    for m in re.findall(r"tel:([+\d()\-.\s%]+)", html, re.I) + re.findall(r"\"telephone\"\s*:\s*\"([^\"]+)\"", html) + \
            re.findall(r"\(?\b661\)?[\s.\-]\d{3}[\s.\-]\d{4}\b", html):
        d = re.sub(r"\D", "", m.replace("%20", ""))
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) == 10 and d.startswith("661") and d not in [re.sub(r"\D", "", x) for x in out]:
            out.append("(%s) %s-%s" % (d[:3], d[3:6], d[6:]))
    return out


def extract_socials(html):
    ig, fb = [], []
    for m in re.finditer(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{2,30})/?", html):
        h = m.group(1).lower()
        if h not in IG_SKIP and h not in ig:
            ig.append(h)
    for m in re.finditer(r"https?://(?:www\.|m\.)?facebook\.com/([A-Za-z0-9.\-]{3,60})/?", html):
        h = m.group(1)
        if h.lower() not in FB_SKIP and h.lower() not in [x.lower() for x in fb]:
            fb.append(h)
    return ig, fb


def find_contact_page(html, base):
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
        if "contact" in (m.group(1) + " " + re.sub(r"<[^>]+>", " ", m.group(2))).lower():
            url = urljoin(base, m.group(1))
            if urlparse(url).netloc == urlparse(base).netloc:
                return url
    return None


OWN_STOP = {"the", "and", "inc", "llc", "company", "for", "at"}
COMMON = {"bakersfield", "kern", "county", "historic", "club", "center", "centre", "theatre", "theater", "museum", "art", "arts", "grill",
          "cafe", "coffee", "bar", "pub", "pizza", "brewing", "brewery", "park", "hall", "studio", "school", "restaurant", "kitchen",
          "comedy", "music", "gallery", "market", "venue", "house", "public"}


def host_label(url):
    parts = (urlparse(full_url(url)).netloc.lower().split(":")[0]).split(".")
    if parts and parts[0] == "www":
        parts = parts[1:]
    return re.sub(r"[^a-z0-9]", "", parts[-2] if len(parts) >= 2 else (parts[0] if parts else ""))


def host_matches(name, url):
    """The site's own domain has to be built from the business's name (temblorbrewing.com, bmoa.org for Bakersfield Museum of
    Art). A page that merely MENTIONS a business (a news story, a promoter, a venue calendar) never counts as its site."""
    h = host_label(url)
    if len(h) < 3:
        return False
    all_toks = [t for t in norm(name).split() if t not in {"the", "and"}]
    acr = "".join(t[0] for t in all_toks)
    if len(acr) >= 3 and (h == acr or (h.startswith(acr) and len(acr) >= 4)):
        return True
    toks = [t for t in all_toks if len(t) >= 3 and t not in OWN_STOP]
    hits = [t for t in toks if t in h]
    distinct = [t for t in hits if t not in COMMON]
    return len(hits) >= 2 or (len(hits) == 1 and bool(distinct) and len(toks) <= 3)


def owns_site(lead, url):
    return any(host_matches(n, url) for n in [lead["name"]] + lead.get("aliases", []))


def pick_social(handles, lead, url):
    """Prefer the handle that looks like this business; with no look-alike, accept only an unambiguous single handle."""
    h = host_label(url)
    toks = [t for t in norm(lead["name"]).split() if len(t) >= 4 and t not in COMMON]
    for x in handles:
        xl = re.sub(r"[^a-z0-9]", "", x.lower())
        if (h and h in xl) or any(t in xl for t in toks):
            return x
    return handles[0] if len(handles) == 1 else None


def recon_one(store, lead, fetcher, now=None, force=False):
    last = (lead.get("recon") or {}).get("at")
    if last and not force:
        try:
            if (dt.datetime.now() - dt.datetime.fromisoformat(last)).days < RECON_FRESH_DAYS:
                return "fresh"
        except ValueError:
            pass
    sites = [full_url(c["url"]) for c in lead["candidates"] if c["kind"] == "website"]
    verified = (lead["contact"].get("website") or {}).get("value")
    # a listing's link is often a dead event page: try the site's front page first, the deep link after
    order = list(dict.fromkeys(([verified] if verified else []) + [origin_of(u) for u in sites] + sites))
    tried, got = [], []
    for url in [u for u in order if u][:3]:
        if not owns_site(lead, url):
            tried.append("%s: domain is not built from the business's name, not their site" % url)
            continue
        try:
            r = fetcher.get(url)
        except FetchRefused as e:
            tried.append("%s: refused (%s)" % (url, e))
            continue
        except Exception as e:  # noqa: BLE001
            tried.append("%s: error %s" % (url, type(e).__name__))
            continue
        if r["status"] != 200 or not r["body"]:
            tried.append("%s: HTTP %s" % (url, r["status"]))
            continue
        html, page = r["body"], url
        emails, phones, (ig, fb) = extract_emails(html), extract_phones(html), extract_socials(html)
        if not emails and not phones:
            cp = find_contact_page(html, url)
            if cp:
                try:
                    r2 = fetcher.get(cp)
                    if r2["status"] == 200 and r2["body"]:
                        emails, phones = extract_emails(r2["body"]), extract_phones(r2["body"])
                        ig2, fb2 = extract_socials(r2["body"])
                        ig, fb = ig or ig2, fb or fb2
                        page = cp
                except Exception:  # noqa: BLE001
                    pass
        set_fact(store, lead, "website", url, "domain is built from the business's name (%s)" % url, now, auto=True)
        got.append("website")
        if emails:
            set_fact(store, lead, "email", emails[0], page, now, auto=True)
            got.append("email")
        if phones:
            set_fact(store, lead, "phone", phones[0], page, now, auto=True)
            got.append("phone")
        igp, fbp = pick_social(ig, lead, url), pick_social(fb, lead, url)
        if igp:
            set_fact(store, lead, "instagram", "https://www.instagram.com/%s/" % igp, url, now, auto=True)
            got.append("instagram")
        if fbp:
            set_fact(store, lead, "facebook", "https://www.facebook.com/%s" % fbp, url, now, auto=True)
            got.append("facebook")
        break
    lead["recon"] = {"at": now_iso(now), "found": got, "tried": tried}
    store.event(lead["id"], "recon", lead["recon"], now)
    return "found" if got else "nothing"


def reset_recon(store, reason, now=None):
    """When the recon rules get stricter, set the old findings aside (kept under `superseded`, never deleted) and start clean.
    Facts a human typed in with `set` (their source is not a URL) are left alone."""
    n = 0
    for lead in store.leads.values():
        keep, moved = {}, {}
        for k, f in lead["contact"].items():
            src = str(f.get("source", ""))
            (moved if f.get("auto") or src.startswith("http") or src.startswith("the page itself names") else keep)[k] = f
        if moved or lead.get("recon"):
            lead.setdefault("superseded", []).append({"at": now_iso(now), "reason": reason, "contact": moved, "recon": lead.get("recon")})
            lead["contact"], lead["recon"] = keep, {}
            store.event(lead["id"], "recon_reset", {"reason": reason, "moved": sorted(moved)}, now)
            n += 1
    store.save()
    return n


def run_recon(store, fetcher, limit=None, only=None, include_public=False, now=None, force=False, retry_empty=False):
    todo = [store.by_ref(only)] if only else sorted(
        [l for l in store.leads.values() if any(c["kind"] == "website" for c in l["candidates"])
         and (include_public or l["sector"] == "private") and l["stage"] not in ("declined", "do_not_contact")
         and (not retry_empty or (l.get("recon") and not l["recon"].get("found")))],
        key=lambda l: -l.get("score", 0))
    stats = collections.Counter()
    for lead in todo[:limit]:
        stats[recon_one(store, lead, fetcher, now, force or retry_empty)] += 1
        lead["score"], lead["tier"], lead["why"] = score_lead(lead)
        store.save()
    store.save()
    return stats


# ---------- views ----------
def contact_str(l, kind):
    c = l["contact"].get(kind)
    return c["value"] if c else ""


def ranked(store, tiers=None, stage=None, include_public=False):
    rows = [l for l in store.leads.values() if (include_public or l["sector"] == "private")
            and (not tiers or l["tier"] in tiers) and (not stage or l["stage"] == stage)
            and l["stage"] not in ("declined", "do_not_contact")]
    rows.sort(key=lambda l: (-l["score"], l["name"].lower()))
    return rows


EXPORT_COLS = ["tier", "score", "name", "stage", "sector", "area", "address", "categories", "weekly_on_map", "dated_on_map",
               "past_events", "newsletter_issues", "website", "instagram", "facebook", "email", "phone", "why", "last_note"]


def export(store, out_dir=None):
    out_dir = out_dir or store.root
    os.makedirs(out_dir, exist_ok=True)
    allrows = sorted(store.leads.values(), key=lambda l: (l["sector"] == "public", -l["score"], l["name"].lower()))
    cpath, mpath = os.path.join(out_dir, "leads.csv"), os.path.join(out_dir, "leads.md")
    with open(cpath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(EXPORT_COLS)
        for l in allrows:
            w.writerow([l["tier"], l["score"], l["name"], l["stage"], l["sector"], l.get("area") or "", l.get("address") or "",
                        " | ".join(l["categories"]), l["on_map"]["weekly"], l["on_map"]["dated"], l["past_events"],
                        l["newsletter"]["issues"], contact_str(l, "website") or next((c["url"] for c in l["candidates"] if c["kind"] == "website"), ""),
                        contact_str(l, "instagram"), contact_str(l, "facebook"), contact_str(l, "email"), contact_str(l, "phone"),
                        "; ".join(l["why"]), (l["notes"][-1]["note"] if l["notes"] else "")])
    priv = [l for l in allrows if l["sector"] == "private"]
    tiers = collections.Counter(l["tier"] for l in priv)
    reach = {k: sum(1 for l in priv if l["contact"].get(k)) for k in CONTACT_KINDS}
    lines = ["# Board Bored: business leads", "",
             "%d businesses (%d private, %d public/nonprofit-sector). Tiers (private): A %d, B %d, C %d, D %d." %
             (len(allrows), len(priv), len(allrows) - len(priv), tiers["A"], tiers["B"], tiers["C"], tiers["D"]),
             "Verified from their own sites: website %d, email %d, phone %d, instagram %d, facebook %d." %
             (reach["website"], reach["email"], reach["phone"], reach["instagram"], reach["facebook"]),
             "Nothing here was sent to anyone. Full list: leads.csv. Contact facts only appear once read off the business's own page (or typed in with a source).", ""]
    for l in [x for x in priv if x["tier"] in ("A", "B") and x["stage"] not in ("declined", "do_not_contact")][:40]:
        lines += ["### %s [%s, score %d, stage %s]" % (l["name"], l["tier"], l["score"], l["stage"]),
                  "- why warm: " + "; ".join(l["why"]),
                  "- where: %s" % (l.get("address") or "address unknown"),
                  "- reach: " + (", ".join("%s %s" % (k, contact_str(l, k)) for k in CONTACT_KINDS if l["contact"].get(k)) or "not found yet")]
        if l["notes"]:
            lines.append("- last note: " + l["notes"][-1]["note"])
        lines.append("")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return cpath, mpath


def table(rows, top):
    out = []
    for l in rows[:top]:
        out.append("%s %3d  %-38s %-10s w%d d%d n%d  %s" % (l["tier"], l["score"], l["name"][:38], l["stage"], l["on_map"]["weekly"],
                   l["on_map"]["dated"], l["newsletter"]["issues"], contact_str(l, "email") or contact_str(l, "phone") or contact_str(l, "website") or "-"))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default=STORE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--board", default=BOARD)
    s.add_argument("--ledger", default=LEDGER)
    s.add_argument("--quiet", action="store_true", help="print only when new leads were added")
    r = sub.add_parser("recon")
    r.add_argument("--limit", type=int)
    r.add_argument("--id")
    r.add_argument("--force", action="store_true")
    r.add_argument("--retry-empty", action="store_true", help="only leads whose last recon found nothing")
    r.add_argument("--reset-all", metavar="REASON", help="set aside every recon-derived fact (kept under `superseded`) before running")
    r.add_argument("--include-public", action="store_true")
    m = sub.add_parser("mark")
    m.add_argument("name")
    m.add_argument("stage", choices=STAGES[1:])
    m.add_argument("--note", default="")
    n = sub.add_parser("note")
    n.add_argument("name")
    n.add_argument("text")
    st = sub.add_parser("set")
    st.add_argument("name")
    st.add_argument("kind", choices=CONTACT_KINDS)
    st.add_argument("value")
    st.add_argument("--source", required=True)
    li = sub.add_parser("list")
    li.add_argument("--tier")
    li.add_argument("--stage")
    li.add_argument("--all", action="store_true", help="include public-sector entries")
    li.add_argument("--top", type=int, default=25)
    ex = sub.add_parser("export")
    ex.add_argument("--copy-to")
    a = ap.parse_args(argv)
    store = Store(a.store)
    if a.cmd == "sync":
        added = sync_all(store, load_json(a.board, {}), load_json(a.ledger, {}), latest_outreach_csv())
        if added or not a.quiet:
            priv = ranked(store)
            print("leads: %d total (%+d new), %d private; tiers A/B/C/D = %s" % (
                len(store.leads), added, len(priv), "/".join(str(sum(1 for l in priv if l["tier"] == t)) for t in "ABCD")))
        return 0
    if a.cmd == "recon":
        if a.reset_all:
            print("reset:", reset_recon(store, a.reset_all))
        stats = run_recon(store, PoliteFetcher(CACHE, min_interval=3), a.limit, a.id, a.include_public, force=a.force, retry_empty=a.retry_empty)
        print("recon:", dict(stats))
        return 0
    if a.cmd == "mark":
        lead = store.by_ref(a.name)
        set_stage(store, lead, a.stage, a.note)
        store.save()
        print("%s -> %s" % (lead["name"], a.stage))
        return 0
    if a.cmd == "note":
        lead = store.by_ref(a.name)
        add_note(store, lead, a.text)
        store.save()
        return 0
    if a.cmd == "set":
        lead = store.by_ref(a.name)
        set_fact(store, lead, a.kind, a.value, a.source)
        lead["score"], lead["tier"], lead["why"] = score_lead(lead)
        store.save()
        print("%s: %s recorded with source" % (lead["name"], a.kind))
        return 0
    if a.cmd == "list":
        print(table(ranked(store, set(a.tier.upper().split(",")) if a.tier else None, a.stage, a.all), a.top))
        return 0
    if a.cmd == "export":
        c, md = export(store)
        print(c, md)
        if a.copy_to:
            print(*export(store, a.copy_to))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
