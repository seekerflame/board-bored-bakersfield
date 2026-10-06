import datetime as dt
import io
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_desk as rd  # noqa: E402

TODAY = dt.date(2026, 10, 2)


class FakeResp(io.BytesIO):
    def __init__(self, status, url, body=b""):
        super().__init__(body)
        self.status, self._url = status, url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_opener(table):
    """table: url -> status (int) | 'timeout'. Square-shaped final URL on 200."""
    def op(req, timeout=0):
        url = req.full_url
        st = table[url]
        if st == "timeout":
            raise TimeoutError("no route")
        if st != 200:
            raise urllib.error.HTTPError(url, st, "x", {}, None)
        return FakeResp(200, "https://checkout.square.site/pay/x")
    return op


PRICING = {"combos": [
    {"tier": "Featured", "cadence": "monthly", "price": 75, "square_checkout_url": "https://sq/good"},
    {"tier": "Anchor", "cadence": "once", "price": 200, "square_checkout_url": "https://sq/dead"},
    {"tier": "Listed", "cadence": "once", "price": 35, "square_checkout_url": "https://sq/slow"},
    {"tier": "Listed", "cadence": "monthly", "price": 25},
]}


def board():
    ev = []
    for i in range(3):
        ev.append({"name": "Test Trivia %d" % i, "venue": "Test Venue Co.", "address": "100 Fake St",
                   "schedule": {"weekday": 3, "weeks": [1, 2, 3, 4]}})
    ev.append({"name": "Test Show", "venue": "Test Venue", "schedule": {"date": "2026-10-09"}})
    ev.append({"name": "Library Hour", "venue": "Beale Memorial Library", "schedule": {"weekday": 1}})
    ev.append({"name": "Pledged Fest", "venue": "Pledged Gallery LLC", "schedule": {"weekday": 5}})
    return {"city_events": ev}


class MoneyPath(unittest.TestCase):
    def test_live_dead_and_unreachable(self):
        rows = rd.check_money_path(PRICING, fake_opener({"https://sq/good": 200, "https://sq/dead": 404, "https://sq/slow": "timeout"}))
        by = {(r["tier"], r["cadence"]): r for r in rows}
        self.assertTrue(by[("Featured", "monthly")]["ok"])
        self.assertFalse(by[("Anchor", "once")]["ok"])
        self.assertEqual(by[("Anchor", "once")]["status"], 404)
        self.assertFalse(by[("Listed", "once")]["ok"], "unreachable must fail closed")
        self.assertEqual(len(rows), 3, "combos without a url are not payment links")

    def test_200_on_non_square_host_is_not_ok(self):
        def op(req, timeout=0):
            return FakeResp(200, "https://evil.example/login")
        rows = rd.check_money_path({"combos": [{"tier": "T", "cadence": "once", "price": 1, "square_checkout_url": "https://sq/x"}]}, op)
        self.assertFalse(rows[0]["ok"])


class Ledger(unittest.TestCase):
    def test_aging_and_totals(self):
        snap = rd.ledger_snapshot({"summary": {"total_collected": 0, "total_pledged_pipeline": 425},
                                   "pipeline_pledges": [{"id": "a", "sponsor_name": "X", "tier": "Featured", "cadence": "monthly",
                                                         "amount": 75, "date": "2026-08-14", "status": "verbal"},
                                                        {"id": "b", "sponsor_name": "Y", "date": "garbage"}],
                                   "transactions": []}, TODAY)
        self.assertEqual(snap["pledges"][0]["age_days"], 49)
        self.assertIsNone(snap["pledges"][1]["age_days"])
        self.assertEqual((snap["collected"], snap["pledged"], snap["transactions"]), (0, 425, 0))


class Prospects(unittest.TestCase):
    def test_entity_merge_public_sector_and_exclusion(self):
        ps = rd.prospects(board(), exclude_names=["Pledged Gallery"])
        names = [p["name"] for p in ps]
        self.assertEqual(len(ps), 1, "Test Venue Co. and Test Venue are one entity; library and pledged are out: %s" % names)
        self.assertEqual((ps[0]["weekly_slots"], ps[0]["dated_events"]), (3, 1))

    def test_rank_by_weekly_weight(self):
        b = board()
        b["city_events"] += [{"name": "Other %d" % i, "venue": "Other Place", "schedule": {"date": "2026-10-1%d" % i}} for i in range(4)]
        ps = rd.prospects(b, exclude_names=[])
        self.assertEqual(ps[0]["name"].startswith("Test Venue"), True)


