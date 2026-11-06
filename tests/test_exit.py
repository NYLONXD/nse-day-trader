"""Phase 3: nse-exit on real triggers (INFY and BIRLACABLE, Thu 5 Nov 2026)."""

import io
import json
import os
import random
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load_skill, market as M

entry = load_skill("nse-entry", "entry")
exitp = load_skill("nse-exit", "exit")
scan = load_skill("nse-scanner", "scan")

FIVE = os.path.join(FIX, "nse-scanner", "wall-5m-5d-2026-11-05.json.gz")
DAILY = os.path.join(FIX, "nse-scanner", "wall-1d-3mo-2026-11-05.json.gz")
WALL = os.path.join(FIX, "nse-scanner", "wall-symbols.txt")
BANDS = os.path.join(FIX, "nse-scanner", "nse", "sec_list.csv")
CAL = os.path.join(FIX, "market", "holidays-test.json")


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

    def infy(self):
        code, out = self.run_mod(entry, ["check", "INFY", "--offline", FIVE, "--now", "2026-11-05 10:15", "--holidays", CAL])
        self.assertEqual(code, 0, out)

    def plan(self, *argv):
        code, out = self.run_mod(exitp, ["plan", "--json"] + list(argv))
        return code, json.loads(out)


class Plans(Base):
    def test_default_or_mid_and_2r(self):
        self.infy()
        code, p = self.plan()
        self.assertEqual(code, 0)
        # (1154.70 + 1144.90) / 2 = 1149.80; risk 10.70; 1160.50 + 21.40 = 1181.90
        self.assertEqual((p["entry"], p["stop"], p["target"], p["risk_per_share"], p["reward_risk"]),
                         (1160.5, 1149.8, 1181.9, 10.7, 2.0))
        self.assertEqual((p["flat_by"], p["side"], p["trigger_time"]), ("15:10", "long", "09:40"))
        self.assertIn("not a guarantee", p["stop_is_a_plan"])

    def test_or_low_and_tick_rounding(self):
        self.infy()
        code, p = self.plan("--stop", "or-low", "--target", "r:1.5")
        self.assertEqual(p["stop"], 1144.85)          # one tick under the range low 1144.90
        self.assertEqual(p["target"], 1184.0)         # 1183.975 rounds half-up to the 0.05 tick

    def test_atr_on_the_session_so_far(self):
        self.infy()
        code, p = self.plan("--stop", "atr:1.5", "--offline", FIVE)
        self.assertEqual(code, 0)
        self.assertEqual(p["stop"], 1152.0)           # 1160.50 - 1.5 x 5.64 = 1152.04, rounded down
        self.assertTrue(any("averaged the 5 bars" in n for n in p["notes"]))

    def test_write_saves_plan_json(self):
        self.infy()
        code, out = self.run_mod(exitp, ["plan", "--write"])
        self.assertEqual(code, 0)
        with open(os.path.join(self.tmp, "plan.json"), encoding="utf-8") as f:
            p = json.load(f)
        self.assertEqual((p["symbol"], p["stop"], p["target"]), ("INFY.NS", 1149.8, 1181.9))
        self.assertIn("Rs. 1,160.50", out)
        self.assertIn("auto square-off", out)

    def test_low_reward_risk_is_noted(self):
        self.infy()
        code, p = self.plan("--target", "r:1")
        self.assertEqual(code, 0)
        self.assertTrue(any("min_reward_risk" in n for n in p["notes"]))

    def test_replan_from_a_later_price_keeps_the_entry_file(self):
        # INFY triggered at 09:40 (bar 09:35) at 1160.50; the price ran on to 1165 by 10:17.
        self.infy()
        code, p = self.plan("--entry", "1165", "--now", "2026-11-05 10:17")
        self.assertEqual(code, 0)
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["trigger_bar"]), ("INFY.NS", 1165.0, 1149.8, "10:15"))
        self.assertEqual((p["or_high"], p["or_low"]), (1154.7, 1144.9))      # range kept from entry.json
        with open(os.path.join(self.tmp, "entry.json"), encoding="utf-8") as f:
            e = json.load(f)
        self.assertEqual((p["band"], p["limits"], p["tick"]), (e["band"], e["limits"], e["tick"]))   # kept too
        self.assertIn("your price, entry bar 10:15", p["entry_source"])
        self.assertIn("1,160.50 at 09:40", p["entry_source"])
        # The same with the symbol given, and an explicit entry bar.
        code, p = self.plan("--symbol", "INFY", "--entry", "1165", "--entry-bar", "10:05", "--now", "2026-11-05 10:17")
        self.assertEqual((code, p["trigger_bar"], p["stop"]), (0, "10:05", 1149.8))

    def test_replan_after_the_session_keeps_the_trigger_bar_and_says_so(self):
        self.infy()
        code, p = self.plan("--entry", "1165", "--now", "2026-11-05 17:00")
        self.assertEqual((code, p["trigger_bar"]), (0, "09:35"))
        self.assertTrue(any("--entry-bar" in n for n in p["notes"]))

    def test_replan_refusals(self):
        self.infy()
        code, out = self.run_mod(exitp, ["plan", "--entry", "1165", "--entry-bar", "09:30", "--now", "2026-11-05 10:17"])
        self.assertIn("before the trigger bar 09:35", out)
        code, out = self.run_mod(exitp, ["plan", "--entry", "1165", "--entry-bar", "10:07"])
        self.assertIn("not a 5-minute bar start", out)
        code, out = self.run_mod(exitp, ["plan", "--entry", "1165", "--entry-bar", "09:20"])
        self.assertIn("not a 5-minute bar start", out)

    def test_entry_without_symbol_or_entry_file(self):
        code, out = self.run_mod(exitp, ["plan", "--entry", "1165"])
        self.assertEqual(code, 1)
        self.assertIn("no triggered entry in trader/entry.json to re-plan", out)

    def test_other_symbol_ignores_the_entry_file(self):
        self.infy()
        code, p = self.plan("--symbol", "RELIANCE", "--entry", "1313.40", "--stop", "pct:0.7", "--entry-bar", "10:00")
        self.assertEqual((code, p["symbol"], p["trigger_bar"], p["band"]), (0, "RELIANCE.NS", "10:00", "unknown"))

    def test_manual_entry_with_a_010_tick(self):
        code, p = self.plan("--symbol", "RELIANCE", "--entry", "1313.40", "--stop", "pct:0.7", "--tick", "0.10")
        self.assertEqual(code, 0)
        self.assertEqual((p["stop"], p["target"]), (1304.2, 1331.8))   # 1304.206 down; 1331.8 on 0.10
        self.assertTrue(M.on_tick(p["stop"], "0.10") and M.on_tick(p["target"], "0.10"))


