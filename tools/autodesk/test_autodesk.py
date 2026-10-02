"""Offline tests: no network, no Ollama. Synthetic fixtures use obviously fake venue names."""
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters  # noqa: E402
import autodesk  # noqa: E402
import llm  # noqa: E402
import netfetch  # noqa: E402
import textutil as tu  # noqa: E402
import truthgate as tg  # noqa: E402

TODAY = dt.date(2026, 10, 2)


# ---------------------------------------------------------------- textutil
class TextUtil(unittest.TestCase):
    def test_dates(self):
        ds = [t.date for t in tu.find_dates("Fri, Oct 9 and Saturday October 10th, 2026 and 10/17", TODAY)]
        self.assertEqual(ds, [dt.date(2026, 10, 9), dt.date(2026, 10, 10), dt.date(2026, 10, 17)])

    def test_weekday_mismatch_rejected(self):
        self.assertEqual(tu.find_dates("Fri, Oct 10", TODAY), [])  # Oct 10 2026 is a Saturday

    def test_year_rollover(self):
        self.assertEqual(tu.find_dates("Jan 5", TODAY)[0].date, dt.date(2027, 1, 5))
        self.assertEqual(tu.find_dates("Sep 25", TODAY)[0].date, dt.date(2026, 9, 25))  # within 60-day tolerance

    def test_times(self):
        t = {m for m, _, _ in tu.find_times("Doors 6pm, show 7:30 PM, 11-2PM, noon, 19:00")}
        self.assertTrue({18 * 60, 19 * 60 + 30, 11 * 60, 14 * 60, 720, 19 * 60} <= t)

    def test_locate_quote_tolerant_and_contiguous(self):
        src = "Intro\nTest   Hall  presents\nFRIDAY, Oct 9 -- Open Mic!\nmore"
        self.assertIsNotNone(tu.locate_quote(src, "test hall presents friday oct 9 open mic"))
        self.assertIsNone(tu.locate_quote(src, "Test Hall presents Open Mic Friday Oct 9"))  # reordered = not a span
        self.assertIsNone(tu.locate_quote(src, "short"))

    def test_html_to_text_skips_script_keeps_links(self):
        t = tu.html_to_text('<script>var x=1</script><p>Hello <a href="/e/1">tix</a></p>', "https://ex.test")
        self.assertIn("Hello", t)
        self.assertIn("[https://ex.test/e/1]", t)
        self.assertNotIn("var x", t)


# ---------------------------------------------------------------- gate
SRC = ("TEST HALL\nFriday, October 9, 2026\nOpen Mic Night with Fake Band 7:30pm free [https://ex.test/e/1]\n"
       "OTHER PLACE\nSaturday, October 10, 2026\nPolka Fest 9pm $15\n")


def cand(**kw):
    c = {"name": "Open Mic Night with Fake Band", "venue": "Test Hall", "date": "2026-10-09", "start": "19:30", "end": None,
         "cost": "free", "link": "https://ex.test/e/1", "family": "t", "source_id": "s",
         "quote": "TEST HALL Friday, October 9, 2026 Open Mic Night with Fake Band 7:30pm free [https://ex.test/e/1]"}
    c.update(kw)
    return c


def g(c, src=SRC, **kw):
    return tg.gate(c, {"s": src}, TODAY, **kw)


