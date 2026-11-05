"""Phase 2: shared/config.py, shared/yahoo.py, shared/nse_files.py, market circuit rules."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from decimal import Decimal

from _load import FIX, market as M

import config
import nse_files
import yahoo

SDIR = os.path.join(FIX, "nse-scanner")


class Config(unittest.TestCase):
    def write(self, doc):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(doc, f)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_defaults_are_valid(self):
        cfg, warnings = config.load(os.path.join(tempfile.gettempdir(), "no-such-config.json"))
        self.assertEqual(cfg["flat_by"], "15:10")
        self.assertEqual(cfg["no_entries_after"], "14:30")
        self.assertEqual(warnings, [])

    def test_user_values_merge_and_typos_warn(self):
        cfg, warnings = config.load(self.write({"min_price_inr": 100, "flatby": "15:00"}))
        self.assertEqual(cfg["min_price_inr"], 100)
        self.assertIn("flatby", warnings[0])

    def test_bad_values_refused(self):
        for doc, want in (({"flat_by": "15:25"}, "too late"), ({"paper": False}, "paper"),
                          ({"max_trades_per_day": 2.5}, "whole number"), ({"daily_loss_limit_inr": 100}, "below"),
                          ({"max_loss_per_trade_inr": -1}, "above 0"), ({"no_entries_after": "9:20"}, "opening range")):
            with self.assertRaises(config.ConfigError) as cm:
                config.load(self.write(doc))
            self.assertIn(want, str(cm.exception))

    def test_init_writes_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "trader", "config.json")
            self.assertTrue(config.init(p)[0])
            self.assertFalse(config.init(p)[0])
            with open(p) as f:
                self.assertEqual(json.load(f), config.DEFAULTS)


class CalendarCommand(unittest.TestCase):
    def test_says_open_or_no_for_a_day(self):
        import contextlib
        old = os.environ.get("NSE_HOLIDAYS_FILE")
        os.environ["NSE_HOLIDAYS_FILE"] = os.path.join(FIX, "market", "holidays-test.json")
        try:
            for day, code, want in (("2026-11-05", 0, "OPEN"), ("2026-10-02", 1, "trading holiday"),
                                    ("2026-11-08", 1, "special session"), ("2027-01-04", 1, "No NSE holiday list")):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(config.main(["calendar", day]), code, day)
                self.assertIn(want, out.getvalue())
        finally:
            if old is None:
                os.environ.pop("NSE_HOLIDAYS_FILE", None)
            else:
                os.environ["NSE_HOLIDAYS_FILE"] = old


class Yahoo(unittest.TestCase):
    def setUp(self):
        self.calls, self.slept = [], []
        self._get, self._sleep = yahoo.http_get, yahoo.SLEEP
        yahoo.SLEEP = self.slept.append

    def tearDown(self):
        yahoo.http_get, yahoo.SLEEP = self._get, self._sleep

    def fake(self, answers):
        def get(url, timeout=20):
            self.calls.append(url)
            a = answers.pop(0)
            if isinstance(a, int):
                raise urllib.error.HTTPError(url, a, "x", {}, io.BytesIO(b'{"chart":{"error":{"description":"No data"}}}'
                                                                       if a == 404 else b"busy"))
            return a
        yahoo.http_get = get

    def test_429_falls_back_then_waits_once(self):
        self.fake([429, 429, "ok"])
        self.assertEqual(yahoo.yahoo_get("/v8/x"), "ok")
        self.assertEqual(self.slept, [yahoo.BACKOFF])
        self.assertTrue(self.calls[0].startswith("https://query2") and self.calls[1].startswith("https://query1"))

    def test_404_json_body_is_returned_for_the_caller(self):
        self.fake([404])
        with self.assertRaises(yahoo.DataError) as cm:
            yahoo.parse_chart(yahoo.yahoo_get("/v8/x"), "NOPE")
        self.assertIn("No data", str(cm.exception))

    def test_paths_encode_ampersand_and_keep_hyphen(self):
        self.assertIn("/chart/M%26M.NS?", yahoo.chart_path("M&M", "1d", "5m"))
        self.assertIn("symbols=BAJAJ-AUTO.NS,SBIN.NS&", yahoo.spark_path(["bajaj-auto", "SBIN"], "1d", "5m"))

    def test_real_spark_answers_parse(self):
        for path in ("spark-5m-1d-2026-11-05.json", "spark-1d-3mo-2026-11-05.json"):
            got = yahoo.load_offline(os.path.join(SDIR, path))
            self.assertEqual(len(got), 20)
            self.assertIn("BAJAJ-AUTO.NS", got)
            s = got["HDFCBANK.NS"]
            self.assertTrue(all(b.h is None for b in s.bars))
            self.assertEqual(s.bars[0].t.utcoffset().total_seconds(), 19800)

    def test_real_bundle_has_61_charts_in_ist(self):
        got = yahoo.load_offline(os.path.join(SDIR, "wall-5m-5d-2026-11-05.json.gz"))
        self.assertEqual(len(got), 61)
        self.assertEqual(M.fmt_time(got["SBIN.NS"].day(got["SBIN.NS"].days()[-1])[0].t), "09:15")

    def test_spark_batches_of_20(self):
        answers = [json.dumps({"spark": {"result": []}})] * 3
        self.fake(answers)
        got, failed = yahoo.fetch_spark(["S%d" % i for i in range(45)], "1d", "5m")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(failed), 45)


class NseFiles(unittest.TestCase):
    def test_bands_from_real_sec_list(self):
        with open(os.path.join(SDIR, "nse", "sec_list.csv"), encoding="utf-8") as f:
            bands = nse_files.parse_bands(f.read())
        self.assertEqual(bands["RELIANCE.NS"]["band"], "No Band")
        self.assertEqual(bands["BIRLACABLE.NS"]["band"], "5")
        self.assertTrue(any("GSM" in v["remarks"] for v in bands.values()))

    def test_plain_list_with_header(self):
        rows, kind = nse_files.parse_listing("SYMBOL\nsbin\nM&M\n")
        self.assertEqual(([r["symbol"] for r in rows], kind), (["sbin", "M&M"], "plain list"))


class Circuits(unittest.TestCase):
    def test_band_parse(self):
        self.assertIsNone(M.parse_band("No Band"))
        self.assertEqual(M.parse_band("20"), Decimal(20))
        for bad in ("", "abc", "0", "150"):
            with self.assertRaises(M.MarketError):
                M.parse_band(bad)

    def test_limits_pull_inside_the_band(self):
        self.assertEqual(M.circuit_limits(123.45, 5), (117.3, 129.6))       # 117.2775 up, 129.6225 down
        self.assertEqual(M.circuit_limits(133.85, 10, "0.01"), (120.47, 147.23))  # PASHUPATI locked at 120.47
        self.assertEqual(M.circuit_limits(115.45, 10, "0.01")[1], 126.99)          # KOTIC locked at 126.99

    def test_side_and_guess(self):
        self.assertEqual(M.circuit_side(129.60, 123.45, 5), "upper")
        self.assertIsNone(M.circuit_side(128.0, 123.45, 5))
        self.assertEqual(M.guess_circuit(105, 100, 105, 99), ("upper", Decimal(5)))
        self.assertEqual(M.guess_circuit(105.5, 100, 105.5, 99), (None, None))   # 5.5 % is no band
        self.assertEqual(M.guess_circuit(105, 100, 106, 99), (None, None))       # fell off its high
        self.assertEqual(M.guess_circuit(90, 100, 101, 90), ("lower", Decimal(10)))

    def test_penny_stock_flat_day_is_not_a_circuit_with_its_real_tick(self):
        # FILATFASH 0.17 -> 0.17: with a 0.05 tolerance this looked "locked"; tick 0.01 fixes it.
        self.assertIsNone(M.circuit_side(0.17, 0.17, 10, "0.01"))


if __name__ == "__main__":
    unittest.main()
