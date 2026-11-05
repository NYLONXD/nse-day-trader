"""nse-timemachine: replay a plan 10,000 times from the stock's own recent 5-minute bars, and
re-run the plan's rule on every past session, all NET OF COSTS. Long only.
Standard library only, Python 3.8+. A simulation, not a prediction. It never places an order.

  timemachine.py run [--plan trader/plan.json] [--replays 10000] [--seed 915] [--write]
  timemachine.py run --symbol INFY --date 2026-11-05 --entry 1160.5 --stop 1149.8 \
                     --target 1181.9 --entry-bar 09:35

How it works
  Pool:     every FULL session before the plan's date in Yahoo's 60 days of 5-minute bars:
            a normal NSE day by data/nse_holidays.json, and every bar from the open to
            flat_by present with prices (bars from 15:15 on are never needed).
  Replay:   start at the entry. For each bar until flat_by, draw a past bar from the same
            time of day (+-window bars) of a random pool session, scaled by its move from the
            bar before. Each bar: an open at or under the stop exits at the open (a gap
            through the stop), else a low at or under the stop exits at the stop, else a high
            at or over the target exits at the target. A bar touching both counts as the stop.
            Still open at flat_by: out at that bar's close.
  Net:      slippage_ticks against you on the entry and on stop/time exits (market orders),
            none on the target (a limit order), then the round-trip costs of shared/costs.py
            for the quantity. R = net rupees / (risk a share x quantity).
  History:  the plan's own rule (same setup, stop rule and target rule, same tick) on every
            pool session: what really happened, also net of costs.
  Seed:     random.Random(seed): same plan, same data, same seed -> the same numbers.
"""

import argparse
import json
import math
import os
import random
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))

import config  # noqa: E402
import costs   # noqa: E402
import market  # noqa: E402
import orb     # noqa: E402
import yahoo   # noqa: E402

M = market
SEED = 915
REPLAYS = 10000
WINDOW = 2
MIN_SESSIONS = 20
HISTORY_RANGE = "60d"
PCTS = (5, 25, 50, 75, 95)
NOTE = ("A simulation from the stock's own recent bars, not a prediction. Most retail intraday "
        "traders lose money; a good-looking replay does not change that.")


class TMError(Exception):
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


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


# ------------------------------------------------------------------ sessions

def flat_by_end(day, cfg):
    t = M.parse_hhmm(cfg["flat_by"])
    return M.ist(day.year, day.month, day.day, t.hour, t.minute)


def session_slots(day, cfg):
    """Bar starts from the open whose bar ends by flat_by (09:15 ... 15:05 for 15:10)."""
    end = flat_by_end(day, cfg)
    return [t for t in M.bar_starts(day) if M.bar_end(t) <= end]


def pool_sessions(series, before, cfg, cal):
    """({day: [Bar...]} of full sessions before `before`, {reason: count} of days left out)."""
    by_day = {}
    for b in series.bars:
        if M.is_bar_start(b.t):
            by_day.setdefault(b.t.date(), {})[b.t] = b
    keep, dropped = {}, {}
    for d in sorted(by_day):
        if d >= before:
            continue
        st = cal.status(d)
        if not st.tradable:
            dropped[st.kind] = dropped.get(st.kind, 0) + 1
            continue
        slots = session_slots(d, cfg)
        bars = [by_day[d].get(t) for t in slots]
        if any(b is None or None in (b.o, b.h, b.l, b.c) for b in bars):
            dropped["missing or empty bars"] = dropped.get("missing or empty bars", 0) + 1
            continue
        keep[d] = bars
    return keep, dropped


def ratios(bars):
    """Each bar from the second on as (open, high, low, close) over the previous close."""
    out = [None]
    for prev, b in zip(bars, bars[1:]):
        pc = prev.c
        out.append((b.o / pc, b.h / pc, b.l / pc, b.c / pc))
    return out


# ------------------------------------------------------------------ one trade

def exit_on_bar(o, h, l, c, stop, target):
    """('stop'|'target', fill price before slippage) or None. Stop first, conservatively."""
    if o <= stop:
        return "stop", o
    if l <= stop:
        return "stop", stop
    if h >= target:
        return "target", target
    return None


