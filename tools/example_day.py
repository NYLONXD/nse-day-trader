"""One whole NSE day through all eight skills, offline, on real saved Yahoo data of
Thu 5 Nov 2026. Standard library only. Nothing touches the network or a broker.

  python tools/example_day.py                 # into a fresh temporary trader/ folder
  python tools/example_day.py --keep DIR      # keep the trader/ files in DIR to look at

It uses tests/fixtures/market/holidays-test.json, a TEST calendar (your real
data/nse_holidays.json is empty until you fill it, so the real tools would refuse the day).
The fills are simulated: the buy at 1,160.60 and the sell at the 15:05 bar's close.
"""

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
CAL = os.path.join(FIX, "market", "holidays-test.json")
PHASE0 = os.path.join(FIX, "phase0")
WALL = os.path.join(FIX, "nse-scanner", "wall-symbols.txt")
BANDS = os.path.join(FIX, "nse-scanner", "nse", "sec_list.csv")
DAILY = os.path.join(FIX, "nse-scanner", "wall-1d-3mo-2026-11-05.json.gz")
FIVE = os.path.join(FIX, "nse-scanner", "wall-5m-5d-2026-11-05.json.gz")


def load(folder, name):
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_"), os.path.join(ROOT, folder, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def short(a):
    """Print repo paths short (tests/fixtures/...), and quote anything with a space."""
    if os.path.isabs(a) and a.startswith(ROOT):
        a = os.path.relpath(a, ROOT).replace(os.sep, "/")
    return '"%s"' % a if " " in a else a


def step(title, mod, argv):
    print("\n$ %s %s" % (title, " ".join(short(a) for a in argv)))
    code = mod.main(argv)
    return code


def run():
    scan = load("nse-scanner", "scan")
    entry = load("nse-entry", "entry")
    exitp = load("nse-exit", "exit")
    tm = load("nse-timemachine", "timemachine")
    size = load("nse-budget", "size")
    risk = load("nse-risk", "check")
    alerts = load("nse-alerts", "alerts")
    ticket = load("nse-broker", "ticket")
    cal = ["--holidays", CAL]
    print("An NSE day, Thu 5 Nov 2026 (real saved data, TEST calendar, simulated fills).")
    codes = []
    codes.append(step("scan.py", scan, ["universe", "--csv", WALL, "--bands", BANDS]))
    codes.append(step("scan.py", scan, ["daily", "--offline", DAILY, "--now", "2026-11-05 09:00", "--show", "5", "--quiet"]))
    codes.append(step("scan.py", scan, ["orb", "--offline", FIVE, "--now", "2026-11-05 10:15", "--show", "6", "--quiet"] + cal))
    codes.append(step("entry.py", entry, ["check", "INFY", "--offline", PHASE0, "--now", "2026-11-05 10:15"] + cal))
    codes.append(step("exit.py", exitp, ["plan", "--write"]))
    codes.append(step("timemachine.py", tm, ["run", "--offline", PHASE0, "--now", "2026-11-05 10:16", "--write", "--show", "3"] + cal))
    codes.append(step("size.py", size, ["--now", "2026-11-05 10:16", "--write"]))
    codes.append(step("check.py", risk, ["--now", "2026-11-05 10:16"] + cal))
    codes.append(step("ticket.py", ticket, ["ticket", "--now", "2026-11-05 10:17"]))
    codes.append(step("ticket.py", ticket, ["placed", "--price", "1160.60", "--now", "2026-11-05 10:18"]))
    codes.append(step("alerts.py", alerts, ["from-plan", "--now", "2026-11-05 10:18"] + cal))
    step("alerts.py", alerts, ["watch", "--offline", PHASE0, "--now", "2026-11-05 10:18", "--every", "300"] + cal)
    sys.path.insert(0, os.path.join(ROOT, "shared"))
    import yahoo
    import market
    last = [b for b in yahoo.load_offline(PHASE0)["INFY.NS"].bars if str(b.t)[:16] == "2026-11-05 15:05"][0]
    out = market.clean_price(last.c, "0.05")
    codes.append(step("ticket.py", ticket, ["closed", "INFY", "--price", "%.2f" % out, "--reason", "flat_by",
                                                    "--now", "2026-11-05 15:10"]))
    codes.append(step("ticket.py", ticket, ["status", "--now", "2026-11-05 15:11"]))
    return codes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="write the trader/ files here and keep them")
    a = ap.parse_args(argv)
    d = a.keep or tempfile.mkdtemp(prefix="nse-example-")
    old = os.environ.get("NSE_TRADER_DIR")
    os.environ["NSE_TRADER_DIR"] = d
    try:
        codes = run()
    finally:
        if old is None:
            os.environ.pop("NSE_TRADER_DIR", None)
        else:
            os.environ["NSE_TRADER_DIR"] = old
        if not a.keep:
            shutil.rmtree(d, ignore_errors=True)
    bad = [c for c in codes if c != 0]
    print("\nEvery step ran." if not bad else "\n%d step(s) did not exit 0." % len(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