class Gate(unittest.TestCase):
    def test_grounded_passes_clean(self):
        c, fails, stripped = g(cand())
        self.assertEqual((fails, stripped), ([], []))

    def test_fabricated_quote_fails(self):
        self.assertIn("quote_not_in_source", g(cand(quote="TEST HALL Friday, October 9, 2026 Free Pony Rides for everyone"))[1])

    def test_wrong_date_fails(self):
        self.assertIn("date_not_in_quote", g(cand(date="2026-10-16"))[1])

    def test_wrong_venue_fails(self):
        c = cand(venue="Other Place", quote="Open Mic Night with Fake Band 7:30pm free")
        self.assertIn("venue_not_in_quote", g(c)[1])

    def test_unsupported_time_stripped_not_fatal(self):
        c, fails, stripped = g(cand(start="21:00"))
        self.assertEqual(fails, [])
        self.assertIn("start", stripped)
        self.assertIsNone(c["start"])

    def test_unsupported_cost_and_link_stripped(self):
        c, fails, stripped = g(cand(cost="$40", link="https://evil.test/x"))
        self.assertEqual(fails, [])
        self.assertEqual(sorted(stripped), ["cost", "link"])

    def test_past_and_far_future(self):
        src = "TEST HALL Friday, October 9, 2026 Open Mic Night with Fake Band"
        self.assertIn("past", tg.gate(cand(date="2026-09-25", quote="TEST HALL Friday, September 25, 2026 Open Mic Night with Fake Band"),
                                      {"s": "TEST HALL Friday, September 25, 2026 Open Mic Night with Fake Band"}, TODAY)[1])
        self.assertIn("date_too_far", tg.gate(cand(date="2029-01-05", quote="TEST HALL January 5, 2029 Open Mic Night with Fake Band"),
                                              {"s": "TEST HALL January 5, 2029 Open Mic Night with Fake Band"}, TODAY)[1])

    def test_not_an_event(self):
        src = "TEST HALL October 9, 2026 Season Pass 2026"
        self.assertIn("not_an_event", tg.gate(cand(name="Season Pass 2026", quote="TEST HALL October 9, 2026 Season Pass 2026"), {"s": src}, TODAY)[1])

    def test_heading_governs_date(self):
        src = "Friday, October 9, 2026\nTEST HALL\nOpen Mic Night with Fake Band 7:30pm"
        c = cand(quote="TEST HALL Open Mic Night with Fake Band 7:30pm", heading="Friday, October 9, 2026", link=None)
        self.assertEqual(g(c, src)[1], [])
        c2 = cand(quote="TEST HALL Open Mic Night with Fake Band 7:30pm", heading="Friday, October 16, 2026", link=None, date="2026-10-09")
        self.assertIn("date_not_in_quote", g(c2, src)[1])

    def test_default_venue_for_single_venue_source(self):
        c = cand(quote="Friday, October 9, 2026 Open Mic Night with Fake Band 7:30pm", venue="Test Hall", link=None)
        self.assertIn("venue_not_in_quote", g(c, "Friday, October 9, 2026 Open Mic Night with Fake Band 7:30pm")[1])
        self.assertEqual(g(c, "Friday, October 9, 2026 Open Mic Night with Fake Band 7:30pm", default_venue="Test Hall")[1], [])

    def test_paraphrased_quote_is_repaired_to_the_real_line(self):
        src = "FRIDAY, OCTOBER 9\n\n\u2022 Open Mic Night with Fake Band @ Test Hall [https://ex.test/e/1] (7:30PM) 1 Fake St. Free comedy.\n\n\u2022 Polka Fest @ Other Place (9PM)\n"
        src = "SATURDAY, OCTOBER 3\nFRIDAY, OCTOBER 9\nSUNDAY, OCTOBER 11\n" + src
        c = cand(date="2026-10-09", link=None, cost=None, start="19:30",
                 quote="Open Mic Night with Fake Band @ Test Hall (07:30 PM) Free comedy night")
        g2, fails, st = tg.gate(c, {"s": src}, TODAY)
        self.assertEqual(fails, [])
        self.assertTrue(g2.get("quote_repaired"))
        self.assertIn("1 Fake St", g2["quote"], "evidence is the page's own text, not the model's")

    def test_fuzzy_repair_cannot_launder_a_hallucinated_event(self):
        src = "FRIDAY, OCTOBER 9\n\u2022 Open Mic Night with Fake Band @ Test Hall (7:30PM) 1 Fake St.\n" + "SATURDAY, OCTOBER 10\nSUNDAY, OCTOBER 11\n"
        # model invents a different venue/name but borrows most words of a real line
        c = cand(name="Open Mic Night with Fake Band", venue="Imaginary Arena", date="2026-10-09", link=None, cost=None, start="19:30",
                 quote="Open Mic Night with Fake Band @ Imaginary Arena (7:30PM) 1 Fake St.")
        g1, f1, _ = tg.gate(c, {"s": src}, TODAY)
        self.assertNotEqual(f1, [], "a quote that swaps the venue must not ground")
        self.assertFalse(g1.get("quote_repaired"))
        c2 = cand(name="Totally Different Show", venue="Test Hall", date="2026-10-09", link=None, cost=None, start="19:30",
                  quote="Totally Different Show @ Test Hall (7:30PM) 1 Fake St.")
        self.assertTrue(any(f in ("name_not_in_quote", "quote_not_in_source") for f in tg.gate(c2, {"s": src}, TODAY)[1]))

    def test_fuzzy_never_spans_a_whole_multiline_card(self):
        card = "October 9, 2026\nOct\nFake Band Live\nDOOR TIME:\n6:00 pm\nSHOW STARTS:\n8:00 pm\nGET TICKETS [https://ex.test/e/1]\nMock Theater\n"
        c = cand(name="Fake Band Live", venue="Mock Theater", date="2026-10-09", start=None, cost=None, link=None,
                 quote="Fake Band Live at Mock Theater October 9 2026 doors 6 pm show 8 pm")
        self.assertNotEqual(tg.gate(c, {"s": card}, TODAY)[1], [], "paraphrase of a 9-line card must not ground")

    def test_next_sections_heading_swept_into_a_window_cannot_change_the_date(self):
        """Regression (measured, phi4-mini on a real issue): a bullet at the end of SATURDAY was accepted as SUNDAY because the
        quote window bled across a blank line into the SUNDAY heading."""
        src = ("FRIDAY, OCTOBER 9\n\u2022 Early Act @ Test Hall (6PM) 1 Fake St.\n\n"
               "SATURDAY, OCTOBER 10\n\n\u2022 Late Act @ Test Hall (9PM) 1 Fake St. Description here.\n\n"
               "SUNDAY, OCTOBER 11\n\n\u2022 Sunday Act @ Test Hall (2PM)\n")
        c = cand(name="Late Act", venue="Test Hall", date="2026-10-11", link=None, cost=None, start="21:00",
                 quote="Late Act @ Test Hall (9PM) 1 Fake St. Description here. SUNDAY, OCTOBER 11")
        self.assertIn("date_not_in_quote", tg.gate(c, {"s": src}, TODAY)[1])
        ok = dict(c, date="2026-10-10", quote="\u2022 Late Act @ Test Hall (9PM) 1 Fake St. Description here.")
        self.assertEqual(tg.gate(ok, {"s": src}, TODAY)[1], [])

    def test_quote_spanning_two_bullets_is_ambiguous(self):
        src = ("FRIDAY, OCTOBER 9\nSATURDAY, OCTOBER 10\nSUNDAY, OCTOBER 11\n"
               "\u2022 Act One @ Test Hall (6PM)\n\u2022 Act Two @ Other Place (9PM)\n")
        c = cand(name="Act One", venue="Other Place", date="2026-10-11", link=None, cost=None, start="21:00",
                 quote="\u2022 Act One @ Test Hall (6PM) \u2022 Act Two @ Other Place (9PM)")
        self.assertIn("quote_spans_records", tg.gate(c, {"s": src}, TODAY)[1])

    def test_fuzzy_prefers_the_tightest_window(self):
        src = "SATURDAY, OCTOBER 10\n\n\u2022 Late Act @ Test Hall (9PM) 1 Fake St.\n\nSUNDAY, OCTOBER 11\n\n\u2022 Next @ X (1PM)\n"
        fz = tu.locate_quote_fuzzy(src, "Late Act @ Test Hall 9 PM 1 Fake St. extra")
        self.assertIsNotNone(fz)
        self.assertNotIn("SUNDAY", fz[2])

    def test_html_entities_in_raw_quote(self):
        src = '"name": "Cults &amp; Classics: Test Film", "startDate": "Oct 12, 2026"'
        c = cand(name="Cults &amp; Classics: Test Film", venue=None, start=None, cost=None, link=None, date="2026-10-12",
                 quote=src)
        self.assertEqual(g(c, src, skip=("venue",))[1], [])


