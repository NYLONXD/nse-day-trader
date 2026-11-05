"""Phase 2: nse-scanner on real saved data (NSE files and Yahoo charts of 2026-11-05)."""

import datetime as dt
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load_skill, market as M

import yahoo

scan = load_skill("nse-scanner", "scan")

SDIR = os.path.join(FIX, "nse-scanner")
NSE = os.path.join(SDIR, "nse")
DAILY = os.path.join(SDIR, "wall-1d-3mo-2026-11-05.json.gz")   # 61 real daily charts, fetched 17:33 IST
FIVE = os.path.join(SDIR, "wall-5m-5d-2026-11-05.json.gz")     # the same 61, 5-minute, Oct 30 - Nov 5
SPARK5 = os.path.join(SDIR, "spark-5m-1d-2026-11-05.json")     # real spark answer, 20 symbols
SPARK1D = os.path.join(SDIR, "spark-1d-3mo-2026-11-05.json")
WALL = os.path.join(SDIR, "wall-symbols.txt")                   # Nifty 50 + 11 banded stocks
CAL = os.path.join(FIX, "market", "holidays-test.json")
CIRCUIT_UP = ["BIRLACABLE", "VENUSREM", "ZENITHEXPO", "KOTIC", "WEBELSOLAR"]
CIRCUIT_DOWN = ["SETL", "ONEGLOBAL", "PASHUPATI", "ELLEN", "AUGMONT"]


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

    def run_scan(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = scan.main(list(argv) + ["--quiet"])
        return code, out.getvalue()

    def config(self, **kw):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump(kw, f)

    def read(self, name):
        with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
            return json.load(f)

    def universe(self, path=WALL):
        code, out = self.run_scan("universe", "--csv", path, "--bands", os.path.join(NSE, "sec_list.csv"))
        self.assertEqual(code, 0, out)


class Universe(Base):
    def test_equity_l_keeps_eq_drops_trade_for_trade_and_gsm(self):
        code, out = self.run_scan("universe", "--csv", os.path.join(NSE, "EQUITY_L.csv"),
                                  "--bands", os.path.join(NSE, "sec_list.csv"))
        self.assertEqual(code, 0)
        doc = self.read("universe.json")
        self.assertEqual(doc["kind"], "EQUITY_L")
        self.assertEqual(doc["count"], 2365)
        self.assertEqual(doc["dropped"]["series"], {"BE": 204, "BZ": 33})
        self.assertEqual(len(doc["dropped"]["gsm"]), 28)
        syms = {e["symbol"]: e for e in doc["symbols"]}
        self.assertIn("M&M.NS", syms)
        self.assertIn("BAJAJ-AUTO.NS", syms)          # NSE's hyphen kept, no "." -> "-" games
        self.assertEqual(syms["RELIANCE.NS"]["band"], "No Band")
        self.assertEqual(syms["20MICRONS.NS"]["band"], "20")

    def test_nifty500_list(self):
        code, _ = self.run_scan("universe", "--csv", os.path.join(NSE, "ind_nifty500list.csv"), "--no-bands")
        doc = self.read("universe.json")
        self.assertEqual((doc["kind"], doc["count"]), ("index list", 496))
        self.assertEqual(doc["dropped"]["series"], {"BE": 2, "RR": 3})
        self.assertIsNone(doc["symbols"][0]["band"])   # no band file -> unknown, not invented

    def test_plain_list_and_bad_files(self):
        self.universe()
        self.assertEqual(self.read("universe.json")["count"], 61)
        p = os.path.join(self.tmp, "x.csv")
        with open(p, "w") as f:
            f.write("Ticker,Name\nRELIANCE,Reliance\n")
        code, out = self.run_scan("universe", "--csv", p, "--no-bands")
        self.assertEqual(code, 1)
        self.assertIn("no SYMBOL column", out)
        code, out = self.run_scan("universe", "--csv", os.path.join(self.tmp, "missing.csv"), "--no-bands")
        self.assertIn("cannot read", out)

    def test_download_refusal_tells_user_to_use_the_browser(self):
        def refuse(url, timeout=20):
            raise OSError("HTTP Error 403: Forbidden")
        old = yahoo.http_get
        yahoo.http_get = refuse
        try:
            code, out = self.run_scan("universe", "--no-bands")
        finally:
            yahoo.http_get = old
        self.assertEqual(code, 1)
        self.assertIn("Download it in your browser", out)
        self.assertIn("--csv" if "--csv" in out else "path", out)


class Daily(Base):
    def test_real_wall_after_the_close(self):
        self.universe()
        code, out = self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 17:40")
        self.assertEqual(code, 0, out)
        doc = self.read("watchlist.json")
        self.assertEqual((doc["universe"], doc["fetched"], doc["last_session"]), (61, 61, "2026-11-05"))
        self.assertEqual(doc["skipped"]["low_price"], 1)                 # TAKE at Rs. 11.99
        self.assertEqual(doc["skipped"]["low_traded_value"], 6)
        skipped = sorted(M.bare_symbol(c["symbol"]) for c in doc["skipped"]["circuit"])
        self.assertEqual(skipped, ["AUGMONT", "BIRLACABLE", "ELLEN", "SETL"])
        self.assertEqual(doc["count"], 50)
        top = doc["hits"][0]
        self.assertEqual(top["symbol"], "HDFCBANK.NS")
        self.assertEqual(top["close"], 760.0)
        self.assertEqual(top["band"], "No Band")
        rel = [h for h in doc["hits"] if h["symbol"] == "RELIANCE.NS"][0]
        self.assertEqual((rel["tick"], rel["close"]), (0.1, 1313.4))
        self.assertIn("near_20d_high", rel["setups"])
        self.assertIn("Rs. 10.00 cr", out)

    def test_todays_bar_is_left_out_before_1600(self):
        self.universe()
        self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 15:00")
        self.assertEqual(self.read("watchlist.json")["last_session"], "2026-11-04")

    def test_every_real_circuit_lock_is_caught_with_bands(self):
        self.config(min_price_inr=0, min_traded_value_inr=1)
        self.universe()
        self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 17:40")
        doc = self.read("watchlist.json")
        got = {M.bare_symbol(c["symbol"]): c["reason"] for c in doc["skipped"]["circuit"]}
        for s in CIRCUIT_UP + ["TAKE"]:
            self.assertIn("upper circuit", got.get(s, ""), s)
        for s in CIRCUIT_DOWN:
            self.assertIn("lower circuit", got.get(s, ""), s)
        self.assertEqual(len(got), 11)
        self.assertFalse(any(h["symbol"] == "BIRLACABLE.NS" for h in doc["hits"]))

    def test_circuit_guessed_when_band_unknown(self):
        self.config(min_price_inr=0, min_traded_value_inr=1)
        code, out = self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 17:40",
                                  "--symbols", ",".join(CIRCUIT_UP + CIRCUIT_DOWN + ["RELIANCE"]))
        doc = self.read("watchlist.json")
        got = {M.bare_symbol(c["symbol"]) for c in doc["skipped"]["circuit"]}
        self.assertEqual(got, set(CIRCUIT_UP + CIRCUIT_DOWN))
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["RELIANCE.NS"])
        self.assertEqual(doc["hits"][0]["band"], "unknown")

    def test_next_session_limits_match_where_stocks_really_locked(self):
        # Limits for Nov 5 from Nov 4 closes vs the price each stock locked at on Nov 5.
        self.config(min_price_inr=0, min_traded_value_inr=1)
        self.universe()
        self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 09:00")
        hits = {M.bare_symbol(h["symbol"]): h for h in self.read("watchlist.json")["hits"]}
        locked = {"AUGMONT": ("lower", 970.0), "SETL": ("lower", 396.75), "ELLEN": ("lower", 312.8),
                  "ONEGLOBAL": ("lower", 395.75), "BIRLACABLE": ("upper", 346.6), "WEBELSOLAR": ("upper", 72.0)}
        nov5 = yahoo.load_offline(DAILY)
        for sym, (side, price) in locked.items():
            self.assertEqual(hits[sym]["last_session"], "2026-11-04")
            self.assertEqual(hits[sym]["next_session_limits"][side], price, sym)
            close = [b.c for b in nov5[sym + ".NS"].bars if b.t.date() == dt.date(2026, 11, 5)][0]
            self.assertAlmostEqual(close, price, places=2)

    def test_missing_universe(self):
        code, out = self.run_scan("daily", "--offline", DAILY, "--now", "2026-11-05 17:40")
        self.assertEqual(code, 1)
        self.assertIn("run: scan.py universe", out)

    def test_offline_needs_now(self):
        code, out = self.run_scan("daily", "--offline", DAILY, "--symbols", "SBIN")
        self.assertIn("--now", out)


