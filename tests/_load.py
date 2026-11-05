"""Test helpers: find the repo, load shared/market.py the way the skills do, load skill scripts."""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
SHARED = os.path.join(ROOT, "shared")
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)

import market  # noqa: E402  (shared/market.py)


def load_skill(folder, name):
    """Load <folder>/<name>.py (skill folders have hyphens, so no plain import)."""
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_") + "_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def phase0_chart(sym):
    """A real saved Yahoo v8 chart from Phase 0 -> (meta, [(ist datetime, bar dict)])."""
    with open(os.path.join(FIX, "phase0", "%s-5m-60d.json" % sym), encoding="utf-8") as f:
        res = json.load(f)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    bars = [(market.to_ist(t), {k: q[k][i] for k in ("open", "high", "low", "close", "volume")})
            for i, t in enumerate(res["timestamp"])]
    return res["meta"], bars