# ---------------------------------------------------------------- consensus
def P(family, **kw):
    c = cand(family=family, **kw)
    return (c, [], family)


class Consensus(unittest.TestCase):
    def test_two_families_agree_is_A(self):
        ev = tg.consensus([P("a"), P("b")])
        self.assertEqual((len(ev), ev[0]["tier"]), (1, "A"))

    def test_single_voice_is_B(self):
        ev = tg.consensus([P("a")])
        self.assertEqual((ev[0]["tier"], ev[0]["reasons"]), ("B", ["single_voice"]))

    def test_date_conflict_blocks_A(self):
        ev = tg.consensus([P("a"), P("b", date="2026-10-16")])
        self.assertEqual(len(ev), 2)  # different dates are different events, each single-voice
        self.assertTrue(all(e["tier"] == "B" for e in ev))

    def test_time_conflict_is_flagged(self):
        ev = tg.consensus([P("a"), P("b", start="21:00")])
        self.assertEqual((ev[0]["tier"], ev[0]["conflicts"][0]["field"]), ("B", "start"))

    def test_untrusted_family_cannot_vouch_venue(self):
        wrong = P("j", venue="Wrong Place")
        ev = tg.consensus([P("a"), wrong], untrusted={"j": {"venue"}})
        self.assertEqual((ev[0]["tier"], ev[0]["venue"], ev[0]["conflicts"]), ("A", "Test Hall", []))

    def test_same_title_same_day_different_venue_are_two_events_never_merged(self):
        ev = tg.consensus([P("a"), P("b", venue="Wrong Place")])
        self.assertEqual(len(ev), 2)
        self.assertTrue(all(e["tier"] == "B" and e["reasons"] == ["single_voice"] for e in ev))

    def test_shared_link_is_not_identity(self):
        a = P("a", name="Mom Walk", link="https://fair.test/")
        b = P("a", name="Blooming Mamas Fair Day", link="https://fair.test/", venue="The Great Fair")
        self.assertEqual(len(tg.consensus([a, b])), 2)

    def test_good_event_survives_a_lying_voice(self):
        ev = tg.consensus([P("a"), P("b"), P("liar", venue="Wrong Place")])
        good = [e for e in ev if e["venue"] == "Test Hall"][0]
        self.assertEqual((good["tier"], good["families"]), ("A", ["a", "b"]))

    def test_same_family_twice_is_not_agreement(self):
        ev = tg.consensus([P("a"), P("a")])
        self.assertEqual(ev[0]["tier"], "B")

    def test_stripped_field_caps_at_B(self):
        c1, c2 = P("a", cost=None), P("b", cost=None)  # the gate nulls a field it strips
        ev = tg.consensus([(c1[0], ["cost"], "a"), (c2[0], [], "b")])
        self.assertEqual(ev[0]["stripped"], ["cost"])  # no voice grounded it
        self.assertEqual(ev[0]["tier"], "B")


