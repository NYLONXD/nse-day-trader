"""nse-exit: set the stop, the target and the time exit BEFORE the entry. Long only.
Standard library only, Python 3.8+. It never places an order.

  exit.py plan [--stop or-mid|or-low|price:X|pct:X|atr:K] [--target r:K|price:X|pct:X] [--write]
      reads trader/entry.json (from nse-entry), prints the plan, and with --write saves
      trader/plan.json for nse-timemachine, nse-budget, nse-risk and nse-alerts.
  exit.py plan --entry 3272 [--entry-bar HH:MM] [--write]
      re-plan the trigger in trader/entry.json from a later price (the price ran on and
      nse-risk blocked the reward:risk). Symbol, range, tick, band and limits stay from the
      entry file; the entry bar becomes the 5-minute bar running now (or --entry-bar), so
      nse-timemachine replays from there.
  exit.py plan --symbol SBIN --entry 1006.40 --or-high 1003 --or-low 995 [--entry-bar HH:MM] ...
      the same without an entry file (or-* stops need the range; nse-timemachine needs --entry-bar).

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
TRIGGER_CLOSE = "the trigger bar's close"
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
    notes = list(entry.get("_notes") or [])
    kind, x = orb.parse_rule(stop_rule, orb.STOP_RULES, "--stop")
    bars = None
    if kind == "atr":
        if not entry.get("trigger_bar") or not entry.get("session"):
            raise PlanError("atr:K needs the trigger bar from trader/entry.json")
        bars = session_bars_to(entry, offline)
    raw, why, note = orb.raw_stop(stop_rule, e, entry.get("or_high"), entry.get("or_low"), tick, bars)
    if note:
        notes.append(note)
    if raw >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop must be below it. "
                        "Pick a lower stop (or-low, pct:X)." % (M.fmt_number(raw), why, M.fmt_number(e)))
    stop = M.stop_price(raw, e, tick)
    if e - stop < MIN_STOP_TICKS * tick - 1e-9:
        raise PlanError("the stop %s is within %d ticks of the entry %s: ordinary noise would hit it. "
                        "Pick a wider stop." % (M.fmt_number(stop), MIN_STOP_TICKS, M.fmt_number(e)))
    risk = round(e - stop, 2)
    traw, twhy = orb.raw_target(target_rule, e, risk)
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
    elif entry.get("band") == "No Band":
        notes.append("No fixed price band (an F&O stock): no circuit limit applies to the target.")
    else:
        notes.append("Circuit limits unknown (band unknown); the target was not checked against them. "
                     "/nse-scanner daily finds the band.")
    reward = round(target - e, 2)
    rr = round(reward / risk, 2)
    if rr < cfg["min_reward_risk"]:
        notes.append("Reward:risk %.2f is below min_reward_risk %.2f: nse-risk will refuse this plan."
                     % (rr, cfg["min_reward_risk"]))
    if entry.get("trigger_time") and M.parse_hhmm(entry["trigger_time"]) >= M.parse_hhmm(cfg["no_entries_after"]):
        raise PlanError("the trigger at %s is at or after no_entries_after %s" % (
            entry["trigger_time"], cfg["no_entries_after"]))
    return {"symbol": sym, "name": entry.get("name", ""), "session": entry.get("session"), "side": "long",
            "entry": e, "entry_source": entry.get("entry_source") or TRIGGER_CLOSE,
            "trigger_time": entry.get("trigger_time"),
            "stop": stop, "stop_rule": stop_rule, "stop_why": why,
            "target": target, "target_rule": target_rule, "target_why": twhy,
            "risk_per_share": risk, "reward_per_share": reward, "reward_risk": rr,
            "risk_pct": round(risk / e * 100, 2),
            "flat_by": cfg["flat_by"], "no_entries_after": cfg["no_entries_after"],
            "min_reward_risk": cfg["min_reward_risk"], "tick": tick, "limits": limits,
            "band": entry.get("band", "unknown"),
            "or_high": entry.get("or_high"), "or_low": entry.get("or_low"),
            "setup": entry.get("setup", "orb"), "level": entry.get("level"),
            "trigger_bar": entry.get("trigger_bar"), "min_volume_x": entry.get("min_volume_x"),
            "notes": notes, "stop_is_a_plan": NOT_A_GUARANTEE}


def check_entry_bar(text):
    """HH:MM on the 5-minute grid, after the opening range (what nse-timemachine accepts)."""
    try:
        t = M.parse_hhmm(text)
    except Exception:
        raise PlanError("--entry-bar %r is not HH:MM" % text)
    if t.minute % M.BAR_MINUTES or t < M.OPENING_RANGE_END or t >= M.SESSION_CLOSE:
        raise PlanError("--entry-bar %s is not a 5-minute bar start from %s to before %s" % (
            text, M.fmt_time(M.OPENING_RANGE_END), M.fmt_time(M.SESSION_CLOSE)))
    return "%02d:%02d" % (t.hour, t.minute)


def bar_running(session, now):
    """The start of the 5-minute bar running at `now`, if `now` is inside that session after 09:30."""
    if not session or now.date() != M.parse_date(session):
        return None
    t = now.time()
    if t < M.OPENING_RANGE_END or t >= M.SESSION_CLOSE:
        return None
    return "%02d:%02d" % (t.hour, t.minute - t.minute % M.BAR_MINUTES)


def read_entry_file(a, required):
    """trader/entry.json if it holds a triggered entry; else refuse (required) or None."""
    path = a.entry_file or trader_path("entry.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        if not required:
            return None
        raise PlanError("no entry at %s: run /nse-entry SYMBOL first, or pass --symbol and --entry" % path)
    except ValueError:
        raise PlanError("%s is not valid JSON" % path)
    if doc.get("status") != "triggered":
        if not required:
            return None
        raise PlanError("%s is not a triggered entry (status %s)" % (path, doc.get("status")))
    return doc


def replan_entry(doc, a):
    """The triggered entry from the file, re-based on the later price the user gives (--entry).
    Everything else stays; the entry bar moves to the bar running now (or --entry-bar)."""
    e = dict(doc)
    old_bar, old_price = doc.get("trigger_bar"), doc.get("entry_price")
    bar = check_entry_bar(a.entry_bar) if a.entry_bar else bar_running(doc.get("session"), a.clock)
    notes = []
    if bar is None:
        bar = old_bar
        notes.append("Entry bar kept at the trigger bar %s (session %s is not running now). Pass --entry-bar "
                     "HH:MM for the 5-minute bar you bought in, so nse-timemachine replays from there."
                     % (old_bar, doc.get("session")))
    elif old_bar and M.parse_hhmm(bar) < M.parse_hhmm(old_bar):
        raise PlanError("--entry-bar %s is before the trigger bar %s" % (bar, old_bar))
    e["entry_price"] = a.entry
    e["trigger_bar"] = bar
    e["entry_source"] = "your price, entry bar %s IST (the trigger bar's close was %s at %s IST)" % (
        bar, M.fmt_number(old_price) if old_price is not None else "?", doc.get("trigger_time") or old_bar)
    if a.tick:
        e["tick"] = float(a.tick)
    if a.upper_limit:
        e["limits"] = {"lower": a.lower_limit or 0.0, "upper": a.upper_limit}
    e["_notes"] = notes
    return e


def entry_from_args(a):
    if a.symbol and a.entry is None:
        raise PlanError("without an entry file give both --symbol and --entry")
    if a.entry is None:
        return read_entry_file(a, required=True)
    doc = read_entry_file(a, required=False)
    if doc is not None and (not a.symbol or M.normalize_symbol(a.symbol) == doc.get("symbol")):
        return replan_entry(doc, a)
    if not a.symbol:
        raise PlanError("no triggered entry in trader/entry.json to re-plan: run /nse-entry SYMBOL first, "
                        "or give --symbol with --entry")
    e = {"symbol": a.symbol, "entry_price": a.entry, "or_high": a.or_high, "or_low": a.or_low,
         "session": a.session, "band": "unknown", "limits": None, "entry_source": "your price",
         "trigger_bar": check_entry_bar(a.entry_bar) if a.entry_bar else None}
    tick = M.tick_for(a.symbol, None, M.load_tick_overrides(a.ticks))[0]
    e["tick"] = float(a.tick or tick)
    if a.upper_limit:
        e["limits"] = {"lower": a.lower_limit or 0.0, "upper": a.upper_limit}
    return e


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
    at = plan["trigger_time"] if plan["entry_source"] == TRIGGER_CLOSE else None
    print("  entry   %14s  %s%s" % (M.fmt_inr(plan["entry"]), plan["entry_source"], " at %s IST" % at if at else ""))
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
    p.add_argument("--entry", type=float, help="your price; with a triggered trader/entry.json it re-plans that trigger")
    p.add_argument("--entry-bar", help="the 5-minute bar you buy in, HH:MM IST (default: the bar running now)")
    p.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
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
        a.clock = M.parse_ist(a.now) if a.now else M.now_ist()
        return cmd_plan(a)
    except (PlanError, orb.RuleError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else "Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
