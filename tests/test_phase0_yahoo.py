"""Phase 0: facts about Yahoo's NSE 5-minute data, pinned on real saved charts.

Fixtures: tests/fixtures/phase0/<SYMBOL>-5m-60d.json, fetched 2026-11-05 17:16 IST
from query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=60d&interval=5m.
Later phases rely on these facts; if Yahoo changes its feed, these tests say what changed.
Run: python -m unittest discover -s tests -v
"""

import datetime as dt
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "phase0")
SYMBOLS = ("RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))  # moves to shared/market.py in Phase 1


def load(sym):
    with open(os.path.join(FIX, "%s-5m-60d.json" % sym), encoding="utf-8") as f:
        res = json.load(f)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(res["timestamp"]):
        bars.append((dt.datetime.fromtimestamp(t, IST),
                     {k: q[k][i] for k in ("open", "high", "low", "close", "volume")}))
    return res["meta"], bars


def by_day(bars):
    days = {}
    for when, b in bars:
        days.setdefault(when.date(), []).append((when, b))
    return days


class YahooNseFacts(unittest.TestCase):
    def test_meta_says_nse_in_ist_and_inr(self):
        for sym in SYMBOLS:
            meta, _ = load(sym)
            self.assertEqual(meta["symbol"], sym)            # .NS kept as-is, no "." -> "-"
            self.assertEqual(meta["exchangeName"], "NSI")
            self.assertEqual(meta["exchangeTimezoneName"], "Asia/Kolkata")
            self.assertEqual(meta["gmtoffset"], 19800)        # +05:30
            self.assertEqual(meta["currency"], "INR")

    def test_every_day_is_75_bars_from_0915_to_1525(self):
        for sym in SYMBOLS:
            days = by_day(load(sym)[1])
            self.assertEqual(len(days), 60, sym)              # range=60d means 60 trading days
            for day, rows in days.items():
                self.assertEqual(len(rows), 75, (sym, day))
                self.assertEqual(rows[0][0].strftime("%H:%M"), "09:15", (sym, day))
                self.assertEqual(rows[-1][0].strftime("%H:%M"), "15:25", (sym, day))
                self.assertTrue(all(w.minute % 5 == 0 and w.second == 0 for w, _ in rows))

    def test_history_spans_aug_11_to_nov_5(self):
        days = sorted(by_day(load("RELIANCE.NS")[1]))
        self.assertEqual((str(days[0]), str(days[-1])), ("2026-08-11", "2026-11-05"))

    def test_weekdays_without_bars_match_across_stocks(self):
        # Market-wide closed weekdays in the window. Check them against NSE's
        # holiday circular in Phase 1; this test only says Yahoo has no bars.
        for sym in SYMBOLS:
            days = set(by_day(load(sym)[1]))
            cur, gaps = min(days), []
            while cur <= max(days):
                if cur.weekday() < 5 and cur not in days:
                    gaps.append(str(cur))
                cur += dt.timedelta(days=1)
            self.assertEqual(gaps, ["2026-09-14", "2026-10-02", "2026-10-20"], sym)

    def test_0915_bar_has_prices_but_almost_never_volume(self):
        # 295 of 300 stock-days have volume 0 on the 09:15 bar. Unreliable -> never use it.
        zero = total = 0
        for sym in SYMBOLS:
            for day, rows in by_day(load(sym)[1]).items():
                first = rows[0][1]
                self.assertIsNotNone(first["close"], (sym, day))
                total += 1
                zero += first["volume"] == 0
        self.assertEqual((zero, total), (295, 300))

    def test_null_bars_are_the_closing_minutes_plus_one_midday_gap(self):
        for sym in SYMBOLS:
            for day, rows in by_day(load(sym)[1]).items():
                nulls = [w.strftime("%H:%M") for w, b in rows if b["close"] is None]
                allowed = {"15:15", "15:20", "15:25"}
                if str(day) == "2026-10-15":
                    allowed |= {"11:45", "11:50"}         # same gap in all five stocks
                self.assertTrue(set(nulls) <= allowed, (sym, day, nulls))
                # Nothing null before 15:10 (the default flat_by) except the Oct 15 gap.
                early = [n for n in nulls if n < "15:10" and str(day) != "2026-10-15"]
                self.assertEqual(early, [], (sym, day))

    def test_prices_sit_on_the_005_tick_after_rounding_float_noise(self):
        for sym in SYMBOLS:
            for _, b in load(sym)[1]:
                for k in ("open", "high", "low", "close"):
                    p = b[k]
                    if p is not None:
                        self.assertAlmostEqual(round(p / 0.05) * 0.05, p, delta=0.001)

    def test_summed_5m_volume_undercounts_the_daily_volume(self):
        # So traded-value filters must use daily bars, not summed 5-minute bars.
        for sym in SYMBOLS:
            meta, bars = load(sym)
            today = sum(b["volume"] or 0 for w, b in bars if str(w.date()) == "2026-11-05")
            ratio = today / meta["regularMarketVolume"]
            self.assertTrue(0.8 < ratio < 1.0, (sym, ratio))


if __name__ == "__main__":
    unittest.main()
