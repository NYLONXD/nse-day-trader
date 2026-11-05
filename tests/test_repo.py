"""Repo-wide rules: every skill is well-formed and no skill script hardcodes a market rule."""

import ast
import glob
import os
import re
import unittest

from _load import ROOT

SKILLS = sorted(d for d in os.listdir(ROOT) if d.startswith("nse-") and os.path.isdir(os.path.join(ROOT, d)))

# Market rules live in shared/market.py. These patterns in a skill script mean one leaked out.
FORBIDDEN = [
    (r"\b0?9:15\b|\b15:30\b|\b0?9:30\b", "a session time"),
    (r"timedelta\(\s*hours\s*=\s*5", "the IST offset"),
    (r"New_York|America/|US/Eastern|\bET\b", "US time"),
    (r"\b0\.05\b", "a tick size"),
    (r"""["']\.NS["']""", "the symbol suffix"),
    (r"""replace\(\s*["']\.["']\s*,\s*["']-["']""", "the US '.'->'-' rule"),
    (r"\b19800\b", "the IST offset in seconds"),
]


def docstring_lines(src):
    """Line numbers inside module/class/function docstrings (they may describe the rules)."""
    skip = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
            first = node.body[0]
            value = getattr(first, "value", None)
            if isinstance(first, ast.Expr) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                skip.update(range(first.lineno, first.end_lineno + 1))
    return skip


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


class Skills(unittest.TestCase):
    def test_there_are_skills(self):
        self.assertIn("nse-scanner", SKILLS)

    def test_skill_md_shape(self):
        for skill in SKILLS:
            path = os.path.join(ROOT, skill, "SKILL.md")
            with open(path, encoding="utf-8") as f:
                text = f.read().replace("\r\n", "\n")
            fm = frontmatter(text)
            self.assertIn("name: %s\n" % skill, fm + "\n", skill)
            self.assertIn("description:", fm, skill)
            self.assertIn("argument-hint:", fm, skill)
            self.assertRegex(fm, r'"[^"]*"|\buse when\b|Use when', skill)
            self.assertIn("## What it never does", text, skill)
            self.assertIn("## Steps", text, skill)
            self.assertIn("${CLAUDE_SKILL_DIR}/../shared", text, skill)

    def test_one_script_that_imports_shared(self):
        for skill in SKILLS:
            scripts = glob.glob(os.path.join(ROOT, skill, "*.py"))
            self.assertEqual(len(scripts), 1, skill)
            with open(scripts[0], encoding="utf-8") as f:
                src = f.read()
            self.assertIn('os.path.join(SKILL_DIR, "..", "shared")', src, skill)
            self.assertIn("import market", src, skill)

    def test_no_market_rule_hardcoded_in_a_skill_script(self):
        for skill in SKILLS:
            for path in glob.glob(os.path.join(ROOT, skill, "*.py")):
                with open(path, encoding="utf-8") as f:
                    src = f.read()
                skip = docstring_lines(src)
                for n, line in enumerate(src.splitlines(), 1):
                    if n in skip:
                        continue
                    code = line.split("#", 1)[0]
                    for pat, what in FORBIDDEN:
                        self.assertIsNone(re.search(pat, code),
                                          "%s:%d hardcodes %s: %s" % (os.path.relpath(path, ROOT), n, what, line.strip()))

    def test_the_rule_check_catches_a_leak(self):
        leak = "\n".join(["x = 1", 'OPEN = "09:15"', ""])
        self.assertNotIn(2, docstring_lines(leak))
        self.assertTrue(any(re.search(p, leak.splitlines()[1]) for p, _ in FORBIDDEN))

    def test_tests_exist_for_every_skill(self):
        names = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py"))}
        for skill in SKILLS:
            self.assertIn("test_%s.py" % skill.split("-", 1)[1], names, skill)


if __name__ == "__main__":
    unittest.main()
