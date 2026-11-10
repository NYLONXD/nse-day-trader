"""Phase 7: the plugin manifests, README, LICENSE, and one whole example day end to end."""

import io
import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, load_skill

SKILLS = sorted(d for d in os.listdir(ROOT) if d.startswith("nse-") and os.path.isdir(os.path.join(ROOT, d)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Manifests(unittest.TestCase):
    def test_nine_skills(self):
        self.assertEqual(SKILLS, ["nse-alerts", "nse-broker", "nse-budget", "nse-entry", "nse-exit",
                                  "nse-news", "nse-risk", "nse-scanner", "nse-timemachine"])

    def test_plugin_json(self):
        p = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(p["name"], "nse-day-trader")
        self.assertRegex(p["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(p["license"], "MIT")
        self.assertEqual(p["author"]["name"], "Himanshu Jha")
        self.assertEqual(p["skills"], "./")
        for s in SKILLS:                                   # "skills": "./" finds every skill folder
            self.assertTrue(os.path.exists(os.path.join(ROOT, p["skills"], s, "SKILL.md")), s)
        self.assertIn("paper", p["description"].lower())
        self.assertEqual(p["homepage"], "https://github.com/NYLONXD/nse-day-trader")
        self.assertEqual(p["repository"], p["homepage"])

    def test_marketplace_json(self):
        m = json.loads(read(".claude-plugin", "marketplace.json"))
        self.assertEqual(m["name"], "nse-day-trader")
        self.assertEqual([x["name"] for x in m["plugins"]], ["nse-day-trader"])
        self.assertEqual(m["plugins"][0]["source"], "./")
        for s in SKILLS:
            self.assertIn(s, m["plugins"][0]["description"])

    def test_no_folder_outside_the_skills_looks_like_a_skill(self):
        for d in os.listdir(ROOT):
            if os.path.isdir(os.path.join(ROOT, d)) and d not in SKILLS:
                self.assertFalse(os.path.exists(os.path.join(ROOT, d, "SKILL.md")), d)


class Docs(unittest.TestCase):
    def test_readme(self):
        r = read("README.md")
        for s in SKILLS:
            self.assertIn("`%s`" % s, r)
        for want in ("## Install", "## First-time setup", "## An example day", "## How to extend", "## Tests",
                     "Most retail intraday traders lose money", "/plugin marketplace add", "nse_holidays.json",
                     "PLACEHOLDERS", "contract note", "day-trader-skill", "none of it is copied", "MIT"):
            self.assertIn(want, r)
        self.assertNotRegex(r, r"Alpaca|\$\d|\bUSD\b")             # no US-market leftovers
        self.assertNotRegex(r, r"YOUR-GITHUB-USER|\b[A-Z]:[/\\]|Professional_life")   # no placeholders or PC paths
        self.assertIn("NYLONXD/nse-day-trader", r)

    def test_license(self):
        lic = read("LICENSE")
        self.assertTrue(lic.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Himanshu Jha", lic)

    def test_said_once_per_skill(self):
        for s in SKILLS:
            text = read(s, "SKILL.md")
            self.assertEqual(len(re.findall(r"lose money", text)), 1, s)


class ExampleDay(unittest.TestCase):
    def test_the_eight_trade_skills_run_end_to_end(self):
        ex = load_skill("tools", "example_day")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = ex.main([])
        text = out.getvalue()
        self.assertEqual(code, 0, text[-2000:])
        for want in ("INFY TRIGGERED", "41 shares   <- decides it", "PASS: every rule holds", "PAPER TICKET",
                     "FLAT BY", "Net Rs. 125.73", "Every step ran."):
            self.assertIn(want, text)


if __name__ == "__main__":
    unittest.main()
