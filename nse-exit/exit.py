"""nse-exit: set the stop, the target and the time exit BEFORE the entry. Long only.
Standard library only, Python 3.8+. It never places an order.

  exit.py plan [--stop or-mid|or-low|price:X|pct:X|atr:K] [--target r:K|price:X|pct:X] [--write]
      reads trader/entry.json (from nse-entry), prints the plan, and with --write saves
      trader/plan.json for nse-timemachine, nse-budget, nse-risk and nse-alerts.
  exit.py plan --symbol SBIN --entry 1006.40 --or-high 1003 --or-low 995 ...
      the same without an entry file (or-* stops need the range).

Rules (prices from shared/market.py):
  - A stop rounds AWAY from the entry onto the stock's tick (down, for a long); a target
    rounds half-up. Refused: a stop at or above the entry, a stop within 2 ticks of it,
    a target at or below the entry, a target above today's upper circuit limit.
  - Flat by `flat_by` (config, IST) whatever the price.
  - Reward:risk below `min_reward_risk` is said here and refused by nse-risk.
Exit code 0 for a plan, 1 when refused (one plain line says why), 2 usage error.
Every stop is a plan, not a guarantee.
"""

import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))

import config  # noqa: E402
import market  # noqa: E402
import orb     # noqa: E402
import yahoo   # noqa: E402

M = market
MIN_STOP_TICKS = 2
ATR_BARS = 14
NOT_A_GUARANTEE = ("The stop is a plan, not a guarantee: a gap, a fast market or a circuit lock "
                   "can fill you worse than the stop, or not at all.")


class PlanError(Exception):
    pass


def trader_path(name):
    return os.path.join(config.trader_dir(), name)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def parse_rule(text, kinds, what):
    """'pct:0.8' -> ('pct', 0.8); 'or-mid' -> ('or-mid', None)."""
    t = str(text).strip().lower()
    if t in kinds and kinds[t] is False:
        return t, None
    if ":" in t:
        k, _, v = t.partition(":")
        if kinds.get(k) is True:
            try:
                x = float(v)
            except ValueError:
                raise PlanError("%s %r: the number after ':' is not a number" % (what, text))
            if x <= 0:
                raise PlanError("%s %r: the number must be above 0" % (what, text))
            return k, x
    raise PlanError("%s must be one of %s (got %r)" % (
        what, ", ".join(k + ("" if v is False else ":X") for k, v in kinds.items()), text))


STOPS = {"or-mid": False, "or-low": False, "price": True, "pct": True, "atr": True}
TARGETS = {"r": True, "price": True, "pct": True}


def atr(bars):
    """(average true range, bars used) over the last ATR_BARS of `bars` (one session; the
    first bar's range is high-low, so the overnight gap does not count)."""
    trs, prev = [], None
    for b in bars:
        tr = b.h - b.l if prev is None else max(b.h - b.l, abs(b.h - prev), abs(b.l - prev))
        trs.append(tr)
        prev = b.c
    use = trs[-ATR_BARS:]
    return sum(use) / len(use), len(use)


def session_bars_to(entry, offline):
    """The session's 5-minute bars up to and including the trigger bar (for atr:K)."""
    sym = entry["symbol"]
    if offline:
        data = yahoo.load_offline(offline)
        if sym not in data:
            raise PlanError("%s is not in the offline data" % sym)
        series = data[sym]
    else:
        series = yahoo.fetch_chart(sym, "1d", "5m")
    day = M.parse_date(entry["session"])
    tb = M.parse_hhmm(entry["trigger_bar"])
    end = M.bar_end(M.ist(day.year, day.month, day.day, tb.hour, tb.minute))
    bars = orb.day_bars(series, day, end)
    missing = orb.missing_bars(bars, day, end)
    if missing:
        raise PlanError("atr: missing 5-minute bar(s) %s on %s" % (",".join(missing[:4]), day))
    return [bars[t] for t in sorted(bars)]


