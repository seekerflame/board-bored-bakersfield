#!/usr/bin/env python3
"""outreach_desk: turn the public Bakersfield newsletter listings into a ranked, tracked outreach list.

Reads only public pages (polite fetch, robots honored). For every venue/host featured in recent issues it records how
often they appear, what they list, their address, and the best contact link the newsletter itself printed. It drafts a
plain, truthful first message (every name/number verified against the fact sheet) and tracks who has been contacted in
an append-only log. It NEVER sends anything: a human sends, then runs `mark`.

  python3 outreach_desk.py build [--issues 58-63]     # writes ~/.boardbored/reports/outreach_<date>.{md,csv}
  python3 outreach_desk.py mark "<venue>" contacted|replied|joined|declined [--note ...]
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters  # noqa: E402
from netfetch import PoliteFetcher  # noqa: E402
from revenue_desk import venue_key, verify_draft  # noqa: E402
from textutil import cover, html_to_text  # noqa: E402

HOME = os.path.expanduser("~")
BOARD = os.path.join(HOME, "bbk-live", "data", "board.json")
OUT = os.path.join(HOME, ".boardbored", "reports")
LOG = os.path.join(HOME, ".boardbored", "outreach", "log.jsonl")
ISSUE_URL = "https://newsletter.thebakersfieldguy.com/p/the-bakersfield-guy-s-newsletter-issue-%d"
JOIN_URL = "https://seekerflame.github.io/board-bored-bakersfield/first-friday/submit-event.html?ref=outreach"
TICKETING = ("eventbrite.", "simpletix.", "tixr.", "ticketon.", "axs.", "ticketmaster.", "regmovies.", "dignityhealtharena.", "qgiv.", "givebutter.", "eventcreate.", "universe.com")
PUBLIC_WORDS = ("library", "county", "city of", "college", "university", "csub", "school", "district", "park")
STATUSES = ("contacted", "replied", "joined", "declined")


def link_kind(url):
    h, p = urlparse(url).netloc.lower(), urlparse(url).path
    if ("google." in h and p.startswith("/search")) or "bing.com" in h or "duckduckgo.com" in h:
        return "search"
    if "instagram.com" in h:
        return "instagram_post" if re.match(r"^/(p|reel)/", p) else "instagram_profile"
    if "facebook.com" in h:
        return "facebook_event" if "/events/" in p else ("facebook_page" if p.strip("/") and "profile.php" not in p else "facebook_profile")
    if any(t in h for t in TICKETING):
        return "ticketing"
    return "website"


def best_contact(links):
    order = ["website", "instagram_profile", "facebook_page", "facebook_profile", "instagram_post", "facebook_event", "ticketing"]
    links = sorted({u for u in links if link_kind(u) != "search"}, key=lambda u: order.index(link_kind(u)) if link_kind(u) in order else 99)
    return (links[0], link_kind(links[0])) if links else ("", "none")


def parse_issue(text):
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{1,2}), (\d{4})", text)
    issue_day = dt.datetime.strptime("%s %s %s" % (m.group(1)[:3], m.group(2), m.group(3)), "%b %d %Y").date() if m else dt.date.today()
    return issue_day, adapters.newsletter_bullets(text, "issue", issue_day)


def aggregate(issue_texts, board_venues):
    agg = collections.defaultdict(lambda: {"names": collections.Counter(), "events": collections.Counter(), "issues": set(), "links": [],
                                           "address": None, "last": ""})
    for n, text in issue_texts.items():
        day, bullets = parse_issue(text)
        for b in bullets:
            a = agg[venue_key(b["venue"])]
            a["names"][b["venue"]] += 1
            a["events"][b["name"]] += 1
            a["issues"].add(n)
            if b.get("link"):
                a["links"].append(b["link"])
            a["address"] = a["address"] or b.get("address")
            a["last"] = max(a["last"], b["date"])
    rows = []
    for key, a in agg.items():
        name = a["names"].most_common(1)[0][0]
        contact, kind = best_contact(a["links"])
        rows.append({"venue": name, "key": key, "appearances": sum(a["events"].values()), "issues": len(a["issues"]),
                     "events": [e for e, _ in a["events"].most_common(3)], "address": a["address"], "last_seen": a["last"],
                     "contact": contact, "contact_kind": kind,
                     "on_map": any(cover(name, v) >= 0.8 and cover(v, name) >= 0.8 for v in board_venues),
                     "sector": "public" if any(w in name.lower() for w in PUBLIC_WORDS) else "private"})
    rows.sort(key=lambda r: (-(r["issues"] * 3 + r["appearances"]), r["venue"]))
    return rows


def draft(row):
    sheet = {"name": row["venue"], "weekly_slots": 0, "dated_events": 0, "address": row["address"] or "", "listings": row["events"] + ["The Bakersfield Guy's"]}
    evs = ", ".join(row["events"][:2])
    if row["on_map"]:
        text = ("Hi %s team, it's Nick from Board Bored, a free Bakersfield events map. Your events like %s are already on it. "
                "If anything is out of date, send us the fix here: %s" % (row["venue"], evs, JOIN_URL))
    else:
        text = ("Hi %s team, it's Nick from Board Bored, a free Bakersfield events map. I saw %s in The Bakersfield Guy's newsletter. "
                "Want your events on the map? It is free and takes a minute: %s" % (row["venue"], evs, JOIN_URL))
    sheet["listings"] += [JOIN_URL.split("/")[2]]
    bad = [b for b in verify_draft(text, sheet) if "seekerflame" not in b and "github" not in b.lower()]
    return text, bad


def load_log():
    state = {}
    try:
        for ln in open(LOG):
            r = json.loads(ln)
            state[r["key"]] = r
    except Exception:
        pass
    return state


def build(issues, fetcher, board_path=BOARD, out_dir=OUT, today=None):
    today = today or dt.date.today()
    texts = {}
    for n in issues:
        r = fetcher.get(ISSUE_URL % n)
        if r["status"] == 200:
            texts[n] = html_to_text(r["body"], ISSUE_URL % n)
    board = json.load(open(board_path))
    bv = {e.get("venue") for e in board.get("city_events", []) if e.get("venue")}
    rows = aggregate(texts, bv)
    log = load_log()
    for r in rows:
        r["status"] = log.get(r["key"], {}).get("status", "not_contacted")
        r["draft"], r["draft_problems"] = draft(r)
        if r["draft_problems"]:
            r["draft"] = ""  # never offer a message whose names/numbers don't all trace to the fact sheet
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "outreach_%s" % today.isoformat())
    with open(base + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["venue", "status", "on_map", "sector", "issues_featured", "appearances", "last_seen", "address", "contact_kind", "contact", "example_events", "draft"])
        for r in rows:
            w.writerow([r["venue"], r["status"], r["on_map"], r["sector"], r["issues"], r["appearances"], r["last_seen"], r["address"] or "",
                        r["contact_kind"], r["contact"], " | ".join(r["events"]), r["draft"]])
    todo = [r for r in rows if r["status"] == "not_contacted" and not r["on_map"]]
    lines = ["# Outreach list %s" % today.isoformat(), "",
             "%d venues/hosts across issues %s. %d already on the map, %d not yet contacted and not on the map." %
             (len(rows), ",".join(map(str, sorted(texts))), sum(r["on_map"] for r in rows), len(todo)),
             "Nothing is sent by this tool. Send, then `outreach_desk.py mark \"<venue>\" contacted`.", ""]
    for r in todo[:40]:
        lines += ["### %s  (%d issues, %d listings, last %s)" % (r["venue"], r["issues"], r["appearances"], r["last_seen"]),
                  "- contact (%s): %s" % (r["contact_kind"], r["contact"] or "none printed: look up their Instagram"),
                  "- address: %s" % (r["address"] or "unknown"),
                  "- " + (r["draft"] or "(draft withheld: venue text looks like parse noise, check by hand: %s)" % "; ".join(r["draft_problems"][:2])), ""]
    open(base + ".md", "w").write("\n".join(lines) + "\n")
    return rows, base


def mark(venue, status, note="", now=None):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    row = {"ts": (now or dt.datetime.now()).isoformat(timespec="seconds"), "key": venue_key(venue), "venue": venue, "status": status, "note": note}
    with open(LOG, "a") as f:  # append-only
        f.write(json.dumps(row) + "\n")
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--issues", default="58-63")
    m = sub.add_parser("mark")
    m.add_argument("venue")
    m.add_argument("status", choices=STATUSES)
    m.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.cmd == "mark":
        print(json.dumps(mark(a.venue, a.status, a.note)))
        return 0
    lo, hi = (int(x) for x in a.issues.split("-"))
    rows, base = build(range(lo, hi + 1), PoliteFetcher(os.path.join(HOME, ".boardbored", "autodesk", "cache"), min_interval=3))
    print("%d venues; %d on map; report: %s.md  csv: %s.csv" % (len(rows), sum(r["on_map"] for r in rows), base, base))
    print("drafts with problems:", sum(1 for r in rows if r["draft_problems"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
