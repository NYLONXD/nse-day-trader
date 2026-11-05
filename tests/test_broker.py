"""Phase 6: nse-broker builds a ticket only; it never calls a broker or touches a secret."""

import ast
import json
import os
import unittest

from _load import ROOT, load_skill

import costs
import journal

from test_budget import CAL, Chain, NOW

ticket = load_skill("nse-broker", "ticket")
check = load_skill("nse-risk", "check")
size = load_skill("nse-budget", "size")


class Base(Chain):
    def passed(self, now=NOW, *exit_args):
        self.plan(*exit_args)
        code, out = self.run_mod(size, ["--now", now, "--write"])
        self.assertEqual(code, 0, out)
        code, out = self.run_mod(check, ["--now", now, "--holidays", CAL])
        self.assertEqual(code, 0, out)

    def tk(self, *argv, now="2026-11-05 10:17"):
        return self.run_mod(ticket, list(argv) + ["--now", now])

    def day(self):
        return journal.load(__import__("datetime").date(2026, 11, 5))


class Ticket(Base):
    def test_real_infy_ticket(self):
        self.passed()
        code, out = self.tk("ticket")
        self.assertEqual(code, 0, out)
        for want in ("PAPER TICKET 2026-11-05 10:17 IST", "Nothing was sent anywhere", "Product MIS (Intraday)",
                     "BUY 41 INFY at MARKET", "LIMIT buy at Rs. 1,160.65", "SL-M, trigger Rs. 1,149.80",
                     "limit Rs. 1,146.35", "OCO / GTT-OCO", "which is a short", "By 15:10 IST", "no leverage",
                     "not a guarantee"):
            self.assertIn(want, out)
        with open(os.path.join(self.tmp, "ticket.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertEqual((t["qty"], t["max_entry"], t["sl_limit"]), (41, 1160.65, 1146.35))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "ticket.txt")))

    def test_the_capped_limit_is_the_last_price_that_fits_the_max_loss(self):
        c = costs.DEFAULT_COSTS
        self.assertLessEqual(costs.loss_at_stop(1160.65, 1149.8, 41, 0.05, c), 500)
        self.assertGreater(costs.loss_at_stop(1160.70, 1149.8, 41, 0.05, c), 500)


class Gate(Base):
    def refused(self, *argv, now="2026-11-05 10:17"):
        code, out = self.tk(*argv, now=now)
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("Refused: "), out)
        return out

    def test_no_risk_check(self):
        self.plan()
        self.run_mod(size, ["--now", NOW, "--write"])
        self.assertIn("run: /nse-risk", self.refused("ticket"))

    def test_a_blocked_order(self):
        self.passed()
        self.run_mod(check, ["--now", "2026-11-05 14:35", "--holidays", CAL])   # past no_entries_after
        self.assertIn("did not pass", self.refused("ticket", now="2026-11-05 14:36"))

    def test_an_order_edited_after_the_check(self):
        self.passed()
        p = os.path.join(self.tmp, "order.json")
        with open(p, encoding="utf-8") as f:
            o = json.load(f)
        o["qty"] = 42
        with open(p, "w", encoding="utf-8") as f:
            json.dump(o, f)
        self.assertIn("changed after nse-risk checked it", self.refused("ticket"))

    def test_old_check_other_day_and_window(self):
        self.passed()
        self.assertIn("more than 15 minutes ago", self.refused("ticket", now="2026-11-05 10:32"))
        self.assertIn("not today", self.refused("ticket", now="2026-11-06 10:17"))
        self.passed(now="2026-11-05 14:25")
        self.assertIn("outside the entry window", self.refused("ticket", now="2026-11-05 14:31"))

    def test_one_ticket_one_placement(self):
        self.passed()
        self.tk("ticket")
        self.tk("placed", "--price", "1160.60", now="2026-11-05 10:18")
        self.assertIn("already recorded", self.refused("ticket", now="2026-11-05 10:19"))
        self.assertIn("already recorded", self.refused("placed", "--price", "1160.60", now="2026-11-05 10:19"))


class Journal(Base):
    def test_placed_and_closed_net_of_costs(self):
        self.passed()
        self.tk("ticket")
        code, out = self.tk("placed", "--price", "1160.60", now="2026-11-05 10:18")
        self.assertEqual(code, 0, out)
        e = self.day()["entries"][0]
        # 41 x (1,160.60 - 1,149.75) + costs = the max loss from the REAL fill
        want = 41 * (1160.60 - 1149.75) + costs.total(1160.60, 1149.75, 41, costs.DEFAULT_COSTS)
        self.assertAlmostEqual(e["max_loss_inr"], round(want, 2), places=2)
        self.assertEqual(journal.loss_used(self.day()), e["max_loss_inr"])
        code, out = self.tk("closed", "INFY", "--price", "1167.40", "--reason", "flat_by", now="2026-11-05 15:10")
        e = self.day()["entries"][0]
        self.assertEqual(e["realised_inr"], round(41 * (1167.40 - 1160.60) - costs.total(1160.60, 1167.40, 41,
                                                                                         costs.DEFAULT_COSTS), 2))
        self.assertEqual(e["realised_inr"], 228.16)
        self.assertEqual(journal.loss_used(self.day()), 0.0)
        self.assertIn("Cancel any stop-loss or target order still resting", out)

    def test_a_loss_counts_against_the_day(self):
        self.passed()
        self.tk("ticket")
        self.tk("placed", "--price", "1160.60", now="2026-11-05 10:18")
        self.tk("closed", "INFY", "--price", "1149.70", "--reason", "stop", now="2026-11-05 11:00")
        self.assertGreater(journal.loss_used(self.day()), 450)
        code, out = self.tk("status", now="2026-11-05 11:01")
        self.assertIn("1 of 3 trades, 0 open", out)

    def test_bad_fills(self):
        self.passed()
        self.tk("ticket")
        out = self.tk("placed", "--price", "1160.62", now="2026-11-05 10:18")[1]
        self.assertIn("not on the 0.05 tick", out)
        self.assertIn("more than 5%", self.tk("placed", "--price", "1300", now="2026-11-05 10:18")[1])
        self.assertIn("1 to 41", self.tk("placed", "--price", "1160.60", "--qty", "50", now="2026-11-05 10:18")[1])

    def test_a_worse_fill_is_warned(self):
        self.passed()
        self.tk("ticket")
        code, out = self.tk("placed", "--price", "1161.50", now="2026-11-05 10:18")
        self.assertIn("over your Rs. 500.00", out)

    def test_flatten(self):
        self.passed()
        self.tk("ticket")
        self.tk("placed", "--price", "1160.60", now="2026-11-05 10:18")
        code, out = self.tk("flatten", now="2026-11-05 15:10")
        self.assertIn("Cancel the resting stop-loss and target orders for INFY, then SELL 41 INFY at MARKET", out)

    def test_risk_reads_the_journal_after_a_placement(self):
        self.passed()
        self.tk("ticket")
        self.tk("placed", "--price", "1160.60", now="2026-11-05 10:18")
        code, out = self.run_mod(check, ["--now", "2026-11-05 10:20", "--holidays", CAL])
        self.assertEqual(code, 1)
        self.assertIn("you already entered INFY today", out)


class NeverCallsABroker(unittest.TestCase):
    """The promise, checked in the code: no network, no secrets, in ticket.py or what it imports."""
    NETWORK = {"urllib", "urllib.request", "http", "http.client", "socket", "ssl", "requests", "yahoo",
               "smtplib", "ftplib", "asyncio", "subprocess", "webbrowser"}
    ALLOWED_LOCAL = {"config", "costs", "journal", "market"}

    def imports(self, path):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
        return out, tree

    def test_ticket_and_its_shared_modules_have_no_network(self):
        mods, _ = self.imports(os.path.join(ROOT, "nse-broker", "ticket.py"))
        local = mods & {"config", "costs", "journal", "market", "yahoo", "nse_files", "orb"}
        self.assertEqual(local, self.ALLOWED_LOCAL)
        seen = [os.path.join(ROOT, "nse-broker", "ticket.py")] + [os.path.join(ROOT, "shared", m + ".py") for m in local]
        for path in seen:
            m, _ = self.imports(path)
            self.assertEqual(m & self.NETWORK, set(), path)

    def test_no_secret_is_ever_read(self):
        for path in [os.path.join(ROOT, "nse-broker", "ticket.py")]:
            _, tree = self.imports(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, ("environ", "getenv", "getpass"), path)
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, ("input", "getpass"), path)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    low = node.value.lower()
                    if "api" in low and "key" in low:
                        self.assertIn("never", low, node.value)

    def test_skill_md_promises_it(self):
        with open(os.path.join(ROOT, "nse-broker", "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("never connects to any broker", text)
        self.assertIn("do not take it", text)


if __name__ == "__main__":
    unittest.main()