def build_plan(entry, stop_rule, target_rule, cfg, offline=None):
    """The plan dict, or raises PlanError with the reason and the fix."""
    sym = M.normalize_symbol(entry["symbol"])
    tick = entry.get("tick") or float(M.TICK_DEFAULT)
    e = M.clean_price(entry["entry_price"], tick)
    notes = []
    kind, x = parse_rule(stop_rule, STOPS, "--stop")
    if kind in ("or-mid", "or-low"):
        if entry.get("or_high") is None or entry.get("or_low") is None:
            raise PlanError("%s needs the opening range: run /nse-entry first or pass --or-high and --or-low" % kind)
        hi, lo = entry["or_high"], entry["or_low"]
        if kind == "or-mid":
            raw, why = (hi + lo) / 2, "opening-range midpoint %s" % M.fmt_number((hi + lo) / 2)
        else:
            raw, why = lo - tick, "one tick under the opening-range low %s" % M.fmt_number(lo)
    elif kind == "price":
        raw, why = x, "your price"
    elif kind == "pct":
        raw, why = e * (1 - x / 100), "%g%% under the entry" % x
    else:
        if not entry.get("trigger_bar") or not entry.get("session"):
            raise PlanError("atr:K needs the trigger bar from trader/entry.json")
        a, n = atr(session_bars_to(entry, offline))
        raw = e - x * a
        why = "%g x ATR %s, over %d five-minute bars to the trigger bar" % (x, M.fmt_number(a), n)
        if n < ATR_BARS:
            notes.append("ATR averaged the %d bars of the session so far (fewer than %d)." % (n, ATR_BARS))
    if raw >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop must be below it. "
                        "Pick a lower stop (or-low, pct:X)." % (M.fmt_number(raw), why, M.fmt_number(e)))
    stop = M.stop_price(raw, e, tick)
    if e - stop < MIN_STOP_TICKS * tick - 1e-9:
        raise PlanError("the stop %s is within %d ticks of the entry %s: ordinary noise would hit it. "
                        "Pick a wider stop." % (M.fmt_number(stop), MIN_STOP_TICKS, M.fmt_number(e)))
    risk = round(e - stop, 2)
    tkind, tx = parse_rule(target_rule, TARGETS, "--target")
    if tkind == "r":
        traw, twhy = e + tx * risk, "%g x the risk a share" % tx
    elif tkind == "price":
        traw, twhy = tx, "your price"
    else:
        traw, twhy = e * (1 + tx / 100), "%g%% over the entry" % tx
    target = M.target_price(traw, tick)
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s. Pick a higher target." % (
            M.fmt_number(target), M.fmt_number(e)))
    limits = entry.get("limits")
    if limits:
        if target > limits["upper"]:
            raise PlanError("the target %s is above today's upper circuit limit %s: it cannot fill there. "
                            "Pick a target at or below %s." % (M.fmt_number(target), M.fmt_number(limits["upper"]),
                                                               M.fmt_number(limits["upper"])))
        if stop < limits["lower"]:
            notes.append("The stop %s is below today's lower circuit limit %s: no trade can happen below it, "
                         "and in a lower-circuit lock you may not be able to sell at all."
                         % (M.fmt_number(stop), M.fmt_number(limits["lower"])))
    else:
        notes.append("Circuit limits unknown (band %s); the target was not checked against them."
                     % entry.get("band", "unknown"))
    reward = round(target - e, 2)
    rr = round(reward / risk, 2)
    if rr < cfg["min_reward_risk"]:
        notes.append("Reward:risk %.2f is below min_reward_risk %.2f: nse-risk will refuse this plan."
                     % (rr, cfg["min_reward_risk"]))
    if entry.get("trigger_time") and M.parse_hhmm(entry["trigger_time"]) >= M.parse_hhmm(cfg["no_entries_after"]):
        raise PlanError("the trigger at %s is at or after no_entries_after %s" % (
            entry["trigger_time"], cfg["no_entries_after"]))
    return {"symbol": sym, "name": entry.get("name", ""), "session": entry.get("session"), "side": "long",
            "entry": e, "entry_source": "the trigger bar's close" if entry.get("trigger_bar") else "your price",
            "trigger_time": entry.get("trigger_time"),
            "stop": stop, "stop_rule": stop_rule, "stop_why": why,
            "target": target, "target_rule": target_rule, "target_why": twhy,
            "risk_per_share": risk, "reward_per_share": reward, "reward_risk": rr,
            "risk_pct": round(risk / e * 100, 2),
            "flat_by": cfg["flat_by"], "no_entries_after": cfg["no_entries_after"],
            "min_reward_risk": cfg["min_reward_risk"], "tick": tick, "limits": limits,
            "or_high": entry.get("or_high"), "or_low": entry.get("or_low"),
            "notes": notes, "stop_is_a_plan": NOT_A_GUARANTEE}


