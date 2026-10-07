import csv
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leads as L  # noqa: E402
from netfetch import FetchRefused  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 12, 0, 0)

BOARD = {
    "city_events": [
        {"name": "Pub Trivia", "venue": "Test Brewing Co.", "address": "1 Fake St", "lat": 35.1, "lng": -119.1, "area": "downtown",
         "category": "Games", "schedule": {"weekday": 2}, "link": "https://testbrewing.example/events/trivia"},
        {"name": "Karaoke", "venue": "Test Brewing Company", "schedule": {"weekday": 4}, "category": "Nightlife"},
        {"name": "Beer Fest", "venue": "Test Brewing Company", "schedule": {"date": "2026-10-20"}, "link": "https://tixr.com/e/1"},
        {"name": "Story Hour", "venue": "Beale Memorial Library", "schedule": {"weekday": 3}},
        {"name": "One Off", "venue": "Lone Gallery", "schedule": {"date": "2026-11-01"}},
    ],
    "city_events_archive": [{"name": "Old Show", "venue": "Lone Gallery", "category": "Art"}],
    "events": [{"vendors": [{"name": "Corner Cafe", "category": "Cafe", "address": "5 Fake St", "website": "", "instagram": ""}]}],
}
LEDGER = {"pipeline_pledges": [
    {"sponsor_name": "BAA (Test Art Association)", "tier": "Anchor", "cadence": "once", "amount": 200, "date": "2026-08-15", "status": "Verbal"},
    {"sponsor_name": "Test Brewing Co", "tier": "Featured", "cadence": "monthly", "amount": 75, "date": "2026-08-14", "status": "Verbal"}]}
NEWS = [{"venue": "Lone Gallery", "issues_featured": "4", "appearances": "6", "last_seen": "2026-10-01", "address": "9 Fake Ave",
         "contact_kind": "website", "contact": "https://lonegallery.example/"},
        {"venue": "Bakersfield Community Theatre", "issues_featured": "5", "appearances": "7", "last_seen": "2026-09-20", "address": "",
         "contact_kind": "instagram_post", "contact": "https://www.instagram.com/promoter/reel/abc/"}]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        v = self.pages.get(url)
        if isinstance(v, Exception):
            raise v
        if v is None:
            return {"status": 404, "body": ""}
        return {"status": 200, "body": v}


def fresh():
    d = tempfile.mkdtemp()
    return L.Store(d), d