class Pricing(object):
    """Turns a price path's result into net rupees and net R for the plan's quantity."""

    def __init__(self, entry, stop, tick, qty, cost_cfg):
        self.entry, self.stop, self.qty, self.c = entry, stop, qty, cost_cfg
        self.slip = cost_cfg["slippage_ticks"] * tick
        self.fill_in = entry + self.slip
        self.risk_inr = (entry - stop) * qty

    def net(self, kind, price):
        out = price if kind == "target" else price - self.slip
        gross = (out - self.fill_in) * self.qty
        cost = costs.total(self.fill_in, out, self.qty, self.c)
        inr = gross - cost
        return inr, inr / self.risk_inr, cost


# ------------------------------------------------------------------ Monte Carlo

def monte_carlo(plan, pool, first_slot, n_slots, pricing, replays, seed, window, checkpoints):
    """Replays from the bar after the trigger bar (slot index first_slot) to the last slot."""
    rng = random.Random(seed)
    days = sorted(pool)
    rat = [ratios(pool[d]) for d in days]
    stop, target = plan["stop"], plan["target"]
    kinds = {"target": 0, "stop": 0, "time": 0}
    r_list, inr_list, gross_r = [], [], []
    marks = {k: [] for k in checkpoints}
    risk = plan["entry"] - plan["stop"]
    for i in range(replays):
        price = plan["entry"]
        result = None
        for k in range(first_slot, n_slots):
            lo_j, hi_j = max(1, k - window), min(n_slots - 1, k + window)
            ro, rh, rl, rc = rat[rng.randrange(len(rat))][rng.randint(lo_j, hi_j)]
            o, h, l, c = price * ro, price * rh, price * rl, price * rc
            hit = exit_on_bar(o, h, l, c, stop, target)
            if hit:
                result = hit
                break
            price = c
            if k in marks:
                marks[k].append(price)
        if result is None:
            result = ("time", price)
        kind, fill = result
        for vals in marks.values():
            if len(vals) < i + 1:      # exited before this checkpoint: it stays at the exit price
                vals.append(fill)
        kinds[kind] += 1
        inr, r, _c = pricing.net(kind, fill)
        r_list.append(r)
        inr_list.append(inr)
        gross_r.append((fill - plan["entry"]) / risk)
    r_sorted, inr_sorted = sorted(r_list), sorted(inr_list)
    n = float(replays)
    return {
        "replays": replays, "seed": seed, "window_bars": window, "pool_sessions": len(days),
        "target_first_pct": round(100 * kinds["target"] / n, 1),
        "stop_first_pct": round(100 * kinds["stop"] / n, 1),
        "flat_by_exit_pct": round(100 * kinds["time"] / n, 1),
        "net_positive_pct": round(100 * sum(1 for x in r_list if x > 0) / n, 1),
        "mean_net_r": round(sum(r_list) / n, 3), "mean_gross_r": round(sum(gross_r) / n, 3),
        "mean_net_inr": round(sum(inr_list) / n, 2),
        "net_r_percentiles": {str(p): round(percentile(r_sorted, p), 3) for p in PCTS},
        "net_inr_percentiles": {str(p): round(percentile(inr_sorted, p), 2) for p in PCTS},
        "fan": {k: {str(p): round(percentile(sorted(v), p), 2) for p in PCTS} for k, v in marks.items()},
    }


# ------------------------------------------------------------------ history

def plan_rule_on_day(plan, bars, day, cfg, qty_rule, cost_cfg):
    """The plan's own rule on one real past session. Returns a row dict."""
    tick = plan["tick"]
    cutoff = flat_by_end(day, cfg)
    by_t = {b.t: b for b in bars}
    rng = orb.opening_range(by_t, day, tick)
    row = {"date": str(day)}
    if rng is None:
        return dict(row, result="no range")
    or_high, or_low = rng
    level = or_high
    nea = M.parse_hhmm(cfg["no_entries_after"])
    trig = orb.first_close_above(by_t, level, tick, plan.get("min_volume_x"))
    if trig is None or M.bar_end(trig.t).time() >= nea:
        return dict(row, result="no trigger", or_high=or_high)
    entry = M.clean_price(trig.c, tick)
    upto = [b for b in bars if b.t <= trig.t]
    raw_s, _, _ = orb.raw_stop(plan["stop_rule"], entry, or_high, or_low, tick, upto)
    if raw_s >= entry:
        return dict(row, result="no valid stop", entry=entry)
    stop = M.stop_price(raw_s, entry, tick)
    risk = entry - stop
    raw_t, _ = orb.raw_target(plan["target_rule"], entry, risk)
    target = M.target_price(raw_t, tick)
    qty = qty_rule(risk)
    if qty < 1:
        return dict(row, result="one share risks more than max_loss_per_trade_inr", entry=entry)
    pricing = Pricing(entry, stop, tick, qty, cost_cfg)
    result, when = None, None
    for b in bars:
        if b.t <= trig.t:
            continue
        hit = exit_on_bar(b.o, b.h, b.l, b.c, stop, target)
        if hit:
            result, when = hit, b.t
            break
    if result is None:
        last = bars[-1]
        result, when = ("time", last.c), last.t
    kind, fill = result
    inr, r, cost = pricing.net(kind, fill)
    return dict(row, result=kind, trigger_time=M.fmt_time(M.bar_end(trig.t)), entry=entry, stop=stop,
                target=target, qty=qty, exit_price=round(fill, 2), exit_time=M.fmt_time(M.bar_end(when)),
                gross_r=round((fill - entry) / risk, 3), net_r=round(r, 3), net_inr=round(inr, 2),
                costs_inr=cost)