# ---------------------------------------------------------------- netfetch
class Resp(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body.encode())
        self.status, self.headers = status, headers or {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NetFetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def fetcher(self, routes, sleeps=None):
        calls = []

        def opener(req, timeout=0):
            calls.append((req.get_method(), req.full_url, dict(req.header_items())))
            r = routes[req.full_url]
            if isinstance(r, int):
                raise urllib.error.HTTPError(req.full_url, r, "x", {}, None)
            return r
        f = netfetch.PoliteFetcher(self.tmp, opener=opener, min_interval=5, sleeper=(sleeps.append if sleeps is not None else (lambda s: None)),
                                   clock=lambda: 1000.0)
        return f, calls

    def test_robots_disallow_blocks_fetch(self):
        f, calls = self.fetcher({"https://ex.test/robots.txt": Resp("User-agent: *\nDisallow: /private", headers={"Content-Type": "text/plain"}),
                                 "https://ex.test/private/a": Resp("secret")})
        with self.assertRaises(netfetch.FetchRefused):
            f.get("https://ex.test/private/a")
        self.assertFalse([c for c in calls if c[1].endswith("/private/a")])

    def test_4xx_robots_means_allowed_5xx_means_closed(self):
        f, _ = self.fetcher({"https://a.test/robots.txt": 404, "https://a.test/x": Resp("ok")})
        self.assertEqual(f.get("https://a.test/x")["status"], 200)
        f2, _ = self.fetcher({"https://b.test/robots.txt": 503, "https://b.test/x": Resp("ok")})
        with self.assertRaises(netfetch.FetchRefused):
            f2.get("https://b.test/x")

    def test_identifies_itself_and_throttles(self):
        sleeps = []
        f, calls = self.fetcher({"https://ex.test/robots.txt": 404, "https://ex.test/a": Resp("a")}, sleeps)
        f.get("https://ex.test/a")
        self.assertTrue(all("BoardBoredBot" in c[2].get("User-agent", "") for c in calls))
        self.assertTrue(sleeps and max(sleeps) > 0, "second request to same host must wait")

    def test_conditional_get_uses_cache_on_304(self):
        f, calls = self.fetcher({"https://ex.test/robots.txt": 404,
                                 "https://ex.test/p": Resp("<p>v1</p>", headers={"Content-Type": "text/html", "ETag": '"abc"'})})
        self.assertFalse(f.get("https://ex.test/p")["from_cache"])
        f2, calls2 = self.fetcher({"https://ex.test/robots.txt": 404, "https://ex.test/p": 304})
        r = f2.get("https://ex.test/p")
        self.assertTrue(r["from_cache"])
        self.assertIn("v1", r["body"])
        self.assertEqual([c for c in calls2 if c[1].endswith("/p")][0][2].get("If-none-match"), '"abc"')

    def test_content_type_and_size_caps(self):
        f, _ = self.fetcher({"https://ex.test/robots.txt": 404, "https://ex.test/img": Resp("x", headers={"Content-Type": "image/png"})})
        with self.assertRaises(netfetch.FetchRefused):
            f.get("https://ex.test/img")

    def test_head_status_403_is_unverifiable_not_dead(self):
        f, _ = self.fetcher({"https://ex.test/robots.txt": 404, "https://ex.test/a": 403, "https://ex.test/b": 404})
        self.assertEqual(f.head_status("https://ex.test/a"), "unverifiable")
        self.assertEqual(f.head_status("https://ex.test/b"), "dead")


# ---------------------------------------------------------------- llm voice
class FakeOllama:
    def __init__(self, events):
        self.events, self.calls = events, 0

    def __call__(self, req, timeout=0):
        self.calls += 1
        body = json.dumps({"message": {"content": json.dumps({"events": self.events})}}).encode()
        return Resp(body.decode(), headers={"Content-Type": "application/json"})


class LLMVoice(unittest.TestCase):
    def test_extract_cache_and_failure_isolation(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeOllama([{"name": "X Show", "venue": "Test Hall", "date": "2026-10-09", "quote": "x show test hall", "start": "", "end": "", "cost": "", "link": ""}])
        v = llm.OllamaVoice("m", "llm:m", cache_dir=tmp, opener=fake)
        out = v.extract("line\n" * 10, TODAY, "s")
        self.assertEqual((len(out), out[0]["family"], out[0]["start"]), (1, "llm:m", None))
        n = fake.calls
        v.extract("line\n" * 10, TODAY, "s")
        self.assertEqual(fake.calls, n, "unchanged page must be served from the content-addressed cache")

        def boom(req, timeout=0):
            raise urllib.error.URLError("down")
        dead = llm.OllamaVoice("m", "llm:m", cache_dir=None, opener=boom)
        self.assertEqual(dead.extract("anything\n", TODAY, "s"), [])
        self.assertEqual(dead.stats["errors"], 1)


class Tidy(unittest.TestCase):
    def test_name_at_venue_split_and_parentheticals_stripped(self):
        e = llm.tidy_fields({"name": "\u2022 Dirty Signal @ The Mint", "venue": "The Mint (6PM)"})
        self.assertEqual((e["name"], e["venue"]), ("Dirty Signal", "The Mint"))
        e = llm.tidy_fields({"name": "Mike Sherm @ Fox", "venue": ""})
        self.assertEqual((e["name"], e["venue"]), ("Mike Sherm", "Fox"))

    def test_link_residue_removed(self):
        e = llm.tidy_fields({"name": "Taco Bingo", "venue": "Cherry Acres [https://i.test/p/1](https://i.test/p/1)"})
        self.assertEqual(e["venue"], "Cherry Acres")

    def test_tidy_cannot_invent(self):
        e = llm.tidy_fields({"name": "Plain Name", "venue": "Plain Venue"})
        self.assertEqual((e["name"], e["venue"]), ("Plain Name", "Plain Venue"))


# ---------------------------------------------------------------- the desk, composed
def card_html(title, date, show, venue, tixr):
    return ('<div role="listitem" class="w-dyn-item"><div class="cal-container cal"><div class="day-card"><div class="when"><p class="b-venue">%s</p></div>'
            '<p class="month-filter">Oct</p></div><div class="event-card-3"><div class="main-title-hover-3">%s</div><div class="date-time">'
            '<div class="date-infos"><div class="times">DOOR TIME:</div><div class="times">6:00 pm</div></div><div class="date-infos">'
            '<div class="times">SHOW STARTS:</div><div class="times">%s</div></div></div><a href="%s" class="button-2 w-button">GET TICKETS</a>'
            '<div class="age-info"><div fs-cmsfilter-field="venue" class="age">%s</div></div></div></div></div>' % (date, title, show, tixr, venue))


def jsonld(name, date, url, venue="Wrong Venue Entirely"):
    return ('<script type="application/ld+json">%s</script>' % json.dumps({
        "@context": "https://schema.org", "@type": "Event", "name": name, "startDate": date,
        "location": {"@type": "Place", "name": venue}, "offers": {"@type": "Offer", "url": url}}))


SHOWS = [("Fake Band Live", "October 9, 2026", "8:00 pm", "Test Hall", "https://tix.test/e/1", "Oct 09, 2026"),
         ("Pretend Comedy Tour", "October 10, 2026", "7:30 pm", "Mock Theater", "https://tix.test/e/2", "Oct 10, 2026"),
         ("Imaginary Orchestra", "October 17, 2026", "6:00 pm", "Test Hall", "https://tix.test/e/3", "Oct 17, 2026"),
         ("Pretend Season Pass 2026", "October 12, 2026", "6:00 pm", "Test Hall", "https://tix.test/e/4", "Oct 12, 2026"),
         ("Phantom Ballet", "October 20, 2026", "7:00 pm", "Test Hall", "https://tix.test/e/5", "Oct 20, 2026")]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        body = self.pages.get(url)
        if body is None:
            return {"url": url, "status": 404, "body": "", "content_type": "", "sha256": "", "from_cache": False}
        return {"url": url, "status": 200, "body": body, "content_type": "text/html", "sha256": "x", "from_cache": False}


def site(shows=SHOWS):
    cal = "<html><body>" + "".join(card_html(s[0], s[1], s[2], s[3], s[4]) for s in shows) + "</body></html>"
    home = "<html><head>" + "".join(jsonld(s[0], s[5], s[4]) for s in shows) + "</head></html>"
    return {"https://ex.test/event-calendar": cal, "https://ex.test/": home}


def config(tmp, **kw):
    cfg = {"state_dir": os.path.join(tmp, "desk"), "publish_path": os.path.join(tmp, "out.json"), "min_clean_cycles": 2,
           "venues": {"Test Hall": {"name": "Test Hall", "address": "1 Fake St, Bakersfield, CA", "lat": 35.37, "lng": -119.02, "area": "downtown", "default_category": "Music"},
                      "Mock Theater": {"name": "Mock Theater", "address": "2 Fake St, Bakersfield, CA", "lat": 35.371, "lng": -119.021, "area": "downtown"}},
           "categories": [["comedy", "Comedy"]], "llm": {"enabled": False, "voices": []},
           "sources": [{"id": "ex", "kind": "foxnile", "calendar_url": "https://ex.test/event-calendar", "home_url": "https://ex.test/",
                        "untrusted": {"struct:jsonld": ["venue"]}}]}
    cfg.update(kw)
    return cfg


class DeskComposed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def desk(self, pages, **kw):
        return autodesk.Desk(config(self.tmp, **kw), now=dt.datetime(2026, 10, 2, 9, 0), fetcher=FakeFetcher(pages))

    def test_end_to_end_tiers_and_wrong_jsonld_venue_ignored(self):
        res = self.desk(site()).run(TODAY)
        by = {e["name"]: e for e in res["events"]}
        self.assertEqual(set(by), {"Fake Band Live", "Pretend Comedy Tour", "Imaginary Orchestra", "Phantom Ballet"})  # season pass dropped
        self.assertTrue(all(e["tier"] == "A" for e in res["events"]), [(e["name"], e["reasons"]) for e in res["events"]])
        self.assertEqual(by["Pretend Comedy Tour"]["venue"], "Mock Theater")  # card venue, never the JSON-LD "Wrong Venue Entirely"
        self.assertEqual(by["Pretend Comedy Tour"]["category"], "Comedy")
        self.assertEqual(by["Fake Band Live"]["start"], "20:00")

    def test_propose_only_by_default_nothing_published(self):
        self.desk(site()).run(TODAY)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out.json")))

    def test_autopublish_only_after_clean_cycles(self):
        pages = site()
        for i, expect in enumerate([0, 4]):
            res = self.desk(pages, autopublish=True).run(TODAY)
            self.assertEqual(res["published"], expect, "cycle %d" % (i + 1))
        pub = json.load(open(os.path.join(self.tmp, "out.json")))
        self.assertEqual(len(pub), 4)
        self.assertTrue(all(p["source"] == "autodesk:ex" and p["lat"] for p in pub))

    def test_silent_when_nothing_changed(self):
        pages = site()
        first = self.desk(pages).run(TODAY)
        self.assertTrue(autodesk.should_notify(first))
        second = self.desk(pages).run(TODAY)
        self.assertFalse(autodesk.should_notify(second))

    def test_circuit_breaker_on_collapsed_source(self):
        many = [("Show %d" % i, "October %d, 2026" % (5 + i), "8:00 pm", "Test Hall", "https://tix.test/e/%d" % (10 + i), "Oct %02d, 2026" % (5 + i)) for i in range(8)]
        self.desk(site(many), autopublish=True).run(TODAY)
        res = self.desk(site(many[:1]), autopublish=True).run(TODAY)
        self.assertTrue(res["trips"], "8 -> 1 events must trip the breaker")
        self.assertEqual(res["published"], 0)
        self.assertEqual(res["clean_cycles"], 0)
        self.assertIn("DESK PAUSED", autodesk.summary(res, TODAY))

    def test_source_down_does_not_wipe_state_or_publish(self):
        self.desk(site(), autopublish=True).run(TODAY)
        res = self.desk({}, autopublish=True).run(TODAY)
        self.assertTrue(res["errors"])
        self.assertEqual(res["published"], 0)

    def test_job_and_mlm_style_listings_never_reach_tier_A(self):
        pages = site([("Now Hiring: Open House", "October 9, 2026", "8:00 pm", "Test Hall", "https://tix.test/e/7", "Oct 09, 2026"),
                      ("Passive Income Seminar", "October 10, 2026", "7:00 pm", "Test Hall", "https://tix.test/e/8", "Oct 10, 2026"),
                      ("Open Mic Night", "October 11, 2026", "7:00 pm", "Test Hall", "https://tix.test/e/9", "Oct 11, 2026")])
        by = {e["name"]: e for e in self.desk(pages).run(TODAY)["events"]}
        self.assertEqual((by["Now Hiring: Open House"]["tier"], "jobs_review" in by["Now Hiring: Open House"]["reasons"]), ("B", True))
        self.assertEqual(by["Passive Income Seminar"]["tier"], "B")
        self.assertEqual(by["Open Mic Night"]["tier"], "A")

    def test_unknown_venue_is_never_tier_A(self):
        pages = site([("Mystery Gig", "October 9, 2026", "8:00 pm", "Unlisted Garage", "https://tix.test/e/9", "Oct 09, 2026")])
        res = self.desk(pages).run(TODAY)
        self.assertEqual((res["events"][0]["tier"], "unknown_venue" in res["events"][0]["reasons"]), ("B", True))

    def test_llm_liar_cannot_publish_and_corroborator_upgrades(self):
        """A fabricating LLM voice contributes nothing; a truthful one agrees with the card and raises families."""
        class Liar:
            stats = {"errors": 0}

            def extract(self, text, today, sid, url=""):
                return [{"name": "Totally Fake Gala", "venue": "Test Hall", "date": "2026-10-11", "start": "20:00", "family": "llm:liar",
                         "voice": "liar", "source_id": sid, "quote": "Totally Fake Gala at Test Hall October 11, 2026 8:00 pm"},
                        {"name": "Fake Band Live", "venue": "Test Hall", "date": "2026-10-09", "start": "21:00", "family": "llm:liar",
                         "voice": "liar", "source_id": sid, "quote": "October 9, 2026 Oct Fake Band Live DOOR TIME: 6:00 pm SHOW STARTS: 8:00 pm GET TICKETS [https://tix.test/e/1] Test Hall"}]

            def unload(self):
                pass
        cfg = config(self.tmp, llm={"enabled": True, "voices": [{"model": "liar", "family": "llm:liar"}]}, min_families=3)
        d = autodesk.Desk(cfg, now=dt.datetime(2026, 10, 2, 9, 0), fetcher=FakeFetcher(site()), voice_factory=lambda v: Liar())
        res = d.run(TODAY)
        names = [e["name"] for e in res["events"]]
        self.assertNotIn("Totally Fake Gala", names)
        self.assertGreaterEqual(res["drops"].get("quote_not_in_source", 0), 1)
        fb = [e for e in res["events"] if e["name"] == "Fake Band Live"][0]
        self.assertIn("llm:liar", fb["families"])
        self.assertEqual(fb["start"], "20:00")  # liar's 21:00 was stripped by the gate, not published

    def test_approve_and_reject_are_recorded_and_durable(self):
        d = self.desk(site(), min_families=3)  # force everything to B
        res = d.run(TODAY)
        self.assertTrue(all(e["tier"] == "B" for e in res["events"]))
        keep, drop = res["events"][0]["id"], res["events"][1]["id"]
        d.reject([drop])
        d2 = self.desk(site(), min_families=3)
        n, picked = d2.approve([keep])
        self.assertEqual((picked, n), (1, 1))
        res3 = self.desk(site(), min_families=3).run(TODAY)
        self.assertNotIn(drop, [e["id"] for e in res3["events"]], "rejected events are never re-proposed")
        self.assertEqual([e for e in res3["events"] if e["id"] == keep][0]["tier"], "A")
        kinds = [json.loads(l)["kind"] for l in open(os.path.join(self.tmp, "desk", "audit.jsonl"))]
        self.assertIn("human_approve", kinds)
        self.assertIn("human_reject", kinds)

    def test_audit_is_append_only_across_runs(self):
        pages = site()
        self.desk(pages).run(TODAY)
        n1 = sum(1 for _ in open(os.path.join(self.tmp, "desk", "audit.jsonl")))
        self.desk(pages).run(TODAY)
        n2 = sum(1 for _ in open(os.path.join(self.tmp, "desk", "audit.jsonl")))
        self.assertGreater(n2, n1)


if __name__ == "__main__":
    unittest.main()