class Sync(unittest.TestCase):
    def setUp(self):
        self.store, self.dir = fresh()
        L.sync_all(self.store, BOARD, LEDGER, None, NOW)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_entities_merge_across_spellings_and_count_slots(self):
        b = self.store.find("Test Brewing Company")
        self.assertIsNotNone(b)
        self.assertEqual((b["on_map"]["weekly"], b["on_map"]["dated"]), (2, 1))
        self.assertEqual(b["address"], "1 Fake St")
        self.assertEqual(sum(1 for l in self.store.leads.values() if "brewing" in l["name"].lower()), 1)

    def test_pledge_with_parenthetical_alias_creates_warm_lead(self):
        baa = self.store.find("Test Art Association")
        self.assertIsNotNone(baa)
        self.assertEqual((baa["stage"], baa["tier"]), ("in_talks", "A"))
        self.assertTrue(any("said yes" in w for w in baa["why"]))
        self.assertIn("payments are paused", baa["pledge"]["note"])

    def test_pledge_merges_into_the_listing_business(self):
        b = self.store.find("Test Brewing Co")
        self.assertEqual(b["pledge"]["amount"], 75)
        self.assertEqual(b["tier"], "A")

    def test_public_sector_is_flagged_and_left_out_of_default_list(self):
        lib = self.store.find("Beale Memorial Library")
        self.assertEqual(lib["sector"], "public")
        self.assertNotIn(lib, L.ranked(self.store))
        self.assertIn(lib, L.ranked(self.store, include_public=True))

    def test_corridor_storefront_and_archive_only(self):
        self.assertTrue(self.store.find("Corner Cafe")["corridor"])
        self.assertEqual(self.store.find("Lone Gallery")["past_events"], 1)

    def test_ticketing_link_is_not_an_own_site_candidate(self):
        b = self.store.find("Test Brewing Company")
        urls = [c["url"] for c in b["candidates"]]
        self.assertIn("https://testbrewing.example/events/trivia", urls)
        self.assertNotIn("https://tixr.com/e/1", urls)

    def test_newsletter_rows_add_leads_and_ignore_post_links(self):
        csvp = os.path.join(self.dir, "outreach_x.csv")
        with open(csvp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(NEWS[0].keys()))
            w.writeheader()
            w.writerows(NEWS)
        L.sync_all(self.store, BOARD, LEDGER, csvp, NOW)
        g = self.store.find("Lone Gallery")
        self.assertEqual(g["newsletter"]["issues"], 4)
        self.assertIn("https://lonegallery.example/", [c["url"] for c in g["candidates"]])
        bct = self.store.find("Bakersfield Community Theatre")
        self.assertIsNotNone(bct)
        self.assertEqual(bct["candidates"], [], "an instagram POST link names the poster, not the venue")

    def test_resync_never_resets_stage_notes_or_deletes(self):
        b = self.store.find("Test Brewing Company")
        had = len(b["notes"])
        L.set_stage(self.store, b, "contacted", "emailed Nick's intro", NOW)
        L.add_note(self.store, b, "asked about Tuesdays", NOW)
        n = len(self.store.leads)
        L.sync_all(self.store, {"city_events": [], "city_events_archive": []}, {}, None, NOW)
        b2 = self.store.find("Test Brewing Company")
        self.assertEqual(b2["stage"], "contacted")
        self.assertEqual(len(b2["notes"]), had + 2)
        self.assertEqual(len(self.store.leads), n, "a lead that left the sources is kept")

    def test_events_log_is_append_only_and_records_stage_changes(self):
        b = self.store.find("Test Brewing Company")
        with open(self.store.log_path) as f:
            before = f.read()
        L.set_stage(self.store, b, "replied", "said hi", NOW)
        with open(self.store.log_path) as f:
            after = f.read()
        self.assertTrue(after.startswith(before))
        self.assertIn('"to": "replied"', after)

    def test_bad_stage_and_missing_source_are_rejected(self):
        b = self.store.find("Test Brewing Company")
        with self.assertRaises(ValueError):
            L.set_stage(self.store, b, "won", "", NOW)
        with self.assertRaises(ValueError):
            L.set_fact(self.store, b, "email", "a@b.co", "", NOW)

    def test_persists_across_loads(self):
        again = L.Store(self.dir)
        self.assertEqual(set(again.leads), set(self.store.leads))


