"""Phase 1: shared/market.py, the one home of every NSE market rule."""

import ast
import datetime as dt
import json
import os
import tempfile
import unittest
from decimal import Decimal

from _load import FIX, ROOT, market, phase0_chart

M = market
SYMBOLS = ("RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS")
TEST_CAL = os.path.join(FIX, "market", "holidays-test.json")


class Time(unittest.TestCase):
    def test_ist_is_fixed_0530_all_year(self):
        for month in (1, 3, 4, 7, 10, 11, 12):   # US/EU DST dates change nothing here
            self.assertEqual(M.ist(2026, month, 2, 10).utcoffset(), dt.timedelta(hours=5, minutes=30))

    def test_yahoo_session_start_timestamp_is_0915_ist(self):
        meta, _ = phase0_chart("RELIANCE.NS")
        reg = meta["currentTradingPeriod"]["regular"]
        self.assertEqual(M.to_ist(reg["start"]), M.ist(2026, 11, 5, 9, 15))
        self.assertEqual(M.to_ist(reg["end"]), M.ist(2026, 11, 5, 15, 30))

    def test_utc_converts_and_naive_is_refused(self):
        utc = dt.datetime(2026, 11, 5, 3, 45, tzinfo=dt.timezone.utc)
        self.assertEqual(M.to_ist(utc), M.ist(2026, 11, 5, 9, 15))
        with self.assertRaises(M.MarketError):
            M.to_ist(dt.datetime(2026, 11, 5, 9, 15))

    def test_parsing(self):
        self.assertEqual(M.parse_hhmm("9:30"), dt.time(9, 30))
        self.assertEqual(M.parse_ist("2026-11-05 10:15"), M.ist(2026, 11, 5, 10, 15))
        self.assertEqual(M.epoch(M.ist(2026, 11, 5, 9, 15)), 1793850300)
        for bad in ("25:00", "10:5", "10:15:00", "ten"):
            with self.assertRaises(M.MarketError):
                M.parse_hhmm(bad)


class Session(unittest.TestCase):
    DAY = dt.date(2026, 11, 5)

    def test_75_bars_0915_to_1525(self):
        bars = M.bar_starts(self.DAY)
        self.assertEqual(len(bars), 75)
        self.assertEqual((M.fmt_time(bars[0]), M.fmt_time(bars[-1])), ("09:15", "15:25"))

    def test_opening_range_is_three_bars_ending_0930(self):
        self.assertEqual([M.fmt_time(b) for b in M.opening_range_bar_starts(self.DAY)],
                         ["09:15", "09:20", "09:25"])
        self.assertEqual(M.OPENING_RANGE_END, dt.time(9, 30))

    def test_every_real_yahoo_bar_is_on_the_session_grid(self):
        for sym in SYMBOLS:
            _, bars = phase0_chart(sym)
            self.assertTrue(all(M.is_bar_start(w) for w, _ in bars), sym)
        self.assertFalse(M.is_bar_start(M.ist(2026, 11, 5, 10, 58, 7)))   # a live tick
        self.assertFalse(M.is_bar_start(M.ist(2026, 11, 5, 9, 10)))       # pre-open

    def test_bar_complete_only_after_it_ends(self):
        b = M.ist(2026, 11, 5, 9, 25)
        self.assertFalse(M.bar_complete(b, M.ist(2026, 11, 5, 9, 29, 59)))
        self.assertTrue(M.bar_complete(b, M.ist(2026, 11, 5, 9, 30)))

    def test_phases(self):
        cases = [("09:00", "before_open"), ("09:15", "opening_range"), ("09:29", "opening_range"),
                 ("09:30", "entries_open"), ("14:29", "entries_open"), ("14:30", "manage_only"),
                 ("15:09", "manage_only"), ("15:10", "flat"), ("15:30", "closed")]
        for hhmm, want in cases:
            now = M.parse_ist("2026-11-05 " + hhmm)
            self.assertEqual(M.session_phase(now, "14:30", "15:10"), want, hhmm)

    def test_config_times_checked_against_the_session(self):
        self.assertEqual(M.validate_session_times("14:30", "15:10"), [])
        self.assertEqual(len(M.validate_session_times("09:25", "15:10")), 1)   # inside the range
        self.assertEqual(len(M.validate_session_times("14:30", "14:30")), 1)   # flat before entries end
        self.assertEqual(len(M.validate_session_times("14:30", "15:20")), 1)   # unreliable bars
        self.assertEqual(len(M.validate_session_times("14:32", "15:10")), 1)   # off the bar grid
        self.assertEqual(len(M.validate_session_times("2pm", "15:10")), 1)


