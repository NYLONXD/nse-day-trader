"""Phase 5: nse-budget on the real INFY plan of Thu 5 Nov 2026 (Phase 0 chart)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load_skill

import costs

entry = load_skill("nse-entry", "entry")
exitp = load_skill("nse-exit", "exit")
size = load_skill("nse-budget", "size")

PHASE0 = os.path.join(FIX, "phase0")
CAL = os.path.join(FIX, "market", "holidays-test.json")
NOW = "2026-11-05 10:16"


class Chain(unittest.TestCase):
    """Real chain: nse-entry (INFY triggered 09:40 at 1,160.50) -> nse-exit (or-mid, 2R)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("NSE_TRADER_DIR")
        os.environ["NSE_TRADER_DIR"] = self.tmp
        self.cfg()

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

    def cfg(self, **kw):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump(kw, f)

    def plan(self, *exit_args):
        code, out = self.run_mod(entry, ["check", "INFY", "--offline", PHASE0, "--now", "2026-11-05 10:15", "--holidays", CAL])
        self.assertEqual(code, 0, out)
        code, out = self.run_mod(exitp, ["plan", "--write"] + list(exit_args))
        self.assertEqual(code, 0, out)

    def prices(self, price, time):
        with open(os.path.join(self.tmp, "prices.json"), "w") as f:
            json.dump({"INFY.NS": {"price": price, "time": time}}, f)

    def journal(self, entries):
        os.makedirs(os.path.join(self.tmp, "state"), exist_ok=True)
        with open(os.path.join(self.tmp, "state", "journal-2026-11-05.json"), "w") as f:
            json.dump({"date": "2026-11-05", "entries": entries}, f)

    def sized(self, *argv, now=NOW):
        code, out = self.run_mod(size, ["--now", now, "--json"] + list(argv))
        return code, json.loads(out)


class Sizes(Chain):
    def test_real_infy_is_decided_by_max_loss_net_of_costs(self):
        self.plan()
        code, o = self.sized()
        self.assertEqual(code, 0)
        # position 50,000 // 1,160.55 = 43; cash 90,000 // 1,160.55 = 77; max loss with costs = 41
        self.assertEqual(o["limits"], {"max loss": 41, "position": 43, "max order": 43, "cash": 77})
        self.assertEqual((o["qty"], o["decided_by"], o["order_type"], o["side"]), (41, "max loss", "MARKET", "BUY"))
        self.assertEqual((o["value_inr"], o["max_loss_at_stop_inr"], o["gain_at_target_inr"]), (47582.55, 492.98, 824.33))
        self.assertEqual((o["costs_at_stop_inr"], o["slippage_at_stop_inr"], o["net_reward_risk"]), (50.18, 4.1, 1.67))
        self.assertAlmostEqual(41 * (1160.55 - 1149.75) + o["costs_at_stop_inr"], o["max_loss_at_stop_inr"], places=2)
        self.assertTrue(o["paper"])

    def test_gross_sizing_would_break_the_limit(self):
        c = costs.DEFAULT_COSTS
        self.assertEqual(int(500 // 10.7), 46)
        self.assertGreater(costs.loss_at_stop(1160.5, 1149.8, 46, 0.05, c), 550)

    def test_small_capital_is_decided_by_position(self):
        self.cfg(capital_inr=20000, max_loss_per_trade_inr=500)
        self.plan()
        code, o = self.sized()
        self.assertEqual((o["qty"], o["decided_by"]), (8, "position"))      # 10,000 // 1,160.55

    def test_open_positions_use_cash(self):
        self.plan()
        self.journal([{"symbol": "SBIN.NS", "qty": 60, "entry": 1000.0, "stop": 990, "max_loss_inr": 400,
                       "closed": False}])
        code, o = self.sized()
        self.assertEqual((o["qty"], o["decided_by"], o["open_positions_inr"]), (25, "cash", 60000.0))

    def test_fresh_higher_price_means_fewer_shares(self):
        self.plan()
        self.prices(1164.20, "2026-11-05 10:15 IST")                         # INFY's real 10:15 close
        code, o = self.sized()
        self.assertEqual(o["entry_used"], 1164.2)
        self.assertIn("fresher, higher", o["entry_source"])
        self.assertLess(o["qty"], 41)
        self.assertLessEqual(o["max_loss_at_stop_inr"], 500)

    def test_old_or_lower_prices_are_not_used(self):
        self.plan()
        self.prices(1170.0, "2026-11-05 09:55 IST")                          # 21 minutes old
        code, o = self.sized()
        self.assertEqual(o["entry_used"], 1160.5)
        self.assertTrue(any("too old" in n for n in o["notes"]))
        self.prices(1155.0, "2026-11-05 10:15 IST")                          # fresh but lower
        code, o = self.sized()
        self.assertEqual(o["entry_used"], 1160.5)

    def test_limit_order_has_no_entry_slippage(self):
        self.plan()
        code, o = self.sized("--limit", "1160.00")
        self.assertEqual((o["order_type"], o["limit_price"], o["entry_used"]), ("LIMIT", 1160.0, 1160.0))
        self.assertEqual(o["slippage_at_stop_inr"], 0)

    def test_placeholder_costs_are_said(self):
        self.plan()
        code, o = self.sized()
        self.assertTrue(any("placeholders" in n for n in o["notes"]))

    def test_write(self):
        self.plan()
        code, out = self.run_mod(size, ["--now", NOW, "--write"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "order.json")))
        for want in ("<- decides it", "Rs. 492.98", "no leverage", "not a guarantee", "/nse-risk"):
            self.assertIn(want, out)


class Refusals(Chain):
    def refused(self, *argv, now=NOW):
        code, out = self.run_mod(size, ["--now", now] + list(argv))
        self.assertEqual(code, 1, out)
        return out

    def test_refusals(self):
        self.plan()
        self.assertIn("today is 2026-11-06", self.refused(now="2026-11-06 10:16"))
        self.assertIn("not on the 0.05 tick", self.refused("--limit", "1160.02"))
        self.prices(1185.0, "2026-11-05 10:15 IST")
        self.assertIn("the move already happened", self.refused())
        os.remove(os.path.join(self.tmp, "prices.json"))
        self.assertIn("setup is gone", self.refused("--limit", "1149.80"))

    def test_blocked_min_order_and_zero_shares(self):
        self.cfg(blocked=["INFY"])
        self.plan()
        self.assertIn("blocked list", self.refused())
        self.cfg(min_order_inr=60000)
        self.assertIn("under min_order_inr", self.refused())
        self.cfg(max_loss_per_trade_inr=5, daily_loss_limit_inr=10)
        out = self.refused()
        self.assertIn("max loss limit allows 0 shares", out)
        self.assertIn("closer stop", out)

    def test_no_plan(self):
        self.assertIn("/nse-exit plan --write", self.refused())


if __name__ == "__main__":
    unittest.main()