class Names(unittest.TestCase):
    def test_clean_name_forms(self):
        self.assertEqual(L.clean_name("Centro Cali Brewing | 1000 19th St."), ("Centro Cali Brewing", "1000 19th St."))
        self.assertEqual(L.clean_name("The Kabob Guy (901 20th St)"), ("The Kabob Guy", "901 20th St"))
        self.assertEqual(L.clean_name("[https://x.example/a?b=1] 3625 Marriott Dr"), (None, "3625 Marriott Dr"))
        self.assertEqual(L.clean_name("316 H St")[0], None)
        self.assertEqual(L.clean_name("Mountain Ridge Dr")[0], None)
        self.assertEqual(L.clean_name("Location provided after RSVP"), (None, None))
        self.assertEqual(L.clean_name("Eye Street Cafe")[0], "Eye Street Cafe", "a business with a street word is still a business")
        self.assertEqual(L.clean_name("18th St. Collections")[0], "18th St. Collections")

    def test_split_names(self):
        self.assertEqual(L.split_names("BAA (Bakersfield Art Association)"), ["Bakersfield Art Association"])
        self.assertEqual(L.split_names("Arts Council of Kern (ACK)"), ["Arts Council of Kern"])
        self.assertEqual(L.split_names("Tin Cup Coffee, Shafter"), ["Tin Cup Coffee, Shafter", "Tin Cup Coffee"])
        self.assertEqual(L.split_names("316 H St"), [])

    def test_shared_acronym_never_merges_two_organisations(self):
        store, d = fresh()
        try:
            store.ensure("Arts Council of Kern (ACK)", NOW)
            store.ensure("Art Center Kern (ACK)", NOW)
            self.assertEqual(len(store.leads), 2)
        finally:
            shutil.rmtree(d)

    def test_junk_names_never_become_leads_and_address_becomes_a_hint(self):
        store, d = fresh()
        try:
            self.assertIsNone(store.ensure("316 H St", NOW))
            lead = store.ensure("Centro Cali Brewing | 1000 19th St.", NOW)
            self.assertEqual((lead["name"], lead["address"]), ("Centro Cali Brewing", "1000 19th St."))
            self.assertEqual(len(store.leads), 1)
        finally:
            shutil.rmtree(d)

    def test_churches_and_schools_count_as_public_sector(self):
        self.assertTrue(L.is_public("Bakersfield First Church"))
        self.assertTrue(L.is_public("Martin Luther King Elementary School"))
        self.assertFalse(L.is_public("Temblor Brewing Co."))


class Duplicates(unittest.TestCase):
    def test_filler_words_do_not_make_a_new_business(self):
        for a, b in [("Jerry's Pizza", "Jerry's Pizza & Pub"), ("Sky Zone", "Sky Zone Bakersfield"), ("Gaslight Melodrama", "Gaslight Melodrama Theatre"),
                     ("Historic Fox Theater", "Historic Bakersfield Fox Theater"), ("DoubleTree Hotel", "DoubleTree by Hilton Hotel Bakersfield")]:
            self.assertTrue(L.same_business(a, b), (a, b))

    def test_real_extra_words_keep_leads_apart(self):
        for a, b in [("Kern Raceway", "Kevin Harvick's Kern Raceway"), ("River Walk", "lululemon River Walk"), ("The Ovation Theatre", "The Underground at The Ovation Theatre"),
                     ("Dignity Health Arena", "Centennial Plaza at Dignity Health Arena"), ("Hart Park", "Hart Memorial Park")]:
            self.assertFalse(L.same_business(a, b), (a, b))

    def test_performer_and_noise_suffixes_are_not_part_of_the_venue(self):
        self.assertEqual(L.clean_name("Jerry's Pizza & Pub with Kev King")[0], "Jerry's Pizza & Pub")
        self.assertEqual(L.clean_name("Art with Heart")[0], "Art with Heart", "a short name that contains 'with' is the whole name")
        self.assertEqual(L.clean_name("Legends Event Center SOLD OUT")[0], "Legends Event Center")
        self.assertEqual(L.clean_name("In Your Wildest Dreams Parking Lot")[0], "In Your Wildest Dreams")

    def test_merge_pass_collapses_existing_duplicates_and_keeps_everything(self):
        store, d = fresh()
        try:
            x = store.ensure("Jerry's Pizza", NOW)
            y = dict(x, id="jerry-s-pizza-and-pub", name="Jerry's Pizza & Pub", aliases=[], notes=[{"ts": "2026-10-01T00:00:00", "note": "from the long one"}],
                     pledge={"amount": 75}, candidates=[{"kind": "website", "url": "https://jerryspizza.com", "source": "x"}], contact={})
            store.leads[y["id"]] = y  # a split pair, the way an older run produced them
            self.assertEqual(L.merge_duplicates(store, NOW), 1)
            self.assertEqual(len(store.leads), 1)
            kept = next(iter(store.leads.values()))
            self.assertIn("Jerry's Pizza & Pub", [kept["name"]] + kept["aliases"])
            self.assertEqual(kept["pledge"]["amount"], 75)
            self.assertTrue(any(n["note"] == "from the long one" for n in kept["notes"]))
            with open(store.log_path) as f:
                self.assertIn('"merged"', f.read())
        finally:
            shutil.rmtree(d)

    def test_a_business_name_ending_in_a_street_word_is_not_an_address(self):
        self.assertEqual(L.clean_name("Wholly Different Place")[0], "Wholly Different Place")
        self.assertEqual(L.clean_name("The Garden Court")[0], "The Garden Court")
        self.assertIsNone(L.clean_name("Mountain Ridge Dr")[0])