class CalendarRules(unittest.TestCase):
    def setUp(self):
        self.cal = M.load_calendar(TEST_CAL)

    def test_normal_day(self):
        st = self.cal.status(dt.date(2026, 11, 5))
        self.assertTrue(st.tradable)
        self.assertEqual(st.kind, "trading")

    def test_weekend(self):
        st = self.cal.status(dt.date(2026, 11, 7))
        self.assertEqual((st.tradable, st.kind), (False, "weekend"))
        self.assertIn("Saturday", st.reason)

    def test_holiday_matches_a_day_yahoo_has_no_bars_for(self):
        st = self.cal.status(dt.date(2026, 10, 2))
        self.assertEqual((st.tradable, st.kind), (False, "holiday"))
        self.assertIn("test holiday B", st.reason)

    def test_special_sessions_refused_weekend_or_weekday(self):
        for d in (dt.date(2026, 11, 8), dt.date(2026, 11, 6)):
            st = self.cal.status(d)
            self.assertEqual((st.tradable, st.kind), (False, "special"), d)
            self.assertIn("special session", st.reason)

    def test_year_not_filled_in_is_refused(self):
        st = self.cal.status(dt.date(2027, 1, 4))
        self.assertEqual((st.tradable, st.kind), (False, "unknown_year"))
        self.assertIn("2027", st.reason)

    def test_previous_trading_day_skips_holiday_and_weekend(self):
        self.assertEqual(self.cal.previous_trading_day(dt.date(2026, 10, 5)), dt.date(2026, 10, 1))
        self.assertIsNone(self.cal.previous_trading_day(dt.date(2026, 1, 1)))   # 2025 unknown

    def test_calendar_agrees_with_real_yahoo_days(self):
        _, bars = phase0_chart("SBIN.NS")
        days = sorted({w.date() for w, _ in bars})
        cur = days[0]
        while cur <= days[-1]:
            self.assertEqual(self.cal.is_trading_day(cur), cur in days, cur)
            cur += dt.timedelta(days=1)

    def test_shipped_file_is_valid_and_refuses_until_filled(self):
        cal = M.load_calendar(os.path.join(ROOT, "data", "nse_holidays.json"))
        for y in cal.years_covered:
            self.assertTrue(cal.sources.get(y), y)
        if 2026 not in cal.years_covered:
            st = cal.status(dt.date(2026, 11, 5))
            self.assertEqual(st.kind, "unknown_year")
            self.assertIn("official circular", st.reason)

    def test_shipped_list_agrees_with_real_yahoo_days(self):
        """Your filled list vs the real saved charts: Yahoo has bars exactly on the open days."""
        cal = M.load_calendar(os.path.join(ROOT, "data", "nse_holidays.json"))
        _, bars = phase0_chart("SBIN.NS")
        days = sorted({w.date() for w, _ in bars})
        if days[0].year not in cal.years_covered:
            self.skipTest("data/nse_holidays.json does not cover %d yet" % days[0].year)
        cur = days[0]
        while cur <= days[-1]:
            self.assertEqual(cal.is_trading_day(cur), cur in days, cur)
            cur += dt.timedelta(days=1)

    def _bad(self, doc):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(doc, f)
        try:
            with self.assertRaises(M.MarketError) as cm:
                M.load_calendar(f.name)
            return str(cm.exception)
        finally:
            os.remove(f.name)

    def test_bad_files_are_refused_with_a_reason(self):
        self.assertIn("source", self._bad({"years_covered": [2026], "source": {}}))
        self.assertIn("not in years_covered", self._bad({
            "years_covered": [], "holidays": [{"date": "2026-10-02", "name": "x"}]}))
        self.assertIn("YYYY-MM-DD", self._bad({
            "years_covered": [2026], "source": {"2026": "c"}, "holidays": [{"date": "02/10/2026", "name": "x"}]}))
        self.assertIn("no name", self._bad({
            "years_covered": [2026], "source": {"2026": "c"}, "holidays": [{"date": "2026-10-02"}]}))