class Orb(Base):
    def orb(self, now, *extra):
        return self.run_scan("orb", "--offline", FIVE, "--now", now, "--holidays", CAL, *extra)

    def watchlist(self, now="2026-11-05 17:40"):
        self.universe()
        self.run_scan("daily", "--offline", DAILY, "--now", now)

    def test_real_breakouts_at_1015(self):
        self.watchlist()
        code, out = self.orb("2026-11-05 10:15")
        self.assertEqual(code, 0, out)
        doc = self.read("orb_hits.json")
        self.assertEqual((doc["scanned"], doc["usable"], doc["candidates"], doc["count"]), (50, 50, 31, 22))
        self.assertEqual(doc["hits"][0]["hit_time"], "09:35")
        infy = [h for h in doc["hits"] if h["symbol"] == "INFY.NS"][0]
        # Checked by hand on the separately fetched Phase 0 chart.
        self.assertEqual((infy["or_high"], infy["or_low"], infy["hit_bar"], infy["hit_time"], infy["hit_close"]),
                         (1154.7, 1144.9, "09:35", "09:40", 1160.5))
        self.assertEqual(infy["over_pct"], 0.5)
        for h in doc["hits"]:
            self.assertGreater(h["hit_close"], h["or_high"])
            self.assertGreaterEqual(h["hit_time"], "09:35")
            self.assertLessEqual(h["hit_time"], "10:15")
            self.assertTrue(M.on_tick(h["or_high"], h["tick"]))
        self.assertIn("22 of 50 usable charts", out)

    def test_later_cutoff_only_adds_hits(self):
        self.watchlist()
        self.orb("2026-11-05 10:15")
        early = {h["symbol"]: h for h in self.read("orb_hits.json")["hits"]}
        self.orb("2026-11-05 14:00", "--at", "11:00")
        late = {h["symbol"]: h for h in self.read("orb_hits.json")["hits"]}
        self.assertEqual(len(late), 24)
        for s, h in early.items():
            self.assertEqual(late[s]["hit_time"], h["hit_time"])

    def test_spark_stage_never_loses_a_real_breakout(self):
        spark = yahoo.load_offline(SPARK5)
        charts = yahoo.load_offline(FIVE)
        day, cutoff = dt.date(2026, 11, 5), M.ist(2026, 11, 5, 14, 30)
        cfg = {"min_price_inr": 0, "no_entries_after": "14:30", "flat_by": "15:10"}
        self.assertEqual(len(spark), 20)
        self.assertIsNone(spark["HDFCBANK.NS"].bars[0].h)                   # closes only
        checked = 0
        for sym, s in spark.items():
            hit, _ = scan.orb_confirm(sym, charts[sym], day, cutoff, cfg, {}, None, None)
            if hit:
                checked += 1
                self.assertTrue(scan.orb_candidate(s, day, cutoff), sym)
        self.assertGreater(checked, 5)

    def test_intraday_circuit_touch_is_skipped(self):
        self.config(min_price_inr=0, min_traded_value_inr=1)
        self.watchlist("2026-11-05 09:00")                                 # limits for Nov 5
        self.orb("2026-11-05 10:15")
        doc = self.read("orb_hits.json")
        self.assertEqual(sorted(M.bare_symbol(s) for s in doc["skipped_circuit"]),
                         ["AUGMONT", "ONEGLOBAL", "WEBELSOLAR"])
        self.assertIn("upper circuit", doc["skipped_circuit"]["WEBELSOLAR.NS"])

    def test_refusals(self):
        self.watchlist()
        cases = [("2026-11-05 09:32", "Too early"), ("2026-11-05 14:45", "no_entries_after"), ("2026-11-05 14:30", "no_entries_after"),
                 ("2026-11-07 10:15", "Saturday"), ("2026-11-08 10:15", "special session"),
                 ("2026-10-02 10:15", "trading holiday"), ("2027-01-04 10:15", "No NSE holiday list for 2027")]
        for now, want in cases:
            code, out = self.orb(now)
            self.assertEqual(code, 1, now)
            self.assertIn(want, out, now)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "orb_hits.json")))

    def test_shipped_calendar_refuses_until_filled(self):
        code, out = self.run_scan("orb", "--offline", FIVE, "--now", "2026-11-05 10:15", "--symbols", "SBIN",
                                  "--holidays", os.path.join(FIX, "market", "holidays-empty.json"))
        self.assertEqual(code, 1)
        self.assertIn("official circular", out)

    def test_null_bars_make_a_chart_unusable(self):
        # 2026-10-15: Yahoo has null 11:45 and 11:50 bars in all five Phase 0 charts.
        code, out = self.run_scan("orb", "--offline", os.path.join(FIX, "phase0"), "--now", "2026-10-15 12:30",
                                  "--holidays", CAL, "--symbols", "RELIANCE,TCS,INFY,HDFCBANK,SBIN",
                                  "--max-confirm", "0")
        doc = self.read("orb_hits.json")
        self.assertEqual(doc["usable"], 5 - doc["candidates"])
        code, out = self.run_scan("orb", "--offline", os.path.join(FIX, "phase0"), "--now", "2026-10-15 11:40",
                                  "--holidays", CAL, "--symbols", "RELIANCE,TCS,INFY,HDFCBANK,SBIN")
        self.assertEqual(self.read("orb_hits.json")["usable"], 5)

    def test_partial_is_said(self):
        self.watchlist()
        code, out = self.orb("2026-11-05 10:15", "--max-confirm", "5")
        doc = self.read("orb_hits.json")
        self.assertEqual((doc["confirmed"], doc["partial"]), (5, 26))
        self.assertIn("PARTIAL", out)

    def test_symbols_without_watchlist(self):
        code, out = self.orb("2026-11-05 10:15", "--symbols", "INFY,M&M,BAJAJ-AUTO", "--no-write")
        self.assertEqual(code, 0, out)
        self.assertIn("INFY", out)


if __name__ == "__main__":
    unittest.main()