def portable_rules(plan):
    """A price:X stop or target means nothing on another day: use the same percent of the entry."""
    p = dict(plan)
    if str(p.get("stop_rule", "")).startswith("price:"):
        p["stop_rule"] = "pct:%g" % round((plan["entry"] - plan["stop"]) / plan["entry"] * 100, 4)
    if str(p.get("target_rule", "")).startswith("price:"):
        p["target_rule"] = "pct:%g" % round((plan["target"] - plan["entry"]) / plan["entry"] * 100, 4)
    return p


def history(plan, pool, cfg, qty_rule, cost_cfg):
    plan = portable_rules(plan)
    rows = [plan_rule_on_day(plan, pool[d], d, cfg, qty_rule, cost_cfg) for d in sorted(pool)]
    traded = [r for r in rows if r["result"] in ("target", "stop", "time")]
    n = len(traded)
    out = {"sessions": len(rows), "triggered": n, "rows": rows}
    if n:
        out.update({
            "target": sum(1 for r in traded if r["result"] == "target"),
            "stop": sum(1 for r in traded if r["result"] == "stop"),
            "flat_by": sum(1 for r in traded if r["result"] == "time"),
            "net_positive": sum(1 for r in traded if r["net_r"] > 0),
            "mean_net_r": round(sum(r["net_r"] for r in traded) / n, 3),
            "total_net_r": round(sum(r["net_r"] for r in traded), 2),
            "total_net_inr": round(sum(r["net_inr"] for r in traded), 2),
            "total_costs_inr": round(sum(r["costs_inr"] for r in traded), 2),
        })
    return out


# ------------------------------------------------------------------ verdict

def break_even(pricing, plan):
    """The share of decided replays (target or stop) the target must win, net of costs."""
    win = pricing.net("target", plan["target"])[1]
    loss = -pricing.net("stop", plan["stop"])[1]
    if win <= 0:
        return None, win, loss
    return loss / (win + loss), win, loss


def verdict(mc, be):
    decided = mc["target_first_pct"] + mc["stop_first_pct"]
    lines = []
    if be is None:
        return "The target does not cover the costs: even a hit loses money. Widen the target.", lines
    if decided <= 0:
        return "No replay reached the stop or the target before flat_by.", lines
    got = mc["target_first_pct"] / decided
    diff = (got - be) * 100
    side = "the target" if diff > 0 else "the stop"
    if abs(diff) < 5:
        v = "Close to break-even after costs"
    elif abs(diff) < 15:
        v = "Leans toward %s" % side
    else:
        v = "Strongly toward %s" % side
    v += (": of the replays that hit a side, %.0f%% hit the target first; break-even after costs "
          "needs %.0f%%." % (got * 100, be * 100))
    if mc["flat_by_exit_pct"] > 40:
        lines.append("%.0f%% of the replays hit neither and were closed at flat_by; their price then "
                     "decides much of the result." % mc["flat_by_exit_pct"])
    if (mc["mean_net_r"] > 0) != (diff > 0) and abs(diff) >= 5:
        lines.append("But the average net result is %+.2fR a trade, against the lean, because of the "
                     "replays closed at flat_by." % mc["mean_net_r"])
    return v, lines


# ------------------------------------------------------------------ inputs