class FileLocations(unittest.TestCase):
    """A plugin is installed into Claude Code's cache: your filled lists must live in trader/."""

    def test_trader_copy_wins_over_the_shipped_one(self):
        old = os.environ.get("NSE_TRADER_DIR")
        with tempfile.TemporaryDirectory() as d:
            os.environ["NSE_TRADER_DIR"] = d
            try:
                self.assertEqual(M.load_calendar().path, M.HOLIDAYS_FILE)
                with open(TEST_CAL, encoding="utf-8") as f, open(os.path.join(d, "nse_holidays.json"), "w") as g:
                    g.write(f.read())
                cal = M.load_calendar()
                self.assertEqual(cal.path, os.path.join(d, "nse_holidays.json"))
                self.assertTrue(cal.is_trading_day(dt.date(2026, 11, 5)))
            finally:
                if old is None:
                    os.environ.pop("NSE_TRADER_DIR", None)
                else:
                    os.environ["NSE_TRADER_DIR"] = old


class Ticks(unittest.TestCase):
    def test_float_noise_is_cleaned(self):
        self.assertEqual(M.clean_price(1312.300048828125), 1312.3)
        self.assertEqual(M.clean_price(1319.199951171875), 1319.2)

    def test_yahoo_float32_noise_at_any_price(self):
        import struct
        f32 = lambda x: struct.unpack("f", struct.pack("f", x))[0]
        self.assertTrue(M.on_tick(2481.39990234375, "0.10"))          # real TCS bar
        self.assertEqual(f32(131072.35), 131072.34375)                # the worst case here
        for price, tick in ((2481.40, "0.10"), (131072.35, "0.05"), (131075.0, "5.00"), (762.65, "0.05")):
            noisy = f32(price)
            self.assertTrue(M.on_tick(noisy, tick), price)
            self.assertEqual(M.clean_price(noisy, tick), price)

    def test_off_tick_prices_are_still_caught(self):
        self.assertFalse(M.on_tick(1240.35, "0.10"))
        self.assertFalse(M.on_tick(100.01, "0.05"))
        self.assertFalse(M.on_tick(131072.30, "5.00"))

    def test_target_rounds_half_up(self):
        self.assertEqual(M.target_price(100.025), 100.05)    # a tie goes up
        self.assertEqual(M.target_price(100.024), 100.0)
        self.assertEqual(M.target_price(1240.35, "0.10"), 1240.4)
        self.assertEqual(M.target_price(1240.34, "0.10"), 1240.3)

    def test_stop_rounds_away_from_entry(self):
        self.assertEqual(M.stop_price(98.73, entry=100), 98.7)          # long stop: down
        self.assertEqual(M.stop_price(98.70, entry=100), 98.7)          # already on the tick
        self.assertEqual(M.stop_price(1240.37, 1250, "0.10"), 1240.3)
        self.assertEqual(M.stop_price(101.01, entry=100), 101.05)       # above the entry: up
        self.assertEqual(M.stop_price(99.99999999, entry=101), 100.0)   # noise never costs a tick
        with self.assertRaises(M.MarketError):
            M.stop_price(100, entry=100)

    def test_bad_prices(self):
        for bad in (-1, float("nan"), "abc", None):
            with self.assertRaises(M.MarketError):
                M.round_to_tick(bad)
        with self.assertRaises(M.MarketError):
            M.round_to_tick(100, how="sideways")

    def test_ticks_inferred_from_real_prices(self):
        want = {"RELIANCE.NS": Decimal("0.10"), "TCS.NS": Decimal("0.10"), "INFY.NS": Decimal("0.05"),
                "HDFCBANK.NS": Decimal("0.05"), "SBIN.NS": Decimal("0.05")}
        for sym, tick in want.items():
            _, bars = phase0_chart(sym)
            recent = [b[k] for _, b in bars[-375:] for k in ("open", "high", "low", "close")]  # last 5 days
            got, how = M.tick_for(sym, recent)
            self.assertEqual(got, tick, sym)
            self.assertIn("inferred", how)
            self.assertTrue(all(M.on_tick(p, got) for p in recent if p is not None), sym)

    def test_too_few_prices_and_tick_file_order(self):
        self.assertEqual(M.tick_for("RELIANCE", [1312.3] * 5)[0], Decimal("0.05"))
        self.assertIn("default", M.tick_for("RELIANCE")[1])
        over = {"RELIANCE.NS": Decimal("0.10")}
        self.assertEqual(M.tick_for("reliance", None, over), (Decimal("0.10"), "tick file"))

    def test_tick_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            self.assertEqual(M.load_tick_overrides(p), {})                 # missing file is fine
            with open(p, "w") as f:
                json.dump({"ticks": {"RELIANCE": 0.1, "TCS.NS": "0.10"}}, f)
            self.assertEqual(M.load_tick_overrides(p),
                             {"RELIANCE.NS": Decimal("0.10"), "TCS.NS": Decimal("0.10")})
            with open(p, "w") as f:
                json.dump({"ticks": {"RELIANCE": 0.07}}, f)
            with self.assertRaises(M.MarketError):
                M.load_tick_overrides(p)
        self.assertEqual(M.load_tick_overrides(os.path.join(ROOT, "data", "nse_tick_sizes.json")).get("X.NS"), None)