class Extraction(unittest.TestCase):
    HTML = ('<a href="mailto:Hello@TestBrewing.example?subject=x">mail</a> <a href="tel:+16615550142">call</a>'
            '<a href="https://www.instagram.com/testbrewing/">ig</a><a href="https://www.instagram.com/p/XYZ/">post</a>'
            '<a href="https://www.facebook.com/TestBrewing">fb</a><a href="https://www.facebook.com/sharer/sharer.php?u=1">share</a>'
            '<img src="logo@2x.png"> contact: noreply@sentry.io <a href="/about/contact-us">Contact</a>')

    def test_email_filters_noise_and_images(self):
        self.assertEqual(L.extract_emails(self.HTML), ["hello@testbrewing.example"])

    def test_package_versions_and_entities_are_not_emails(self):
        js = "slick-carousel@1.8.1 intl-segmenter@11.7.10 bootstrap@5.0.1 leaflet@1.9.4 a@b.c"
        self.assertEqual(L.extract_emails(js), [])
        self.assertEqual(L.extract_emails('<a href="mailto:info@bmoa.org&quot;>x</a> info@thewellcomedyclub.com\\'), ["info@bmoa.org", "info@thewellcomedyclub.com"])

    def test_only_bakersfield_phones(self):
        self.assertEqual(L.extract_phones('<a href="tel:+18052961128">HQ</a> and (661) 555-0100'), ["(661) 555-0100"])

    def test_phone_from_tel_and_local_pattern(self):
        self.assertEqual(L.extract_phones(self.HTML), ["(661) 555-0142"])
        self.assertEqual(L.extract_phones("Call us (661) 555-0199 today"), ["(661) 555-0199"])

    def test_socials_skip_posts_and_share_links(self):
        ig, fb = L.extract_socials(self.HTML)
        self.assertEqual((ig, fb), (["testbrewing"], ["TestBrewing"]))

    def test_contact_page_is_same_host_only(self):
        self.assertEqual(L.find_contact_page(self.HTML, "https://testbrewing.example/"), "https://testbrewing.example/about/contact-us")
        self.assertIsNone(L.find_contact_page('<a href="https://other.example/contact">Contact</a>', "https://testbrewing.example/"))


