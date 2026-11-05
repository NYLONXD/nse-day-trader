"""Phase 3: nse-entry on real saved Yahoo charts (Thu 5 Nov 2026, and Phase 0's 60 days)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load_skill

entry = load_skill("nse-entry", "entry")
scan = load_skill("nse-scanner", "scan")

FIVE = os.path.join(FIX, "nse-scanner", "wall-5m-5d-2026-11-05.json.gz")
DAILY = os.path.join(FIX, "nse-scanner", "wall-1d-3mo-2026-11-05.json.gz")
WALL = os.path.join(FIX, "nse-scanner", "wall-symbols.txt")
BANDS = os.path.join(FIX, "nse-scanner", "nse", "sec_list.csv")
PHASE0 = os.path.join(FIX, "phase0")
CAL = os.path.join(FIX, "market", "holidays-test.json")
SHIPPED_CAL = os.path.join(os.path.dirname(FIX), "..", "data", "nse_holidays.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("NSE_TRADER_DIR")
        os.environ["NSE_TRADER_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("NSE_TRADER_DIR", None)
        else:
            os.environ["NSE_TRADER_DIR"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_mod(self, mod, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(argv)
        return code, out.getvalue()

    def check(self, sym, now, *extra, data=FIVE, cmd="check"):
        return self.run_mod(entry, [cmd, sym, "--offline", data, "--now", now, "--holidays", CAL] + list(extra))

    def status(self, sym, now, *extra, data=FIVE):
        code, out = self.check(sym, now, "--json", "--no-write", *extra, data=data)
        return json.loads(out)

    def config(self, **kw):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump(kw, f)

    def read(self, name):
        with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
            return json.load(f)


class States(Base):
    def test_forming_before_0930(self):
        r = self.status("INFY", "2026-11-05 09:25")
        self.assertEqual(r["status"], "forming")
        self.assertIn("range_so_far", r)

    def test_waiting_says_how_far(self):
        r = self.status("INFY", "2026-11-05 09:38")
        self.assertEqual((r["status"], r["level"], r["last_close"], r["distance_pct"]),
                         ("waiting", 1154.7, 1151.2, 0.3))

    def test_triggered_on_real_infy(self):
        code, out = self.check("INFY", "2026-11-05 10:15")
        self.assertEqual(code, 0, out)
        e = self.read("entry.json")
        # Same numbers as the scanner and as the hand check on the Phase 0 chart.
        self.assertEqual((e["or_high"], e["or_low"], e["trigger_bar"], e["trigger_time"], e["entry_price"]),
                         (1154.7, 1144.9, "09:35", "09:40", 1160.5))
        self.assertEqual((e["status"], e["tick"], e["volume_ratio"]), ("triggered", 0.05, 2.23))
        self.assertIn("Nothing was placed", out)
        self.assertIn("/nse-exit", out)

    def test_entry_agrees_with_the_scanner_on_every_hit(self):
        code, out = self.run_mod(scan, ["orb", "--offline", FIVE, "--now", "2026-11-05 14:25", "--holidays", CAL,
                                        "--symbols", "INFY,TCS,SBIN,HDFCBANK,RELIANCE,ITC,M&M,BAJAJ-AUTO", "--quiet"])
        hits = {h["symbol"]: h for h in self.read("orb_hits.json")["hits"]}
        self.assertGreater(len(hits), 3)
        for sym in ("INFY.NS", "TCS.NS", "SBIN.NS", "HDFCBANK.NS", "RELIANCE.NS", "ITC.NS"):
            r = self.status(sym.split(".")[0], "2026-11-05 14:25")
            if sym in hits:
                self.assertEqual((r["status"], r["trigger_time"], r["entry_price"]),
                                 ("triggered", hits[sym]["hit_time"], hits[sym]["hit_close"]), sym)
            else:
                self.assertNotEqual(r["status"], "triggered", sym)

    def test_expired_when_no_close_above_by_no_entries_after(self):
        for sym in ("HDFCBANK", "RELIANCE", "ITC"):
            r = self.status(sym, "2026-11-05 14:30")
            self.assertEqual(r["status"], "expired", sym)

    def test_a_trigger_at_no_entries_after_is_not_an_entry(self):
        self.config(no_entries_after="09:40")       # INFY's trigger bar ends exactly at 09:40
        r = self.status("INFY", "2026-11-05 10:15")
        self.assertEqual(r["status"], "expired")

    def test_failed_breakout_is_said(self):
        r = self.status("BIRLACABLE", "2026-11-05 11:00", "--setup", "level", "--level", "340")
        self.assertEqual((r["status"], r["trigger_time"], r["entry_price"]), ("triggered", "10:00", 340.25))
        self.assertIn("failed so far", r["reason"])

    def test_volume_filter_and_its_wording(self):
        r = self.status("INFY", "2026-11-05 14:00", "--min-volume-x", "1.5")
        self.assertEqual(r["status"], "triggered")                       # the 09:35 bar had 2.23 x
        r = self.status("INFY", "2026-11-05 14:00", "--min-volume-x", "3")
        self.assertEqual(r["status"], "waiting")
        self.assertIn("above the level", r["reason"])
        self.assertIn("3 x the day's average volume", r["reason"])


class Data(Base):
    def test_null_midday_bars_make_the_chart_unusable(self):
        r = self.status("SBIN", "2026-10-15 11:56", data=PHASE0)
        self.assertEqual(r["status"], "unusable")
        self.assertIn("11:45,11:50", r["reason"])

    def test_only_the_newest_bar_missing_is_yahoo_lag(self):
        r = self.status("SBIN", "2026-10-15 11:52", data=PHASE0)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("11:45 bar yet", r["data_note"])

    def test_symbol_not_in_data(self):
        r = self.status("ZZZZ", "2026-11-05 10:15")
        self.assertEqual(r["status"], "unusable")


class Calendar(Base):
    def test_closed_days(self):
        for now, want in (("2026-11-07 10:15", "Saturday"), ("2026-10-02 10:15", "trading holiday"),
                          ("2026-11-08 10:15", "special session")):
            r = self.status("INFY", now)
            self.assertEqual(r["status"], "closed", now)
            self.assertIn(want, r["reason"])

    def test_shipped_calendar_refuses(self):
        code, out = self.run_mod(entry, ["check", "INFY", "--offline", FIVE, "--now", "2026-11-05 10:15",
                                         "--holidays", SHIPPED_CAL])
        self.assertEqual(code, 1)
        self.assertIn("official circular", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "entry.json")))

    def test_before_open(self):
        self.assertEqual(self.status("INFY", "2026-11-05 09:00")["status"], "not_open")


class Circuit(Base):
    def daily_at_nine(self):
        self.config(min_price_inr=0, min_traded_value_inr=1)
        self.run_mod(scan, ["universe", "--csv", WALL, "--bands", BANDS, "--quiet"])
        self.run_mod(scan, ["daily", "--offline", DAILY, "--now", "2026-11-05 09:00", "--quiet"])

    def test_limits_from_the_daily_watchlist(self):
        self.daily_at_nine()
        r = self.status("BIRLACABLE", "2026-11-05 11:00", "--setup", "level", "--level", "340")
        self.assertEqual(r["limits"], {"lower": 313.6, "upper": 346.6})
        self.assertEqual(r["prev_close_source"], "daily watchlist")

    def test_touched_upper_circuit_says_skip(self):
        self.daily_at_nine()
        r = self.status("BIRLACABLE", "2026-11-05 14:25")
        self.assertEqual(r["status"], "circuit")
        self.assertIn("upper circuit", r["reason"])


class Watch(Base):
    def test_watch_steps_bar_by_bar_to_the_trigger(self):
        code, out = self.check("TCS", "2026-11-05 09:00", cmd="watch")
        self.assertEqual(code, 0, out)
        for want in ("[09:00] TCS NOT_OPEN", "[09:20] TCS FORMING", "[09:30] TCS WAITING", "[09:50] TCS TRIGGERED"):
            self.assertIn(want, out)
        self.assertEqual(self.read("entry.json")["trigger_time"], "09:50")

    def test_watch_stops_at_until(self):
        code, out = self.check("HDFCBANK", "2026-11-05 09:30", "--until", "10:00", cmd="watch")
        self.assertEqual(code, 1)
        self.assertIn("Stopped watching at 10:00", out)

    def test_watch_ends_on_expiry(self):
        code, out = self.check("ITC", "2026-11-05 14:00", cmd="watch")
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", out)


class Watchlist(Base):
    def test_checks_every_scanner_hit(self):
        self.run_mod(scan, ["orb", "--offline", FIVE, "--now", "2026-11-05 10:15", "--holidays", CAL,
                            "--symbols", "INFY,TCS,HDFCBANK", "--quiet"])
        code, out = self.run_mod(entry, ["watchlist", "--offline", FIVE, "--now", "2026-11-05 10:15", "--holidays", CAL])
        self.assertEqual(code, 0)
        self.assertIn("INFY", out)
        self.assertIn("Pick one", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "entry.json")))


class Usage(Base):
    def test_bad_arguments(self):
        code, out = self.check("INFY", "2026-11-05 10:15", "--setup", "level")
        self.assertIn("--level", out)
        code, out = self.run_mod(entry, ["check", "INFY", "--offline", FIVE, "--holidays", CAL])
        self.assertIn("--now", out)
        code, out = self.check("BRK.B", "2026-11-05 10:15")
        self.assertIn("only NSE symbols", out)


if __name__ == "__main__":
    unittest.main()