class DraftVerifier(unittest.TestCase):
    SHEET = {"name": "Test Venue Co.", "weekly_slots": 3, "dated_events": 1, "address": "100 Fake St",
             "listings": ["Test Trivia 0", "Test Show"], "offer": {"tier": "Featured", "cadence": "monthly", "price": 75}}

    def test_template_always_verifies(self):
        self.assertEqual(rd.verify_draft(rd.template_draft(self.SHEET), self.SHEET), [])

    def test_fabricated_number_rejected(self):
        bad = rd.verify_draft("Hi Test Venue Co. team, you get 500 views and it is $75.", self.SHEET)
        self.assertTrue(any("500" in b for b in bad))

    def test_fabricated_name_rejected(self):
        bad = rd.verify_draft("Hi team. We also feature Dagny's Coffee near you.", self.SHEET)
        self.assertTrue(any("Dagny" in b for b in bad))

    def test_plural(self):
        one = dict(self.SHEET, weekly_slots=1, dated_events=0)
        t = rd.template_draft(one)
        self.assertIn("1 weekly slot ", t)
        self.assertNotIn("1 weekly slots", t)

    def test_model_falls_back_to_template_on_lies(self):
        calls = []

        def liar(model, prompt):
            calls.append(prompt)
            return "Hi team, we got you 900 visitors at Fake Mega Mall."
        text, how = rd.draft_with_model(self.SHEET, "m", liar, retries=1)
        self.assertEqual(how, "template")
        self.assertEqual(len(calls), 2)
        self.assertIn("rejected", calls[1])
        self.assertEqual(rd.verify_draft(text, self.SHEET), [])

    def test_model_accepted_when_grounded(self):
        text, how = rd.draft_with_model(self.SHEET, "m", lambda m, p: "Hi Test Venue Co. team, we list 3 weekly slots. Featured is $75.", retries=0)
        self.assertEqual(how, "model:m")

    def test_model_exception_falls_back(self):
        def boom(m, p):
            raise RuntimeError("ollama down")
        self.assertEqual(rd.draft_with_model(self.SHEET, "m", boom)[1], "template")


class Report(unittest.TestCase):
    def test_nudge_blocks_dead_link_and_names_age(self):
        ledger = {"summary": {}, "pipeline_pledges": [{"id": "a", "sponsor_name": "Pledged Gallery LLC", "tier": "Anchor", "cadence": "once",
                                                       "amount": 200, "date": "2026-08-15"}], "transactions": []}
        rep = rd.build_report(PRICING, ledger, board(), TODAY, opener=fake_opener({"https://sq/good": 200, "https://sq/dead": 404, "https://sq/slow": 200}))
        self.assertIn("[PAYMENT LINK NOT LIVE YET]", rep["nudges"][0])
        self.assertIn("48 days ago", rep["nudges"][0])
        self.assertIn("MONEY PATH: 2/3", rd.summary(rep))

    def test_live_link_is_offered(self):
        ledger = {"summary": {}, "pipeline_pledges": [{"id": "a", "sponsor_name": "P", "tier": "Featured", "cadence": "monthly",
                                                       "amount": 75, "date": "2026-09-30"}], "transactions": []}
        rep = rd.build_report(PRICING, ledger, board(), TODAY, opener=fake_opener({"https://sq/good": 200, "https://sq/dead": 404, "https://sq/slow": 200}))
        self.assertIn("https://sq/good", rep["nudges"][0])


class PaymentsPaused(unittest.TestCase):
    PAUSED = dict(PRICING, payments={"paused": True, "since": "2026-10-06", "reason": "pending a model that scales"})

    def test_no_links_checked_or_offered(self):
        def never(req, timeout=0):
            raise AssertionError("network touched while payments are paused")
        self.assertEqual(rd.check_money_path(self.PAUSED, never), [])
        self.assertIsNone(rd.offer_for(self.PAUSED))

    def test_report_holds_pledges_and_pitches_free_only(self):
        ledger = {"summary": {}, "pipeline_pledges": [{"id": "a", "sponsor_name": "P", "tier": "Featured", "cadence": "monthly",
                                                       "amount": 75, "date": "2026-09-30"}], "transactions": []}

        def never(req, timeout=0):
            raise AssertionError("network touched while payments are paused")
        rep = rd.build_report(self.PAUSED, ledger, board(), TODAY, opener=never)
        self.assertIn("ON HOLD", rep["nudges"][0])
        self.assertNotIn("Pay here", rep["nudges"][0])
        self.assertTrue(rep["prospects"])
        for p in rep["prospects"]:
            self.assertNotIn("$", p["draft"], "no price in a draft while payments are paused")
        self.assertIn("MONEY PATH: PAUSED since 2026-10-06", rd.summary(rep))
        self.assertIn("PAUSED", rd.render(rep))

    def test_unpaused_default_unchanged(self):
        self.assertFalse(rd.payments_paused(PRICING))
        self.assertIsNotNone(rd.offer_for(PRICING))


if __name__ == "__main__":
    unittest.main()
