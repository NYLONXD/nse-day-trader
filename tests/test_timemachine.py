"""Phase 4: nse-timemachine on Phase 0's 60 real days of INFY 5-minute bars, and shared/costs.py."""

import datetime as dt
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load_skill, market as M

import config
import costs
import yahoo

tm = load_skill("nse-timemachine", "timemachine")
entry = load_skill("nse-entry", "entry")
exitp = load_skill("nse-exit", "exit")
scan = load_skill("nse-scanner", "scan")

PHASE0 = os.path.join(FIX, "phase0")
FIVE = os.path.join(FIX, "nse-scanner", "wall-5m-5d-2026-11-05.json.gz")
CAL = os.path.join(FIX, "market", "holidays-test.json")
EMPTY_CAL = os.path.join(FIX, "market", "holidays-empty.json")   # an unfilled list
NOW = "2026-11-05 17:00"


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

    def infy_plan(self, *exit_args):
        self.run_mod(entry, ["check", "INFY", "--offline", PHASE0, "--now", "2026-11-05 10:15", "--holidays", CAL])
        code, out = self.run_mod(exitp, ["plan", "--write"] + list(exit_args))
        self.assertEqual(code, 0, out)

    def run_tm(self, *extra, data=PHASE0, now=NOW):
        code, out = self.run_mod(tm, ["run", "--offline", data, "--now", now, "--holidays", CAL, "--json"] + list(extra))
        return code, json.loads(out)


class RealInfy(Base):
    def test_pool_costs_and_net_r(self):
        self.infy_plan()
        code, d = self.run_tm()
        self.assertEqual(code, 0)
        self.assertEqual(d["pool"]["sessions"], 58)                        # 60 days - Nov 5 - Oct 15 (gap)
        self.assertEqual(d["pool"]["left_out"], {"missing or empty bars": 1})
        # Sized like nse-budget: 41 shares lose Rs. 492.98 at the stop with costs and slippage, 42 would lose 505.
        self.assertEqual((d["qty"], d["risk_inr"]), (41, 438.7))
        self.assertEqual(d["costs"]["at_target"]["total"],
                         costs.round_trip(1160.55, 1181.9, 41, costs.DEFAULT_COSTS)["total"])
        self.assertEqual((d["net_r_at_target"], d["net_r_at_stop"]), (1.879, -1.124))
        self.assertEqual(len(d["costs"]["still_placeholder"]), len(costs.RATE_KEYS))
        mc = d["mc"]
        self.assertAlmostEqual(mc["target_first_pct"] + mc["stop_first_pct"] + mc["flat_by_exit_pct"], 100, delta=0.2)
        self.assertEqual((mc["replays"], mc["seed"]), (10000, 915))
        self.assertLess(mc["mean_net_r"], mc["mean_gross_r"])              # costs always cost
        self.assertIn("Strongly toward the stop", d["verdict"])

    def test_replays_a_replanned_entry_from_its_later_bar(self):
        # The bug seen live on 2026-11-06: a re-plan with --entry lost the trigger bar and the
        # Time Machine refused "the plan has no trigger_bar". Now it keeps the entry file and
        # moves the entry bar to the bar running when the user re-plans.
        self.infy_plan("--entry", "1165", "--now", "2026-11-05 10:17")
        code, d = self.run_tm()
        self.assertEqual(code, 0, d)
        self.assertEqual((d["plan"]["trigger_bar"], d["plan"]["entry"], d["plan"]["stop"]), ("10:15", 1165.0, 1149.8))

    def test_same_seed_same_numbers_other_seed_close(self):
        self.infy_plan()
        _, a = self.run_tm("--no-history")
        _, b = self.run_tm("--no-history")
        self.assertEqual(a["mc"], b["mc"])
        _, c = self.run_tm("--no-history", "--seed", "7")
        self.assertNotEqual(a["mc"]["mean_net_r"], c["mc"]["mean_net_r"])
        self.assertAlmostEqual(a["mc"]["stop_first_pct"], c["mc"]["stop_first_pct"], delta=2.0)

    def test_history_triggers_match_the_scanner_rule_day_by_day(self):
        self.infy_plan()
        _, d = self.run_tm()
        h = d["history"]
        self.assertEqual((h["sessions"], h["triggered"], h["target"], h["stop"], h["flat_by"]), (58, 19, 1, 11, 7))
        series = yahoo.load_offline(PHASE0)["INFY.NS"]
        cfg = {"min_price_inr": 0, "no_entries_after": "14:30", "flat_by": "15:10"}
        for row in h["rows"]:
            day = M.parse_date(row["date"])
            hit, _ = scan.orb_confirm("INFY.NS", series, day, M.ist(day.year, day.month, day.day, 14, 30), cfg,
                                      {"INFY.NS": Decimal("0.05")}, None, None)
            if row["result"] in ("target", "stop", "time"):
                self.assertEqual((hit["hit_time"], hit["hit_close"]), (row["trigger_time"], row["entry"]), row["date"])
            else:
                self.assertIsNone(hit, row["date"])

    def test_history_exits_by_hand_on_one_day(self):
        self.infy_plan()
        _, d = self.run_tm()
        row = [r for r in d["history"]["rows"] if r["date"] == "2026-10-13"][0]
        self.assertEqual((row["result"], row["exit_time"]), ("target", "12:35"))
        bars = yahoo.load_offline(PHASE0)["INFY.NS"].day(dt.date(2026, 10, 13))
        after = [b for b in bars if M.fmt_time(M.bar_end(b.t)) > row["trigger_time"]]
        first_hit = next(b for b in after if b.h >= row["target"] or b.l <= row["stop"])
        self.assertEqual(M.fmt_time(M.bar_end(first_hit.t)), "12:35")
        self.assertGreaterEqual(first_hit.h, row["target"])
        self.assertGreater(first_hit.l, row["stop"])

    def test_what_really_happened_only_after_flat_by(self):
        self.infy_plan()
        _, d = self.run_tm("--no-history")
        self.assertEqual(d["actual"]["result"], "time")
        _, d = self.run_tm("--no-history", now="2026-11-05 12:00")
        self.assertIsNone(d["actual"])

    def test_write_and_text_report(self):
        self.infy_plan()
        code, out = self.run_mod(tm, ["run", "--offline", PHASE0, "--now", NOW, "--holidays", CAL, "--write"])
        self.assertEqual(code, 0)
        for want in ("seed 915", "PLACEHOLDERS", "break-even after costs", "not a prediction", "Rs. 51.02"):
            self.assertIn(want, out)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "timemachine.json")))

    def test_real_broker_rates_clear_the_placeholder_warning(self):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            mine = {k: costs.DEFAULT_COSTS[k] * 1.1 + 0.01 for k in costs.RATE_KEYS}
            mine["slippage_ticks"] = 2
            json.dump({"costs": mine}, f)
        self.infy_plan()
        _, d = self.run_tm("--no-history")
        self.assertEqual(d["costs"]["still_placeholder"], [])

    def test_price_rules_carry_as_percent(self):
        self.infy_plan("--stop", "price:1150", "--target", "price:1185")
        _, d = self.run_tm()
        self.assertGreater(d["history"]["triggered"], 0)
        p = tm.portable_rules({"entry": 1000, "stop": 990, "target": 1020, "stop_rule": "price:990",
                               "target_rule": "price:1020"})
        self.assertEqual((p["stop_rule"], p["target_rule"]), ("pct:1", "pct:2"))


