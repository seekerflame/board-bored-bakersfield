import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leads  # noqa: E402
import outreach_desk as od  # noqa: E402

ISSUE = """The Test Guy's Newsletter! Issue #1
Oct 2, 2026
FRIDAY, OCTOBER 2
• Chess Night @ Test Venue Co. [https://www.instagram.com/p/AAA111/] (6PM) 100 Fake St. Weekly chess.
• Polka Hour @ Mock Cafe [https://mockcafe.example/events] (7PM) 5 Fake Ave.
SATURDAY, OCTOBER 3
• Trivia @ Test Venue Co. [https://www.eventbrite.com/e/trivia-1] (7PM) 100 Fake St.
SUNDAY, OCTOBER 4
• Brunch Jam @ Imaginary Library (10AM)
"""


class Outreach(unittest.TestCase):
    def test_link_kinds(self):
        self.assertEqual(od.link_kind("https://www.instagram.com/p/X/"), "instagram_post")
        self.assertEqual(od.link_kind("https://www.instagram.com/mockcafe/"), "instagram_profile")
        self.assertEqual(od.link_kind("https://www.google.com/search?q=x"), "search")
        self.assertEqual(od.link_kind("https://secure.qgiv.com/for/x"), "ticketing")
        self.assertEqual(od.link_kind("https://mockcafe.example/"), "website")
        self.assertEqual(od.link_kind("https://www.facebook.com/events/123"), "facebook_event")

    def test_best_contact_prefers_website_and_never_search(self):
        self.assertEqual(od.best_contact(["https://www.google.com/search?q=a", "https://www.instagram.com/p/X/", "https://mockcafe.example/"])[1], "website")
        self.assertEqual(od.best_contact(["https://www.google.com/search?q=a"])[1], "none")

    def test_aggregate_ranks_counts_and_on_map(self):
        rows = od.aggregate({1: ISSUE}, {"Mock Cafe"})
        by = {r["venue"]: r for r in rows}
        self.assertEqual(by["Test Venue Co."]["appearances"], 2)
        self.assertEqual(rows[0]["venue"], "Test Venue Co.")
        self.assertTrue(by["Mock Cafe"]["on_map"])
        self.assertFalse(by["Test Venue Co."]["on_map"])
        self.assertEqual(by["Imaginary Library"]["sector"], "public")
        self.assertEqual(by["Test Venue Co."]["address"], "100 Fake St.")

    def test_draft_is_truthful_and_distinguishes_on_map(self):
        rows = od.aggregate({1: ISSUE}, {"Mock Cafe"})
        by = {r["venue"]: r for r in rows}
        t, bad = od.draft(by["Test Venue Co."])
        self.assertEqual(bad, [])
        self.assertIn("I saw", t)
        self.assertNotIn("already on it", t)
        t2, bad2 = od.draft(by["Mock Cafe"])
        self.assertIn("already on it", t2)

    def test_untraceable_draft_is_withheld_in_report(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        issue = ISSUE.replace("Mock Cafe", "Zq9 Vvxk")
        board = os.path.join(tmp, "b.json")
        json.dump({"city_events": []}, open(board, "w"))

        class F:
            def get(self, url):
                return {"status": 200, "body": "<html><body>" + issue.replace("\n", "<br>") + "</body></html>"}
        rows, base = od.build([1], F(), board_path=board, out_dir=tmp, today=dt.date(2026, 10, 2), store=leads.Store(os.path.join(tmp, "leads")))
        self.assertTrue(os.path.exists(base + ".csv") and os.path.exists(base + ".md"))

    def test_mark_writes_the_one_stage_tracker_append_only(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        store = leads.Store(tmp)
        od.mark("Test Venue Co.", "contacted", store=store, now=dt.datetime(2026, 10, 2, 9))
        od.mark("Test Venue Co.", "replied", note="wants a call", store=store, now=dt.datetime(2026, 10, 2, 10))
        again = leads.Store(tmp)  # durable: a fresh load sees it
        lead = again.find("Test Venue Co.")
        self.assertEqual(lead["stage"], "replied")
        self.assertEqual(od.status_of(again, "Test Venue Co."), "replied")
        self.assertEqual(od.status_of(again, "Nobody Here"), "not_contacted")
        with open(again.log_path) as f:
            events = [json.loads(l) for l in f]
        self.assertEqual([e["type"] for e in events], ["created", "stage", "stage"])
        self.assertEqual(od.mark("Test Venue Co.", "joined", store=again)["stage"], "partner")

    def test_report_status_column_comes_from_the_registry(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        store = leads.Store(os.path.join(tmp, "leads"))
        od.mark("Mock Cafe", "contacted", store=store)
        board = os.path.join(tmp, "b.json")
        json.dump({"city_events": []}, open(board, "w"))

        class F:
            def get(self, url):
                return {"status": 200, "body": "<html><body>" + ISSUE.replace("\n", "<br>") + "</body></html>"}
        rows, _ = od.build([1], F(), board_path=board, out_dir=tmp, today=dt.date(2026, 10, 2), store=store)
        by = {r["venue"]: r["status"] for r in rows}
        self.assertEqual(by.get("Mock Cafe"), "contacted")
        self.assertTrue(all(v == "not_contacted" for k, v in by.items() if k != "Mock Cafe"))

    def test_marking_a_street_address_is_refused(self):
        with self.assertRaises(ValueError):
            od.mark("316 H St", "contacted", store=leads.Store(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
