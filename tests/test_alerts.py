"""Phase 6: nse-alerts on the real INFY chart of Thu 5 Nov 2026 (Phase 0)."""

import json
import os
import unittest

from _load import FIX, load_skill

import yahoo

from test_budget import CAL, Chain, PHASE0

alerts = load_skill("nse-alerts", "alerts")
size = load_skill("nse-budget", "size")


class Base(Chain):
    def al(self, *argv, now="2026-11-05 10:16", offline=True):
        args = list(argv) + ["--now", now, "--holidays", CAL] + (["--offline", PHASE0] if offline else [])
        return self.run_mod(alerts, args)

    def saved(self):
        with open(os.path.join(self.tmp, "alerts.json"), encoding="utf-8") as f:
            return json.load(f)


class Quote(Base):
    def test_offline_quote_is_the_last_completed_bar(self):
        code, out = self.al("quote", "INFY", "--write")
        self.assertEqual(code, 0)
        self.assertIn("Rs. 1,164.20 at 10:15 IST (the close of the 10:10 bar", out)
        with open(os.path.join(self.tmp, "prices.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["INFY.NS"], {"price": 1164.2, "time": "2026-11-05 10:15 IST",
                                                       "source": "the close of the 10:10 bar"})

    def test_budget_uses_the_fresh_quote(self):
        self.plan()
        self.al("quote", "INFY", "--write")
        code, out = self.run_mod(size, ["--now", "2026-11-05 10:16", "--json"])
        self.assertEqual(json.loads(out)["entry_used"], 1164.2)

    def test_live_quote_uses_yahoos_latest_trade(self):
        real = yahoo.load_offline(PHASE0)["INFY.NS"]
        old = yahoo.fetch_chart
        yahoo.fetch_chart = lambda sym, rng, interval: real
        try:
            code, out = self.al("quote", "INFY", now="2026-11-05 15:40", offline=False)
        finally:
            yahoo.fetch_chart = old
        self.assertEqual(code, 0)
        self.assertIn("Rs. 1,167.40 at 15:28 IST (Yahoo's latest trade", out)   # meta.regularMarketPrice


class PriceAlerts(Base):
    def test_rings_only_on_a_completed_bar_after_the_alert(self):
        self.al("add", "INFY", "above", "1169")                      # 10:15 bar high 1,168.80; 10:20 bar 1,169.90
        code, out = self.al("check", now="2026-11-05 10:24")
        self.assertEqual(code, 1)                                  # the 10:20 bar is not complete yet
        code, out = self.al("check", now="2026-11-05 10:25")
        self.assertEqual(code, 0)
        self.assertIn("touched 1,169.90 at the 10:20 bar", out)
        code, out = self.al("check", now="2026-11-05 10:40")
        self.assertIn("0 rang", out)                               # rings once

    def test_plan_stop_and_target_cancel_each_other(self):
        self.plan("--stop", "pct:0.5", "--target", "r:1.5")        # stop 1,154.65, target 1,169.30
        self.al("from-plan")
        code, out = self.al("watch", "--every", "300")
        self.assertIn("RING [10:26] INFY above 1,169.30: touched 1,169.90 at the 10:20 bar", out)
        self.assertIn("(The below alert is cancelled.)", out)
        self.assertIn("FLAT BY", out)
        kinds = {a["kind"] + str(a.get("price", a.get("time"))): a["fired_at"] for a in self.saved()["alerts"]}
        self.assertEqual(kinds["below1154.65"], "cancelled")

    def test_alerts_added_while_watching_are_watched_and_kept(self):
        # A second stock's from-plan while watch runs in another window: before the fix, watch
        # never saw it and its next write erased it from alerts.json (stop alert included).
        self.al("add-time", "10:30", "--note", "first stock")
        real_sleep, calls = alerts.Clock.sleep, []

        def sleep(clock, seconds):
            real_sleep(clock, seconds)
            calls.append(1)
            if len(calls) == 1:
                self.al("add-time", "11:00", "--note", "second stock", now="2026-11-05 10:21")
                self.al("add", "INFY", "below", "1100", now="2026-11-05 10:21")
        alerts.Clock.sleep = sleep
        try:
            code, out = self.al("watch", "--every", "300", "--until", "11:30")
        finally:
            alerts.Clock.sleep = real_sleep
        self.assertIn("Now also watching: 11:00 IST: second stock", out)
        self.assertIn("Now also watching: INFY below 1,100.00", out)
        self.assertIn("10:30 IST (now 10:31): first stock", out)
        self.assertIn("11:00 IST (now 11:01): second stock", out)
        saved = {a.get("note") or a["kind"]: a["fired_at"] for a in self.saved()["alerts"]}
        self.assertEqual(saved, {"first stock": "10:31", "second stock": "11:01", "below": None})
        self.assertEqual(out.count("first stock"), 1)                # rang once, kept rung after the re-read

    def test_duplicates_and_bad_input(self):
        self.al("add", "INFY", "below", "1150")
        code, out = self.al("add", "INFY", "below", "1150")
        self.assertIn("Already there", out)
        self.assertEqual(len(self.saved()["alerts"]), 1)
        code, out = self.al("add", "BRK.B", "above", "10")
        self.assertIn("only NSE symbols", out)


class TimeAlerts(Base):
    def test_time_alert_and_bells(self):
        self.al("add-time", "13:00", "--note", "check the plan")
        self.al("bells")
        code, out = self.al("check", now="2026-11-05 12:59")
        self.assertIn("09:15 IST (now 12:59): NSE opening bell", out)
        self.assertNotIn("check the plan", out)
        code, out = self.al("check", now="2026-11-05 13:00")
        self.assertIn("13:00 IST: check the plan", out)

    def test_closed_days_and_a_new_session(self):
        self.al("add-time", "13:00")
        code, out = self.al("check", now="2026-11-07 13:00")
        self.assertEqual(code, 1)
        self.assertIn("Saturday", out)
        code, out = self.al("list", now="2026-11-06 09:00")
        self.assertIn("No alerts for 2026-11-06", out)

    def test_polite_polling(self):
        self.al("add-time", "13:00")
        code, out = self.al("watch", "--every", "5")
        self.assertIn("at least 30 seconds", out)

    def test_from_plan_needs_todays_plan(self):
        code, out = self.al("from-plan")
        self.assertIn("/nse-exit plan --write", out)


if __name__ == "__main__":
    unittest.main()