class Sizing(Base):
    def test_uses_the_budget_quantity_for_the_same_plan(self):
        self.infy_plan()
        with open(os.path.join(self.tmp, "order.json"), "w") as f:
            json.dump({"symbol": "INFY.NS", "session": "2026-11-05", "stop": 1149.8, "target": 1181.9, "qty": 30}, f)
        _, d = self.run_tm("--no-history")
        self.assertEqual((d["qty"], d["qty_source"]), (30, "nse-budget (trader/order.json)"))
        _, d = self.run_tm("--no-history", "--qty", "10")
        self.assertEqual(d["qty"], 10)

    def test_same_sizing_rule_as_the_budget(self):
        c = costs.DEFAULT_COSTS
        q = costs.max_qty_for_loss(1160.5, 1149.8, 0.05, 500, c)
        self.assertEqual(q, 41)
        self.assertLessEqual(costs.loss_at_stop(1160.5, 1149.8, 41, 0.05, c), 500)
        self.assertGreater(costs.loss_at_stop(1160.5, 1149.8, 42, 0.05, c), 500)


class Engine(unittest.TestCase):
    def test_exit_on_bar(self):
        self.assertEqual(tm.exit_on_bar(95, 101, 94, 100, stop=97, target=110), ("stop", 95))   # gap through
        self.assertEqual(tm.exit_on_bar(100, 111, 96, 105, stop=97, target=110), ("stop", 97))  # both: stop
        self.assertEqual(tm.exit_on_bar(100, 111, 98, 105, stop=97, target=110), ("target", 110))
        self.assertIsNone(tm.exit_on_bar(100, 105, 98, 101, stop=97, target=110))

    def test_unbiased_on_a_mirrored_real_pool(self):
        cfg, _ = config.load(os.path.join(tempfile.gettempdir(), "no-such.json"))
        cal = M.load_calendar(CAL)
        s = yahoo.load_offline(PHASE0)["INFY.NS"]
        pool, _ = tm.pool_sessions(s, dt.date(2026, 11, 5), cfg, cal)

        def mirror(bars):
            base = bars[0].c
            f = lambda p: base * base / p
            return [yahoo.Bar(b.t, f(b.o), f(b.l), f(b.h), f(b.c), b.v) for b in bars]
        sym = dict(pool)
        for d, b in pool.items():
            sym[d + dt.timedelta(days=1000)] = mirror(b)
        e = 1160.5
        plan = {"entry": e, "stop": e * math.exp(-0.0092), "target": e * math.exp(0.0092)}
        pr = tm.Pricing(e, plan["stop"], 0.05, 46, dict(cfg["costs"], slippage_ticks=0))
        mc = tm.monte_carlo(plan, sym, 5, 71, pr, 20000, 1, 2, {})
        self.assertAlmostEqual(mc["target_first_pct"], mc["stop_first_pct"], delta=1.5)

    def test_percentile(self):
        self.assertEqual(tm.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(tm.percentile([1, 2], 50), 1.5)


class Refusals(Base):
    def refused(self, *argv, data=PHASE0, cal=CAL):
        code, out = self.run_mod(tm, ["run", "--offline", data, "--now", NOW, "--holidays", cal] + list(argv))
        self.assertEqual(code, 1, out)
        return out

    def test_bad_plans_and_data(self):
        self.infy_plan()
        self.assertIn("official circular", self.refused(cal=EMPTY_CAL))
        self.assertIn("only 4 full sessions", self.refused(data=FIVE))
        base = ["--symbol", "INFY", "--date", "2026-11-05", "--entry", "1160.5", "--target", "1181.9", "--tick", "0.05"]
        self.assertIn("stop < entry < target", self.refused(*base, "--stop", "1170", "--entry-bar", "09:35"))
        self.assertIn("inside the opening range", self.refused(*base, "--stop", "1150", "--entry-bar", "09:20"))
        self.assertIn("not a 5-minute bar", self.refused(*base, "--stop", "1150", "--entry-bar", "09:37"))
        self.assertIn("trading holiday", self.refused("--symbol", "INFY", "--date", "2026-10-02", "--entry", "1000",
                                                      "--stop", "990", "--target", "1020", "--entry-bar", "09:35"))
        self.assertIn("without a plan file give", self.refused("--symbol", "INFY"))

    def test_one_share_over_the_max_loss(self):
        self.infy_plan()
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump({"max_loss_per_trade_inr": 5, "daily_loss_limit_inr": 10}, f)
        self.assertIn("one share loses", self.refused())

    def test_no_plan(self):
        self.assertIn("run /nse-exit plan --write", self.refused())


class Costs(unittest.TestCase):
    def test_round_trip_by_hand(self):
        r = costs.round_trip(1160.5, 1181.9, 46, costs.DEFAULT_COSTS)
        buy, sell = 1160.5 * 46, 1181.9 * 46                                 # 53,383.00 and 54,367.40
        self.assertAlmostEqual(r["brokerage"], round(buy * 0.0003 + sell * 0.0003, 2))   # both under Rs. 20
        self.assertAlmostEqual(r["stt"], round(sell * 0.00025, 2))
        self.assertAlmostEqual(r["stamp"], round(buy * 0.00003, 2))
        self.assertAlmostEqual(r["gst"], round(0.18 * (r["brokerage"] + (buy + sell) * (0.0000297 + 0.000001)), 2))
        self.assertEqual(r["total"], 57.24)

    def test_brokerage_cap(self):
        r = costs.round_trip(1000, 1000, 1000, costs.DEFAULT_COSTS)          # Rs. 10 lakh a leg
        self.assertEqual(r["brokerage"], 40.0)                               # Rs. 20 cap x 2 orders

    def test_config_merges_one_rate_and_checks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"costs": {"brokerage_max_inr_per_order": 0}}, f)
        try:
            cfg, _ = config.load(f.name)
            self.assertEqual(cfg["costs"]["brokerage_max_inr_per_order"], 0)
            self.assertEqual(cfg["costs"]["stt_sell_pct"], 0.025)
            self.assertNotIn("brokerage_max_inr_per_order", costs.placeholder_keys(cfg["costs"]))
        finally:
            os.remove(f.name)
        self.assertTrue(costs.check({"stt_sell_pct": -1}))
        self.assertTrue(any("not a known cost" in p for p in costs.check(dict(costs.DEFAULT_COSTS, stt=1))))


if __name__ == "__main__":
    unittest.main()