class Ownership(unittest.TestCase):
    def test_domain_must_be_built_from_the_name(self):
        self.assertTrue(L.host_matches("Temblor Brewing Co.", "https://temblorbrewing.com/events/x"))
        self.assertTrue(L.host_matches("Jerry's Pizza & Pub", "jerryspizza.com"))
        self.assertTrue(L.host_matches("Bakersfield Museum of Art", "https://www.bmoa.org/visit"), "initials acronym")
        self.assertTrue(L.host_matches("Arts Council of Kern", "https://kernarts.org/"))
        self.assertTrue(L.host_matches("The Well Comedy Club", "https://www.thewellcomedyclub.com/shows/1"))
        self.assertTrue(L.host_matches("BarrelHouse Bakersfield", "https://barrelhousebrewing.com/"))

    def test_pages_that_only_mention_the_business_do_not_count(self):
        self.assertFalse(L.host_matches("The Marketplace", "https://www.bmoa.org/via-arte"))
        self.assertFalse(L.host_matches("Historic Bakersfield Fox Theater", "https://www.bakersfieldlive.com/"))
        self.assertFalse(L.host_matches("DoubleTree by Hilton Hotel Bakersfield", "https://kchcc.org/"))
        self.assertFalse(L.host_matches("Greenacres Community Center", "https://www.kvpr.org/news"))
        self.assertFalse(L.host_matches("Dagny's Coffee Co.", "https://kernpoetry.com/first-friday/"))
        self.assertFalse(L.host_matches("Lake Ming", "https://njbaracing.net/"))

    def test_social_pick_prefers_a_lookalike_and_refuses_ambiguity(self):
        lead = {"name": "Temblor Brewing Company"}
        self.assertEqual(L.pick_social(["webagency", "temblorbrewing"], lead, "https://temblorbrewing.com/"), "temblorbrewing")
        self.assertIsNone(L.pick_social(["webagency", "otherpage"], lead, "https://temblorbrewing.com/"))
        self.assertEqual(L.pick_social(["onlyone"], lead, "https://temblorbrewing.com/"), "onlyone")


class Recon(unittest.TestCase):
    def setUp(self):
        self.store, self.dir = fresh()
        L.sync_all(self.store, BOARD, LEDGER, None, NOW)
        self.brew = self.store.find("Test Brewing Company")
        self.site = "https://testbrewing.example/events/trivia"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def page(self, name="Test Brewing Company"):
        return "<html><title>%s</title><body><h1>Welcome to %s</h1>%s</body></html>" % (name, name, Extraction.HTML)

    def test_own_site_yields_sourced_facts(self):
        f = FakeFetcher({self.site: self.page()})
        self.assertEqual(L.recon_one(self.store, self.brew, f, NOW), "found")
        c = self.brew["contact"]
        self.assertEqual(c["email"]["value"], "hello@testbrewing.example")
        self.assertEqual(c["email"]["source"], self.site)
        self.assertEqual(c["phone"]["value"], "(661) 555-0142")
        self.assertIn("testbrewing", c["instagram"]["value"])
        self.assertTrue(c["email"]["verified_at"])

    def test_a_site_whose_domain_is_not_the_business_is_never_read(self):
        L.add_candidate(self.brew, "website", "https://somepromoter.example/shows/test-brewing-night", "listing link")
        self.brew["candidates"] = [c for c in self.brew["candidates"] if "somepromoter" in c["url"]]
        f = FakeFetcher({"https://somepromoter.example/": self.page(), "https://somepromoter.example/shows/test-brewing-night": self.page()})
        self.assertEqual(L.recon_one(self.store, self.brew, f, NOW), "nothing")
        self.assertEqual(f.calls, [], "not even fetched")
        self.assertEqual(self.brew["contact"], {})
        self.assertTrue(any("not built from the business" in t for t in self.brew["recon"]["tried"]))

    def test_robots_refusal_and_errors_are_recorded_not_raised(self):
        f = FakeFetcher({self.site: FetchRefused("disallowed by robots.txt")})
        self.assertEqual(L.recon_one(self.store, self.brew, f, NOW), "nothing")
        self.assertTrue(any("refused" in t for t in self.brew["recon"]["tried"]))

    def test_fresh_recon_is_skipped_unless_forced(self):
        f = FakeFetcher({self.site: self.page()})
        self.brew["recon"] = {"at": dt.datetime.now().isoformat(timespec="seconds"), "found": [], "tried": []}
        self.assertEqual(L.recon_one(self.store, self.brew, f), "fresh")
        self.assertEqual(f.calls, [])
        self.assertEqual(L.recon_one(self.store, self.brew, f, force=True), "found")

    def test_reachable_lead_scores_higher(self):
        before = self.brew["score"]
        L.recon_one(self.store, self.brew, FakeFetcher({self.site: self.page()}), NOW)
        self.brew["score"], self.brew["tier"], self.brew["why"] = L.score_lead(self.brew)
        self.assertEqual(self.brew["score"], before + 1)

    def test_dead_event_link_falls_back_to_the_front_page(self):
        f = FakeFetcher({"https://testbrewing.example/": self.page()})  # the deep event link 404s, the root is fine
        self.assertEqual(L.recon_one(self.store, self.brew, f, NOW), "found")
        self.assertEqual(f.calls[0], "https://testbrewing.example/", "front page is tried before the deep link")
        self.assertEqual(self.brew["contact"]["website"]["value"], "https://testbrewing.example/")

    def test_scheme_less_candidate_is_fixed(self):
        self.assertEqual(L.full_url("jerryspizza.com"), "https://jerryspizza.com")
        self.assertEqual(L.origin_of("jerryspizza.com/events/x"), "https://jerryspizza.com/")
        self.assertEqual(L.full_url(""), "")

    def test_retry_empty_only_touches_leads_that_found_nothing(self):
        self.brew["recon"] = {"at": NOW.isoformat(), "found": ["email"], "tried": []}
        f = FakeFetcher({})
        L.run_recon(self.store, f, retry_empty=True, now=NOW)
        self.assertEqual(f.calls, [])

    def test_contact_page_hop_when_home_has_no_contact(self):
        home = "<html><body>Test Brewing Company <a href='/contact'>Contact</a></body></html>"
        f = FakeFetcher({self.site: home, "https://testbrewing.example/contact": "<p>Call (661) 555-0111 or mailto:hi@testbrewing.example</p>"})
        L.recon_one(self.store, self.brew, f, NOW)
        self.assertEqual(self.brew["contact"]["phone"]["value"], "(661) 555-0111")
        self.assertEqual(self.brew["contact"]["phone"]["source"], "https://testbrewing.example/contact")