def load_plan(a, overrides):
    if a.symbol:
        need = [("--date", a.date), ("--entry", a.entry), ("--stop", a.stop), ("--target", a.target),
                ("--entry-bar", a.entry_bar)]
        miss = [n for n, v in need if v is None]
        if miss:
            raise TMError("without a plan file give %s" % ", ".join(miss))
        tick = float(a.tick or M.tick_for(a.symbol, None, overrides)[0])
        return {"symbol": M.normalize_symbol(a.symbol), "session": a.date, "entry": a.entry, "stop": a.stop,
                "target": a.target, "trigger_bar": a.entry_bar, "tick": tick,
                "stop_rule": "pct:%g" % ((a.entry - a.stop) / a.entry * 100),
                "target_rule": "r:%g" % ((a.target - a.entry) / (a.entry - a.stop)), "setup": "orb"}
    path = a.plan or trader_path("plan.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise TMError("no plan at %s: run /nse-exit plan --write first, or pass --symbol and the prices" % path)
    except ValueError:
        raise TMError("%s is not valid JSON" % path)


def check_plan(plan, cfg, cal):
    for k in ("symbol", "session", "entry", "stop", "target", "trigger_bar", "tick"):
        if plan.get(k) in (None, ""):
            raise TMError("the plan has no %s" % k)
    if plan.get("side", "long") != "long":
        raise TMError("v1 replays long plans only")
    if not plan["stop"] < plan["entry"] < plan["target"]:
        raise TMError("a long plan needs stop < entry < target (got %s / %s / %s)" % (
            plan["stop"], plan["entry"], plan["target"]))
    day = M.parse_date(plan["session"])
    st = cal.status(day)
    if not st.tradable:
        raise TMError(st.reason)
    tb = M.parse_hhmm(plan["trigger_bar"])
    start = M.ist(day.year, day.month, day.day, tb.hour, tb.minute)
    slots = session_slots(day, cfg)
    if start not in slots:
        raise TMError("the entry bar %s is not a 5-minute bar of the session before flat_by %s" % (
            plan["trigger_bar"], cfg["flat_by"]))
    if start.time() < M.OPENING_RANGE_END:
        raise TMError("the entry bar %s is inside the opening range; entries come after %s" % (
            plan["trigger_bar"], M.fmt_time(M.OPENING_RANGE_END)))
    return day, slots.index(start) + 1, len(slots)


def quantity_rule(cfg, cost_cfg, fixed=None):
    """Shares for a given risk a share: --qty, else what max_loss_per_trade_inr allows."""
    if fixed:
        return lambda risk: fixed
    return lambda risk: int(cfg["max_loss_per_trade_inr"] // risk) if risk > 0 else 0


def actual_today(plan, series, day, cfg, pricing, now):
    """What really happened on the plan's own day, once that session is past flat_by."""
    end = flat_by_end(day, cfg)
    if now < end:
        return None
    tb = M.parse_hhmm(plan["trigger_bar"])
    start = M.ist(day.year, day.month, day.day, tb.hour, tb.minute)
    bars = [b for b in series.day(day) if M.is_bar_start(b.t) and start < b.t and M.bar_end(b.t) <= end]
    want = [t for t in session_slots(day, cfg) if t > start]
    if [b.t for b in bars] != want or any(None in (b.o, b.h, b.l, b.c) for b in bars):
        return None
    for b in bars:
        hit = exit_on_bar(b.o, b.h, b.l, b.c, plan["stop"], plan["target"])
        if hit:
            inr, r, _ = pricing.net(*hit)
            return {"result": hit[0], "exit_time": M.fmt_time(M.bar_end(b.t)), "net_r": round(r, 3),
                    "net_inr": round(inr, 2)}
    inr, r, _ = pricing.net("time", bars[-1].c)
    return {"result": "time", "exit_time": M.fmt_time(M.bar_end(bars[-1].t)), "net_r": round(r, 3),
            "net_inr": round(inr, 2)}


# ------------------------------------------------------------------ run

def cmd_run(a):
    cfg, _ = config.load(a.config)
    cal = M.load_calendar(a.holidays)
    overrides = M.load_tick_overrides(a.ticks)
    plan = load_plan(a, overrides)
    if a.offline and not a.now:
        raise TMError("--offline needs --now \"YYYY-MM-DD HH:MM\"")
    now = M.parse_ist(a.now) if a.now else M.now_ist()
    day, first_slot, n_slots = check_plan(plan, cfg, cal)
    if a.replays < 100:
        raise TMError("--replays must be at least 100")
    if a.offline:
        data = yahoo.load_offline(a.offline)
        if plan["symbol"] not in data:
            raise TMError("%s is not in the offline data" % plan["symbol"])
        series = data[plan["symbol"]]
    else:
        series = yahoo.fetch_chart(plan["symbol"], HISTORY_RANGE, "5m")
    pool, dropped = pool_sessions(series, day, cfg, cal)
    if len(pool) < MIN_SESSIONS:
        raise TMError("only %d full sessions before %s (need %d); left out: %s" % (
            len(pool), day, MIN_SESSIONS, dropped or "none"))
    cost_cfg = cfg["costs"]
    qty_rule = quantity_rule(cfg, cost_cfg, a.qty)
    risk = plan["entry"] - plan["stop"]
    qty = qty_rule(risk)
    if qty < 1:
        raise TMError("one share risks %s, more than max_loss_per_trade_inr %s: widen the limit or tighten the stop"
                      % (M.fmt_inr(risk), M.fmt_inr(cfg["max_loss_per_trade_inr"])))
    pricing = Pricing(plan["entry"], plan["stop"], plan["tick"], qty, cost_cfg)
    slots = session_slots(day, cfg)
    checkpoints = {}
    for i, t in enumerate(slots):
        if i >= first_slot and (M.bar_end(t).minute % 60 == 0 or i == n_slots - 1):
            checkpoints[i] = M.fmt_time(M.bar_end(t))
    mc = monte_carlo(plan, pool, first_slot, n_slots, pricing, a.replays, a.seed, a.window, checkpoints)
    mc["fan"] = {checkpoints[k]: v for k, v in sorted(mc["fan"].items())}
    be, win_r, loss_r = break_even(pricing, plan)
    v, extra = verdict(mc, be)
    hist = None if a.no_history or plan.get("setup", "orb") != "orb" else history(plan, pool, cfg, qty_rule, cost_cfg)
    rt_target = costs.round_trip(pricing.fill_in, plan["target"], qty, cost_cfg)
    doc = {"plan": {k: plan.get(k) for k in ("symbol", "session", "entry", "stop", "target", "trigger_bar",
                                             "stop_rule", "target_rule", "tick", "setup")},
           "as_of": now.strftime("%Y-%m-%d %H:%M IST"), "flat_by": cfg["flat_by"], "qty": qty,
           "qty_source": "--qty" if a.qty else "max_loss_per_trade_inr / risk a share",
           "risk_inr": round(pricing.risk_inr, 2),
           "costs": {"rates": {k: cost_cfg[k] for k in costs.RATE_KEYS}, "at_target": rt_target,
                     "still_placeholder": costs.placeholder_keys(cost_cfg)},
           "net_r_at_target": round(win_r, 3), "net_r_at_stop": round(-loss_r, 3),
           "break_even_pct": None if be is None else round(be * 100, 1),
           "pool": {"sessions": len(pool), "first": str(min(pool)), "last": str(max(pool)), "left_out": dropped},
           "mc": mc, "verdict": v, "verdict_notes": extra, "history": hist,
           "actual": actual_today(plan, series, day, cfg, pricing, now), "note": NOTE}
    if a.write:
        write_json(a.out or trader_path("timemachine.json"), doc)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0
    report(doc, plan, a)
    return 0


def report(doc, plan, a):
    mc, s = doc["mc"], M.bare_symbol(plan["symbol"])
    print("Time Machine: %s long %s, entry %s (bar %s), stop %s, target %s, flat by %s IST." % (
        s, plan["session"], M.fmt_number(plan["entry"]), plan["trigger_bar"], M.fmt_number(plan["stop"]),
        M.fmt_number(plan["target"]), doc["flat_by"]))
    p = doc["pool"]
    print("Pool: %d full sessions, %s to %s%s." % (p["sessions"], p["first"], p["last"],
          "; left out " + ", ".join("%d %s" % (n, k) for k, n in sorted(p["left_out"].items())) if p["left_out"] else ""))
    print("Quantity %d (%s), risk %s. Round-trip costs at the target %s (%.2fR)%s." % (
        doc["qty"], doc["qty_source"], M.fmt_inr(doc["risk_inr"]), M.fmt_inr(doc["costs"]["at_target"]["total"]),
        doc["costs"]["at_target"]["total"] / doc["risk_inr"],
        "; %d of %d rates are still PLACEHOLDERS, put your broker's in trader/config.json" % (
            len(doc["costs"]["still_placeholder"]), len(costs.RATE_KEYS)) if doc["costs"]["still_placeholder"] else ""))
    print("Net of costs and %s tick(s) slippage: a target hit is %+.2fR, a stop %+.2fR." % (
        doc["costs"]["rates"]["slippage_ticks"], doc["net_r_at_target"], doc["net_r_at_stop"]))
    print("")
    print("%d replays (seed %d, +-%d bars):" % (mc["replays"], mc["seed"], mc["window_bars"]))
    print("  target first %5.1f%%   stop first %5.1f%%   closed at flat_by %5.1f%%   net positive %5.1f%%" % (
        mc["target_first_pct"], mc["stop_first_pct"], mc["flat_by_exit_pct"], mc["net_positive_pct"]))
    q = mc["net_r_percentiles"]
    print("  net R: average %+.2f; 5th %+.2f, median %+.2f, 95th %+.2f   (average %s a trade)" % (
        mc["mean_net_r"], q["5"], q["50"], q["95"], M.fmt_inr(mc["mean_net_inr"])))
    if mc["fan"]:
        print("  price paths (5th / median / 95th):  " + "  ".join(
            "%s %s/%s/%s" % (t, M.fmt_number(v["5"]), M.fmt_number(v["50"]), M.fmt_number(v["95"]))
            for t, v in list(mc["fan"].items())[:6]))
    print("Verdict: " + doc["verdict"])
    for line in doc["verdict_notes"]:
        print("  " + line)
    h = doc["history"]
    if h is None:
        print("History: skipped (%s)." % ("--no-history" if a.no_history else "only the ORB setup re-runs on past days"))
    elif h["triggered"] == 0:
        print("History: the rule never triggered on the %d past sessions." % h["sessions"])
    else:
        print("")
        print("History, the same rule on %d past sessions: triggered on %d. Target %d, stop %d, flat_by %d; "
              "net positive %d of %d." % (h["sessions"], h["triggered"], h["target"], h["stop"], h["flat_by"],
                                          h["net_positive"], h["triggered"]))
        print("  average %+.2fR a trade, total %+.2fR = %s net (costs paid %s)." % (
            h["mean_net_r"], h["total_net_r"], M.fmt_inr(h["total_net_inr"]), M.fmt_inr(h["total_costs_inr"])))
        for r in [x for x in h["rows"] if x["result"] in ("target", "stop", "time")][-a.show:]:
            print("  %s  in %s at %s  %-6s %s at %s  %+.2fR" % (
                r["date"], r["trigger_time"], M.fmt_number(r["entry"]), r["result"], M.fmt_number(r["exit_price"]),
                r["exit_time"], r["net_r"]))
    if doc["actual"]:
        x = doc["actual"]
        print("What really happened on %s: %s at %s, %+.2fR net (%s)." % (
            plan["session"], x["result"], x["exit_time"], x["net_r"], M.fmt_inr(x["net_inr"])))
    print(NOTE)


def build_parser():
    ap = argparse.ArgumentParser(prog="timemachine.py", description="Replay an NSE plan (simulation only).")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--plan", help="default trader/plan.json")
    r.add_argument("--symbol")
    r.add_argument("--date", help="the plan's session, YYYY-MM-DD")
    r.add_argument("--entry", type=float)
    r.add_argument("--stop", type=float)
    r.add_argument("--target", type=float)
    r.add_argument("--entry-bar", help="the trigger bar's start, HH:MM IST")
    r.add_argument("--tick", type=float)
    r.add_argument("--qty", type=int, help="shares (default: what max_loss_per_trade_inr allows)")
    r.add_argument("--replays", type=int, default=REPLAYS)
    r.add_argument("--seed", type=int, default=SEED)
    r.add_argument("--window", type=int, default=WINDOW)
    r.add_argument("--no-history", action="store_true")
    r.add_argument("--offline", help="saved Yahoo 5-minute charts (60 days)")
    r.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    r.add_argument("--config")
    r.add_argument("--holidays")
    r.add_argument("--ticks")
    r.add_argument("--write", action="store_true", help="save trader/timemachine.json")
    r.add_argument("--out")
    r.add_argument("--json", action="store_true")
    r.add_argument("--show", type=int, default=10)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return cmd_run(a)
    except (TMError, orb.RuleError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
