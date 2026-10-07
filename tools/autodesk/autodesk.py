#!/usr/bin/env python3
"""autodesk: the local truth desk for Board Bored events. Runs unattended (Hermes cron); nothing here needs Claude.

  run                 fetch every configured source, extract with deterministic + local-LLM voices, gate, tier, write proposals
  status              what the desk currently holds
  review              list tier-B events with the evidence the human needs
  approve ID.. | --tier-a    human sign-off (recorded); publish
  reject ID..         never propose this event again
  measure             score an LLM voice against the deterministic parse of the same page (false-accept rate)

Local models only EXTRACT. Whether a claim is true is decided by truthgate (deterministic) and cross-voice agreement.
Auto-publish is off until `autopublish` is true AND the desk has completed `min_clean_cycles` clean cycles.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters  # noqa: E402
from llm import OllamaVoice  # noqa: E402
from netfetch import FetchRefused, PoliteFetcher  # noqa: E402
from textutil import cover, html_to_text  # noqa: E402
from truthgate import consensus, event_id, gate  # noqa: E402

HOME = os.path.expanduser("~")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_CONFIG = os.path.join(HOME, ".boardbored", "autodesk", "config.json")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


class Desk:
    def __init__(self, cfg, now=None, fetcher=None, voice_factory=None):
        self.cfg = cfg
        self.dir = os.path.expanduser(cfg.get("state_dir", os.path.join(HOME, ".boardbored", "autodesk")))
        os.makedirs(self.dir, exist_ok=True)
        self.fetcher = fetcher or PoliteFetcher(os.path.join(self.dir, "cache"), min_interval=cfg.get("min_interval", 10))
        self.voice_factory = voice_factory or (lambda v: OllamaVoice(v["model"], v["family"], host=cfg.get("llm", {}).get("host", "http://localhost:11434"),
                                                                    cache_dir=os.path.join(self.dir, "cache")))
        self.now = now or dt.datetime.now()
        self.state = load_json(self.p("state.json"), {"clean_cycles": 0, "last_counts": {}, "rejected": [], "approved": []})
        self.prev = {e["id"]: e for e in load_json(self.p("proposed.json"), {}).get("events", [])}
        self.venues = dict(self.board_venues(cfg.get("venues_from_board")))
        self.venues.update(cfg.get("venues", {}))  # explicit config wins over what the board already has

    def p(self, name):
        return os.path.join(self.dir, name)

    def audit(self, **row):
        row = dict(ts=self.now.isoformat(timespec="seconds"), **row)
        with open(self.p("audit.jsonl"), "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def board_venues(path):
        """Venues already on the live board keep their verified address/coords (never re-guessed, never taken from a scraped page)."""
        out = {}
        if not path:
            return out
        b = load_json(os.path.expanduser(path), {})
        for e in b.get("city_events", []) + b.get("events", []):
            v = e.get("venue")
            if v and v not in out and e.get("address") and e.get("lat") is not None:
                out[v] = {"name": v, "address": e["address"], "lat": e["lat"], "lng": e["lng"], "area": e.get("area"),
                          "default_category": e.get("category", "Community")}
        return out

    # ---- venue canonicalisation -------------------------------------------------
    def canon_venue(self, name):
        for k, v in self.venues.items():
            if name and cover(name, k) >= 0.8 and cover(k, name) >= 0.8:
                return k, v
        return None, None

    # ---- one source -------------------------------------------------------------
    def collect(self, src, today):
        """-> (candidates, sources{id:text}, errors[], llm_pages[(source_id,url)])"""
        errors, cands, texts = [], [], {}

        def fetch(url):
            try:
                r = self.fetcher.get(url)
            except FetchRefused as e:
                errors.append("refused: %s" % e)
                return None
            if r["status"] != 200 or not r["body"]:
                errors.append("fetch %s -> %s %s" % (url, r["status"], r.get("error", "")))
                return None
            return r
        kind = src["kind"]
        if kind == "foxnile":
            cal = fetch(src["calendar_url"])
            if cal:
                cs, text = adapters.foxnile_cards(cal["body"], src["id"] + ":cal", today, src["calendar_url"])
                cands += cs
                texts[src["id"] + ":cal"] = text
            home = fetch(src["home_url"])
            if home:
                js, text = adapters.foxnile_jsonld(home["body"], src["id"] + ":home", today)
                cands += js
                texts[src["id"] + ":home"] = text
            llm_pages = [(src["id"] + ":cal", src["calendar_url"])] if cal else []
        elif kind == "newsletter":
            # numbered issues: process the last known issue and every newer one (404 ends the walk)
            n = int(self.state.setdefault("issues", {}).get(src["id"], src["start_issue"]))
            llm_pages, highest = [], None
            for k in range(n, n + src.get("max_new", 4) + 1):
                url = src["issue_url_template"] % k
                try:
                    r = self.fetcher.get(url)
                except FetchRefused as e:
                    errors.append("refused: %s" % e)
                    break
                if r["status"] == 404 and k > n:
                    break
                if r["status"] != 200 or not r["body"]:
                    if k == n:
                        errors.append("fetch %s -> %s" % (url, r["status"]))
                    break
                sid = "%s:issue-%d" % (src["id"], k)
                text = html_to_text(r["body"], url)
                texts[sid] = text
                bullets = adapters.newsletter_bullets(text, sid, today)
                cands += bullets
                if any(b["date"] >= today.isoformat() for b in bullets) or not bullets:
                    llm_pages.append((sid, url))  # an issue whose events are all in the past is not worth a model's time
                highest = k
            if highest is not None:
                self.state["issues"][src["id"]] = highest
        elif kind == "tribe":
            end = today + dt.timedelta(days=self.cfg.get("horizon_days", 150))
            api = fetch("%s?start_date=%s&end_date=%s&per_page=50" % (src["api_url"], today.isoformat(), end.isoformat()))
            llm_pages = []
            if api:
                cs, text = adapters.tribe_events(api["body"], src["id"] + ":api", today)
                cands += cs
                texts[src["id"] + ":api"] = text
        elif kind == "html_llm":
            page = fetch(src["url"])
            llm_pages = []
            if page:
                texts[src["id"] + ":page"] = html_to_text(page["body"], src["url"])
                llm_pages = [(src["id"] + ":page", src["url"])]
        else:
            return [], {}, ["unknown source kind " + kind], []
        if self.cfg.get("llm", {}).get("enabled"):
            for v in self.cfg["llm"]["voices"]:
                voice = self.voice_factory(v)
                for sid, url in llm_pages:
                    cands += voice.extract(texts[sid], today, sid, url)
                if hasattr(voice, "unload"):
                    voice.unload()
                if voice.stats.get("errors"):
                    errors.append("llm voice %s: %d chunk errors" % (v["model"], voice.stats["errors"]))
        return cands, texts, errors, llm_pages

    def judge(self, src, cands, texts, today):
        untrusted = {k: set(v) for k, v in src.get("untrusted", {}).items()}
        passed, drops = [], {}
        for c in cands:
            fam = c.get("family", "?")
            g, fails, stripped = gate(c, texts, today, default_venue=src.get("default_venue"), skip=tuple(untrusted.get(fam, ())))
            if fails:
                for f in fails:
                    drops[f] = drops.get(f, 0) + 1
                continue
            passed.append((g, stripped, fam))
        evs = consensus(passed, self.cfg.get("min_families", 2), untrusted)
        for ev in evs:
            vk, vinfo = self.canon_venue(ev["venue"])
            if vinfo is None and ev.get("address"):
                pass  # unlisted venue but the source itself states a street address: Node geocodes it; no pin guess made here
            elif vinfo is None:
                ev["tier"] = "B"
                ev["reasons"].append("unknown_venue")
            else:
                ev["venue"], ev["address"] = vinfo.get("name", vk), vinfo.get("address")
                ev["lat"], ev["lng"], ev["area"] = vinfo.get("lat"), vinfo.get("lng"), vinfo.get("area")
                ev["category"] = self.category(ev["name"], vinfo)
            ev["id"] = event_id(ev)
            ev["source"] = src["id"]
        return evs, drops

    def category(self, name, vinfo):
        for pat, cat in self.cfg.get("categories", []):
            import re
            if re.search(pat, name, re.I):
                return cat
        return vinfo.get("default_category", "Community")

    # ---- run --------------------------------------------------------------------
    def run(self, today=None):
        today = today or self.now.date()
        all_ev, errors, drops_total, trips = [], [], {}, []
        counts = {}
        for src in self.cfg["sources"]:
            cands, texts, errs, _pages = self.collect(src, today)
            errors += ["%s: %s" % (src["id"], e) for e in errs]
            evs, drops = self.judge(src, cands, texts, today)
            for k, v in drops.items():
                drops_total[k] = drops_total.get(k, 0) + v
            counts[src["id"]] = len(evs)
            prev_n = self.state["last_counts"].get(src["id"], 0)
            if prev_n >= 5 and (len(evs) < prev_n * 0.5 or len(evs) > prev_n * 3):
                trips.append("%s: %d events vs %d last run" % (src["id"], len(evs), prev_n))
            elif prev_n > 0 and len(evs) == 0:
                trips.append("%s: 0 events (had %d)" % (src["id"], prev_n))
            all_ev += evs
        rejected = set(self.state.get("rejected", []))
        approved = set(self.state.get("approved", []))
        all_ev = [e for e in all_ev if e["id"] not in rejected]
        for e in all_ev:
            if e["id"] in approved and not e["conflicts"]:
                e["tier"] = "A"
                e["reasons"].append("human_approved")
        all_ev.sort(key=lambda e: (e["date"], e["name"]))
        new = [e for e in all_ev if e["id"] not in self.prev]
        changed = [e for e in all_ev if e["id"] in self.prev and self.prev[e["id"]]["tier"] != e["tier"]]
        gone = [i for i in self.prev if i not in {e["id"] for e in all_ev}
                and self.prev[i]["date"] >= today.isoformat()]
        for e in new + changed:
            self.audit(kind="decision", id=e["id"], tier=e["tier"], name=e["name"], date=e["date"], reasons=e["reasons"],
                       families=e["families"], quote_sha=hashlib.sha256((e["evidence"][0]["quote"] or "").encode()).hexdigest()[:12])
        for i in gone:
            self.audit(kind="disappeared", id=i, name=self.prev[i]["name"], date=self.prev[i]["date"])
        conflicts = [e for e in all_ev if e["conflicts"]]
        tripped = bool(trips) or bool(errors)
        clean = not tripped and not conflicts
        self.state["clean_cycles"] = self.state["clean_cycles"] + 1 if clean else 0
        if not tripped:
            self.state["last_counts"].update(counts)
        publish_ok = (self.cfg.get("autopublish") and self.state["clean_cycles"] >= self.cfg.get("min_clean_cycles", 2) and not tripped)
        save_json(self.p("proposed.json"), {"asof": today.isoformat(), "events": all_ev})
        save_json(self.p("state.json"), self.state)
        published = 0
        if publish_ok:
            published = self.publish([e for e in all_ev if e["tier"] == "A"])
        self.audit(kind="run", counts=counts, drops=drops_total, tripped=trips, errors=errors, clean_cycles=self.state["clean_cycles"],
                   autopublished=published)
        return {"events": all_ev, "new": new, "changed": changed, "gone": gone, "trips": trips, "errors": errors,
                "clean_cycles": self.state["clean_cycles"], "published": published, "drops": drops_total,
                "publish_armed": bool(self.cfg.get("autopublish"))}

    # ---- publish ----------------------------------------------------------------
    def publish(self, events):
        out_path = os.path.expanduser(self.cfg.get("publish_path", os.path.join(REPO, "data", "autodesk_events.json")))
        raw = []
        horizon = (self.now.date() + dt.timedelta(days=self.cfg.get("horizon_days", 150))).isoformat()
        for e in events:
            if not (e.get("address") or e.get("lat")):
                continue  # no verified location -> never publish a pinless guess
            if e.get("lat") is None and "Bakersfield" not in (e.get("address") or ""):
                e = dict(e, address=e["address"] + ", Bakersfield, CA")  # newsletter addresses omit the city; Nominatim needs it
            if e["date"] > horizon:
                continue  # stays in proposed.json; the map shows the next ~5 months, not 2027
            d = dt.date.fromisoformat(e["date"])
            when = "%s %s %d" % (DAYS[d.weekday()], d.strftime("%b"), d.day)
            if e.get("start"):
                h, m = map(int, e["start"].split(":"))
                when += " · %d%s%s" % ((h % 12) or 12, (":%02d" % m) if m else "", "pm" if h >= 12 else "am")
            raw.append({"name": e["name"], "venue": e["venue"], "address": e.get("address"), "lat": e.get("lat"), "lng": e.get("lng"),
                        "area": e.get("area"), "category": e.get("category", "Community"), "date": e["date"], "start": e.get("start"),
                        "end": e.get("end"), "when": when, "cost": e.get("cost"), "link": e.get("link"),
                        "source": "autodesk:" + e["source"], "autodesk_id": e["id"], "verified_by": e["families"]})
        save_json(out_path, raw)
        self.audit(kind="publish", path=out_path, n=len(raw), ids=[r["autodesk_id"] for r in raw])
        return len(raw)

    def approve(self, ids=None, tier_a=False):
        evs = load_json(self.p("proposed.json"), {}).get("events", [])
        pick = [e for e in evs if (tier_a and e["tier"] == "A") or (ids and e["id"] in ids)]
        for e in pick:
            if e["id"] not in self.state["approved"] and not tier_a:
                self.state["approved"].append(e["id"])
            self.audit(kind="human_approve", id=e["id"], name=e["name"], date=e["date"])
        save_json(self.p("state.json"), self.state)
        return self.publish(pick), len(pick)

    def reject(self, ids):
        self.state["rejected"] = sorted(set(self.state["rejected"]) | set(ids))
        for i in ids:
            self.audit(kind="human_reject", id=i)
        save_json(self.p("state.json"), self.state)


def summary(res, today):
    ev = res["events"]
    a = [e for e in ev if e["tier"] == "A"]
    b = [e for e in ev if e["tier"] == "B"]
    lines = []
    if res["trips"] or res["errors"]:
        lines.append("DESK PAUSED (nothing published): " + "; ".join(res["trips"] + res["errors"])[:300])
    lines.append("Events desk %s: %d ready (A), %d need a look (B), %d dropped by gate%s" % (
        today.isoformat(), len(a), len(b), sum(res["drops"].values()),
        "" if res["publish_armed"] else " | propose-only (autopublish off)"))
    if res["new"]:
        lines.append("New: " + ", ".join("%s %s" % (e["date"][5:], e["name"][:34]) for e in res["new"][:5]) + ("..." if len(res["new"]) > 5 else ""))
    if b:
        lines.append("Review: autodesk.py review")
    if a and not res["published"]:
        lines.append("Approve all ready: autodesk.py approve --tier-a")
    return "\n".join(lines)


def should_notify(res):
    return bool(res["new"] or res["changed"] or res["gone"] or res["trips"] or res["errors"])


def cmd_measure(desk, args, today):
    """Score an LLM voice against the deterministic (structured) parse of the same page(s). The truth is what the page
    itself says, parsed without a model. Reports the number that matters: false accepts through the gate."""
    import time
    src = next((s for s in desk.cfg["sources"] if s["id"] == args.source), desk.cfg["sources"][0])
    desk.cfg.setdefault("llm", {})["enabled"] = False
    cands, texts, errs, pages = desk.collect(src, today)
    if errs:
        print("collect errors:", errs)
    struct = [c for c in cands if str(c.get("family", "")).startswith("struct:") and c["family"] != "struct:jsonld"]
    truth = []
    for c in struct:
        g, fails, _ = gate(c, texts, today, default_venue=src.get("default_venue"))
        if not fails:
            truth.append(g)
    voice = desk.voice_factory({"model": args.model, "family": "llm:" + args.model})
    t0 = time.time()
    lc = []
    for sid, url in pages:
        lc += voice.extract(texts[sid], today, sid, url)
    secs = time.time() - t0
    voice.unload()
    accepted, rejected_by = [], {}
    for c in lc:
        g, fails, st = gate(c, texts, today, default_venue=src.get("default_venue"))
        if fails:
            for f in fails:
                rejected_by[f] = rejected_by.get(f, 0) + 1
        else:
            accepted.append((g, st))

    def match(a, b):
        return a["date"] == b["date"] and cover(a["name"], b["name"]) >= 0.75 and cover(b["name"], a["name"]) >= 0.75
    true_acc = [(a, st) for a, st in accepted if any(match(a, t) for t in truth)]
    unmatched = [a for a, st in accepted if not any(match(a, t) for t in truth)]
    found = [t for t in truth if any(match(a, t) for a, _ in accepted)]
    field_diffs = []
    for a, _ in true_acc:
        for t in truth:
            if match(a, t):
                if a.get("start") and t.get("start") and a["start"] != t["start"]:
                    field_diffs.append((a["name"], "start", a["start"], t["start"]))
                if a.get("venue") and t.get("venue") and cover(t["venue"], a["venue"]) < 0.75:
                    field_diffs.append((a["name"], "venue", a["venue"], t["venue"]))
    rep = {"model": args.model, "source": src["id"], "seconds": round(secs, 1), "truth_events": len(truth), "raw_candidates": len(lc),
           "gate_rejected": rejected_by, "accepted": len(accepted), "matched_truth": len(true_acc), "unmatched_accepts": len(unmatched),
           "field_disagreements_with_truth": len(field_diffs), "recall": round(len(found) / max(1, len(truth)), 3),
           "agreement_rate_after_gate": round(len(true_acc) / max(1, len(accepted)), 3),
           "unmatched_examples": [(a["name"], a["date"], a.get("venue"), (a.get("quote") or "")[:140]) for a in unmatched[:8]],
           "field_diff_examples": field_diffs[:6]}
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    desk.audit(kind="measure", **{k: v for k, v in rep.items() if not k.endswith("examples")})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["run", "status", "review", "approve", "reject", "measure"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--today", default=None)
    ap.add_argument("--tier-a", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--source", default=None, help="source id for measure")
    ap.add_argument("--quiet-if-unchanged", action="store_true", help="print nothing unless something changed (Hermes silent mode)")
    a = ap.parse_args(argv)
    cfg = load_json(a.config, None)
    if cfg is None:
        print("no config at %s (copy config.example.json there)" % a.config)
        return 2
    if a.no_llm:
        cfg.setdefault("llm", {})["enabled"] = False
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    desk = Desk(cfg, now=dt.datetime.combine(today, dt.datetime.now().time()))
    if a.cmd == "run":
        res = desk.run(today)
        if not a.quiet_if_unchanged or should_notify(res):
            print(summary(res, today))
        return 1 if res["trips"] or res["errors"] else 0
    if a.cmd == "status":
        evs = load_json(desk.p("proposed.json"), {}).get("events", [])
        print("A=%d B=%d clean_cycles=%d autopublish=%s" % (sum(e["tier"] == "A" for e in evs), sum(e["tier"] == "B" for e in evs),
                                                          desk.state["clean_cycles"], cfg.get("autopublish")))
        return 0
    if a.cmd == "review":
        for e in load_json(desk.p("proposed.json"), {}).get("events", []):
            if e["tier"] == "B":
                print("%s  %s  %s @ %s  [%s]\n    \"%s\"" % (e["id"], e["date"], e["name"], e["venue"], ", ".join(e["reasons"]),
                                                          (e["evidence"][0]["quote"] or "")[:200]))
        return 0
    if a.cmd == "approve":
        n, picked = desk.approve(a.ids, a.tier_a)
        print("approved %d, wrote %d publishable events" % (picked, n))
        return 0
    if a.cmd == "reject":
        desk.reject(a.ids)
        print("rejected %d" % len(a.ids))
        return 0
    if a.cmd == "measure":
        return cmd_measure(desk, a, today)


if __name__ == "__main__":
    sys.exit(main())