class Symbols(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(M.normalize_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(M.normalize_symbol(" RELIANCE.NS "), "RELIANCE.NS")
        self.assertEqual(M.normalize_symbol("bajaj-auto"), "BAJAJ-AUTO.NS")   # NSE's own hyphen kept
        self.assertEqual(M.normalize_symbol("M&M"), "M&M.NS")
        self.assertEqual(M.normalize_symbol("3MINDIA"), "3MINDIA.NS")
        self.assertEqual(M.bare_symbol("SBIN.NS"), "SBIN")

    def test_dot_is_never_turned_into_a_dash(self):
        with self.assertRaises(M.MarketError):
            M.normalize_symbol("BRK.B")     # US style: refused, not rewritten to BRK-B.NS
        with self.assertRaises(M.MarketError):
            M.normalize_symbol("RELIANCE.BO")
        for bad in ("", "RELIANCE NS", "$ABC", "A" * 21):
            with self.assertRaises(M.MarketError):
                M.normalize_symbol(bad)

    def test_url_symbol(self):
        self.assertEqual(M.url_symbol("M&M"), "M%26M.NS")
        self.assertEqual(M.url_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")

    def test_real_yahoo_symbols_round_trip(self):
        for sym in SYMBOLS:
            self.assertEqual(M.normalize_symbol(phase0_chart(sym)[0]["symbol"]), sym)


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(M.fmt_inr(1234567.5), "Rs. 12,34,567.50")
        self.assertEqual(M.fmt_inr(999), "Rs. 999.00")
        self.assertEqual(M.fmt_inr(1000), "Rs. 1,000.00")
        self.assertEqual(M.fmt_inr(123456789012), "Rs. 1,23,45,67,89,012.00")
        self.assertEqual(M.fmt_inr(-500), "-Rs. 500.00")
        self.assertEqual(M.fmt_inr(0.005), "Rs. 0.01")                 # half-up
        self.assertEqual(M.fmt_number(2500000, 0), "25,00,000")

    def test_compact(self):
        self.assertEqual(M.fmt_inr_compact(52300000), "Rs. 5.23 cr")
        self.assertEqual(M.fmt_inr_compact(420000), "Rs. 4.20 lakh")
        self.assertEqual(M.fmt_inr_compact(-99999), "-Rs. 99,999.00")


class Repo(unittest.TestCase):
    def test_python_38_syntax(self):
        for folder, _, files in os.walk(ROOT):
            if any(p in folder for p in (".git", "reference", "__pycache__")):
                continue
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as f:
                        ast.parse(f.read(), path, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
