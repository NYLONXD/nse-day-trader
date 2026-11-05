"""Phase 5: nse-risk on the real INFY chain of Thu 5 Nov 2026, rule by rule."""

import json
import os
import unittest

from _load import FIX, load_skill

import journal

from test_budget import CAL, Chain, NOW, PHASE0

check = load_skill("nse-risk", "check")
size = load_skill("nse-budget", "size")
tm = load_skill("nse-timemachine", "timemachine")
EMPTY_CAL = os.path.join(FIX, "market", "holidays-empty.json")   # an unfilled list


class Base(Chain):
    def order(self, *exit_args):
        self.plan(*exit_args)
        code, out = self.run_mod(size, ["--now", NOW, "--write"])
        self.assertEqual(code, 0, out)

    def risk(self, now=NOW, cal=CAL):
        code, out = self.run_mod(check, ["--now", now, "--holidays", cal, "--json"])
        return code, json.loads(out)

    def rows(self, doc):
        return {r["check"]: r for r in doc["checks"]}

    def edit(self, **kw):
        p = os.path.join(self.tmp, "order.json")
        with open(p, encoding="utf-8") as f:
            o = json.load(f)
        o.update(kw)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(o, f)
        return o


class Pass(Base):
    def test_real_infy_passes_with_warnings(self):
        self.order()
        self.run_mod(tm, ["run", "--offline", PHASE0, "--now", NOW, "--holidays", CAL, "--write", "--no-history"])
        code, d = self.risk()
        self.assertEqual((code, d["verdict"]), (0, "PASS"))
        r = self.rows(d)
        self.assertEqual(d["numbers"]["loss_at_stop_inr"], 492.98)          # re-computed, same as nse-budget
        self.assertEqual(d["numbers"]["net_reward_risk"], 1.67)
        self.assertEqual(r["time machine"]["result"], "WARN")
        self.assertIn("toward the stop", r["time machine"]["detail"])
        self.assertEqual(r["cost rates"]["result"], "WARN")
        self.assertEqual(r["circuit"]["result"], "WARN")
        with open(journal.risk_path(), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["verdict"], saved["date"]), ("PASS", "2026-11-05"))
        self.assertEqual(saved["fingerprint"], journal.fingerprint(saved["order"]))

    def test_any_edit_changes_the_fingerprint(self):
        self.order()
        _, a = self.risk()
        self.edit(qty=40)
        _, b = self.risk()
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])
        self.assertEqual(b["verdict"], "PASS")


class Blocks(Base):
    def blocked(self, name, **kw):
        code, d = self.risk(**kw)
        self.assertEqual((code, d["verdict"]), (1, "BLOCK"))
        r = self.rows(d)[name]
        self.assertEqual(r["result"], "BLOCK", r)
        return r["detail"]

    def test_entry_window_and_calendar(self):
        self.order()
        self.assertIn("opening range is still forming", self.blocked("entry window", now="2026-11-05 09:25"))
        self.assertIn("past no_entries_after 14:30", self.blocked("entry window", now="2026-11-05 14:30"))
        self.assertIn("official circular", self.blocked("calendar", cal=EMPTY_CAL))

    def test_too_many_shares(self):
        self.order()
        self.edit(qty=60)
        self.assertIn("over max_loss_per_trade_inr", self.blocked("max loss"))
        self.assertIn("over max_order_inr", self.blocked("max order"))

    def test_a_run_up_since_the_plan_raises_the_risk(self):
        self.order()
        with open(os.path.join(self.tmp, "prices.json"), "w") as f:
            json.dump({"INFY.NS": {"price": 1170.0, "time": "2026-11-05 10:15 IST"}}, f)
        self.assertIn("over max_loss_per_trade_inr", self.blocked("max loss"))

    def test_daily_loss_trades_and_duplicates_from_the_journal(self):
        self.order()
        self.journal([{"symbol": "SBIN.NS", "qty": 10, "entry": 1000, "max_loss_inr": 450, "closed": True,
                       "realised_inr": -1200.0}])
        self.assertIn("over daily_loss_limit_inr", self.blocked("daily loss"))
        three = [{"symbol": s, "qty": 1, "entry": 100, "max_loss_inr": 10, "closed": True, "realised_inr": 50.0}
                 for s in ("SBIN.NS", "TCS.NS", "ITC.NS")]
        self.journal(three)
        self.assertIn("3 of 3 trades", self.blocked("trades today"))
        self.journal([{"symbol": "INFY.NS", "qty": 1, "entry": 100, "max_loss_inr": 10, "closed": True, "realised_inr": 5.0}])
        self.assertIn("already entered INFY", self.blocked("duplicate"))

    def test_a_win_gives_no_room_back(self):
        self.assertEqual(journal.loss_used({"entries": [
            {"closed": True, "realised_inr": 900.0}, {"closed": True, "realised_inr": -300.0},
            {"closed": False, "max_loss_inr": 450.0}]}), 750.0)

    def test_net_reward_risk_after_costs(self):
        self.order("--target", "r:1.6")                                      # 1.6 gross, under 1.5 net
        detail = self.blocked("reward:risk")
        self.assertIn("after costs", detail)

    def test_format_rules(self):
        self.order()
        self.edit(stop=1149.82)
        self.assertIn("not on the stock's 0.05 tick", self.blocked("stop tick"))
        self.edit(stop=1149.8, side="SELL")
        self.assertIn("no shorts", self.blocked("long only"))
        self.edit(side="BUY", product="CNC")
        self.assertIn("intraday (MIS) orders only", self.blocked("product"))
        self.edit(product="MIS (intraday)", session="2026-11-04")
        self.assertIn("make a new plan", self.blocked("session"))

    def test_upper_circuit(self):
        self.order()
        p = os.path.join(self.tmp, "plan.json")
        with open(p, encoding="utf-8") as f:
            plan = json.load(f)
        plan["limits"] = {"lower": 1045.0, "upper": 1180.0}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        self.assertIn("upper circuit 1,180.00", self.blocked("upper circuit"))

    def test_blocked_symbol(self):
        self.order()
        self.cfg(blocked=["infy"])
        self.assertIn("blocked list", self.blocked("blocked list"))


class Inputs(Base):
    def test_no_order(self):
        code, out = self.run_mod(check, ["--now", NOW, "--holidays", CAL])
        self.assertEqual(code, 1)
        self.assertIn("/nse-budget --write", out)


if __name__ == "__main__":
    unittest.main()