def entry_from_args(a):
    if a.symbol or a.entry is not None:
        if not (a.symbol and a.entry is not None):
            raise PlanError("without an entry file give both --symbol and --entry")
        e = {"symbol": a.symbol, "entry_price": a.entry, "or_high": a.or_high, "or_low": a.or_low,
             "session": a.session, "band": "unknown", "limits": None}
        tick = M.tick_for(a.symbol, None, M.load_tick_overrides(a.ticks))[0]
        e["tick"] = float(a.tick or tick)
        if a.upper_limit:
            e["limits"] = {"lower": a.lower_limit or 0.0, "upper": a.upper_limit}
        return e
    path = a.entry_file or trader_path("entry.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        raise PlanError("no entry at %s: run /nse-entry SYMBOL first, or pass --symbol and --entry" % path)
    except ValueError:
        raise PlanError("%s is not valid JSON" % path)
    if doc.get("status") != "triggered":
        raise PlanError("%s is not a triggered entry (status %s)" % (path, doc.get("status")))
    return doc


def cmd_plan(a):
    cfg, _ = config.load(a.config)
    entry = entry_from_args(a)
    plan = build_plan(entry, a.stop, a.target, cfg, a.offline)
    path = None
    if a.write:
        path = a.out or trader_path("plan.json")
        write_json(path, plan)
    if a.json:
        print(json.dumps(plan, indent=1, ensure_ascii=False))
        return 0
    s = M.bare_symbol(plan["symbol"])
    print("Plan for %s (long), session %s:" % (s, plan["session"] or "?"))
    print("  entry   %14s  %s%s" % (M.fmt_inr(plan["entry"]), plan["entry_source"],
                                    " at %s IST" % plan["trigger_time"] if plan["trigger_time"] else ""))
    print("  stop    %14s  %s: %s, rounded down to the %s tick" % (
        M.fmt_inr(plan["stop"]), plan["stop_rule"], plan["stop_why"], plan["tick"]))
    print("  target  %14s  %s: %s" % (M.fmt_inr(plan["target"]), plan["target_rule"], plan["target_why"]))
    print("  risk a share %s (%.2f%%), reward a share %s, reward:risk %.2f" % (
        M.fmt_inr(plan["risk_per_share"]), plan["risk_pct"], M.fmt_inr(plan["reward_per_share"]), plan["reward_risk"]))
    print("  flat by %s IST whatever the price. Check your broker's own intraday auto square-off time "
          "and keep flat_by before it." % plan["flat_by"])
    for n in plan["notes"]:
        print("  Note: " + n)
    print(NOT_A_GUARANTEE)
    if path:
        print("Wrote %s. Next: /nse-timemachine to replay it, then /nse-budget to size it." % path)
    else:
        print("Not saved (add --write).")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="exit.py", description="NSE exit planner (long only, paper research).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--stop", default="or-mid", help="or-mid | or-low | price:X | pct:X | atr:K")
    p.add_argument("--target", default="r:2", help="r:K | price:X | pct:X")
    p.add_argument("--entry-file", help="default trader/entry.json")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--session")
    p.add_argument("--tick", type=float, help="the stock's tick if you know it (else tick file, else %s)" % M.TICK_DEFAULT)
    p.add_argument("--upper-limit", type=float, help="today's upper circuit limit, if you know it")
    p.add_argument("--lower-limit", type=float)
    p.add_argument("--offline", help="saved Yahoo 5-minute charts (for atr:K)")
    p.add_argument("--config")
    p.add_argument("--ticks")
    p.add_argument("--write", action="store_true", help="save trader/plan.json")
    p.add_argument("--out")
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return cmd_plan(a)
    except (PlanError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else "Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