class Reset(unittest.TestCase):
    def test_reset_keeps_history_and_human_facts(self):
        store, d = fresh()
        try:
            L.sync_all(store, BOARD, LEDGER, None, NOW)
            b = store.find("Test Brewing Company")
            L.set_fact(store, b, "email", "wrong@scripps.example", "https://news.example/story", NOW)
            L.set_fact(store, b, "phone", "(661) 555-0100", "owner told me on the phone", NOW)
            L.set_fact(store, b, "website", "https://testbrewing.example/", "domain is built from the business's name", NOW, auto=True)
            b["recon"] = {"at": NOW.isoformat(), "found": ["email"], "tried": []}
            self.assertEqual(L.reset_recon(store, "stricter ownership", NOW), 1)
            self.assertNotIn("email", b["contact"])
            self.assertNotIn("website", b["contact"], "auto facts go even when the source text is not a URL")
            self.assertEqual(b["contact"]["phone"]["value"], "(661) 555-0100", "a human-typed fact survives")
            self.assertEqual(b["superseded"][0]["contact"]["email"]["value"], "wrong@scripps.example")
            self.assertEqual(b["recon"], {})
        finally:
            shutil.rmtree(d)


class Export(unittest.TestCase):
    def test_csv_and_md(self):
        store, d = fresh()
        try:
            L.sync_all(store, BOARD, LEDGER, None, NOW)
            L.set_fact(store, store.find("Test Brewing Company"), "email", "hi@testbrewing.example", "told to me by the owner", NOW)
            c, m = L.export(store)
            with open(c) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["tier"], "A")
            self.assertTrue(any(r["email"] == "hi@testbrewing.example" for r in rows))
            self.assertEqual([r["sector"] for r in rows][-1], "public", "public-sector entries sort last")
            with open(m) as f:
                md = f.read()
            self.assertIn("Nothing here was sent to anyone", md)
            self.assertIn("Test Brewing Co.", md)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