class Refusals(Base):
    def refused(self, *argv):
        code, out = self.run_mod(exitp, ["plan"] + list(argv))
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("Refused: "), out)
        return out

    def test_bad_stops_and_targets(self):
        self.infy()
        self.assertIn("at or above the entry", self.refused("--stop", "price:1170"))
        self.assertIn("within 2 ticks", self.refused("--stop", "price:1160.45"))
        self.assertIn("at or below the entry", self.refused("--target", "price:1150"))
        self.assertIn("must be one of", self.refused("--stop", "middle"))
        self.assertIn("above 0", self.refused("--stop", "pct:-1"))

    def test_target_above_the_upper_circuit_on_real_birlacable(self):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump({"min_price_inr": 0, "min_traded_value_inr": 1}, f)
        self.run_mod(scan, ["universe", "--csv", WALL, "--bands", BANDS, "--quiet"])
        self.run_mod(scan, ["daily", "--offline", DAILY, "--now", "2026-11-05 09:00", "--quiet"])
        code, out = self.run_mod(entry, ["check", "BIRLACABLE", "--setup", "level", "--level", "340", "--offline", FIVE,
                                         "--now", "2026-11-05 11:00", "--holidays", CAL])
        self.assertEqual(code, 0, out)
        out = self.refused("--stop", "or-low", "--target", "r:3")
        self.assertIn("above today's upper circuit limit 346.60", out)
        code, p = self.plan("--stop", "pct:1", "--target", "r:1.5")
        self.assertEqual((code, p["stop"], p["target"]), (0, 336.8, 345.45))

    def test_no_entry_file(self):
        self.assertIn("run /nse-entry", self.refused())
        self.assertIn("both --symbol and --entry", self.refused("--symbol", "SBIN"))

    def test_not_triggered_entry_file(self):
        with open(os.path.join(self.tmp, "entry.json"), "w") as f:
            json.dump({"symbol": "INFY.NS", "status": "waiting"}, f)
        self.assertIn("not a triggered entry", self.refused())


class Rounding(unittest.TestCase):
    def test_stop_never_tighter_than_asked_and_always_on_tick(self):
        rnd = random.Random(7)
        for _ in range(2000):
            tick = rnd.choice(["0.01", "0.05", "0.10", "0.50", "1.00", "5.00"])
            e = rnd.uniform(50, 20000)
            raw = e * (1 - rnd.uniform(0.001, 0.05))
            s = M.stop_price(raw, e, tick)
            self.assertLessEqual(s, raw + 1e-6)
            self.assertTrue(M.on_tick(s, tick), (s, tick))
            self.assertLess(raw - s, float(tick) + 1e-6)


if __name__ == "__main__":
    unittest.main()
