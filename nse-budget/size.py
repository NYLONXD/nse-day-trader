"""nse-budget: size one NSE intraday trade to your capital and your max loss, NET OF COSTS.
Long only, whole shares, no leverage. Standard library only, Python 3.8+.
It never places an order: it writes a proposed order for nse-risk to check.

  size.py [--plan trader/plan.json] [--limit PRICE] [--write]
      -> trader/order.json with --write

The size is the smallest of four limits (all from trader/config.json):
  max loss   the most shares whose loss at the stop, with slippage and every charge
             (shared/costs.py), is at most max_loss_per_trade_inr
  position   capital_inr x max_position_pct / entry
  max order  max_order_inr / entry
  cash       capital_inr x (1 - cash_buffer_pct) minus today's open positions / entry.
             v1 uses your own cash only, never the broker's intraday (MIS) margin.
The entry it sizes at: --limit PRICE for a limit order; otherwise the plan's entry, or a
fresh (15 minutes or less) higher price from trader/prices.json: a price that ran up since the
plan means a bigger loss at the same stop. Exit code 0 sized, 1 refused, 2 usage error.
"""

import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))

import config   # noqa: E402
import costs    # noqa: E402
import journal  # noqa: E402
import market   # noqa: E402

M = market
NOTE = ("A proposed order, not a placed one. A stop is a plan, not a guarantee: a gap or a "
        "circuit lock can lose more than the max loss shown.")


class SizeError(Exception):
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


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise SizeError("no %s at %s" % (what, path))
    except ValueError:
        raise SizeError("%s at %s is not valid JSON" % (what, path))


def size(plan, cfg, now, limit=None, prices_path=None):
    """The proposed order dict, or raises SizeError with the reason and the fix."""
    sym = M.normalize_symbol(plan["symbol"])
    if plan.get("side", "long") != "long":
        raise SizeError("v1 sizes long trades only (no shorts, options, futures or leverage)")
    blocked = {M.normalize_symbol(s) for s in cfg["blocked"]}
    if sym in blocked:
        raise SizeError("%s is on your blocked list in trader/config.json" % M.bare_symbol(sym))
    if plan.get("session") != str(now.date()):
        raise SizeError("the plan is for %s and today is %s: make a new plan with /nse-entry and /nse-exit"
                        % (plan.get("session"), now.date()))
    tick = float(plan["tick"])
    stop, target = plan["stop"], plan["target"]
    c = cfg["costs"]
    notes = []
    if limit is not None:
        if not M.on_tick(limit, tick):
            raise SizeError("the limit price %s is not on the %s tick" % (limit, tick))
        entry, source, order_type = float(limit), "your limit price", "LIMIT"
        e_fill = entry     # a limit buy fills at its price or better
    else:
        entry, source, order_type = float(plan["entry"]), "the plan's entry", "MARKET"
        p, why = journal.fresh_price(sym, now, prices_path)
        if p is not None and p > entry:
            entry, source = M.clean_price(p, tick), "a fresher, higher price (%s)" % why
        elif p is None:
            notes.append("Sized at the plan's entry (%s)." % why)
        e_fill = entry + c["slippage_ticks"] * tick
    if entry <= stop:
        raise SizeError("the price %s is at or under the stop %s: the setup is gone" % (
            M.fmt_number(entry), M.fmt_number(stop)))
    if entry >= target:
        raise SizeError("the price %s is at or over the target %s: the move already happened" % (
            M.fmt_number(entry), M.fmt_number(target)))
    lc = dict(c) if limit is None else dict(c, slippage_ticks=0)
    today = journal.load(now.date())
    open_value = sum(e["qty"] * e["entry"] for e in today["entries"] if not e.get("closed"))
    cash = cfg["capital_inr"] * (1 - cfg["cash_buffer_pct"] / 100.0) - open_value
    limits = {
        "max loss": costs.max_qty_for_loss(entry, stop, tick, cfg["max_loss_per_trade_inr"], lc),
        "position": int(cfg["capital_inr"] * cfg["max_position_pct"] / 100.0 // e_fill),
        "max order": int(cfg["max_order_inr"] // e_fill),
        "cash": max(0, int(cash // e_fill)),
    }
    decided = min(limits, key=lambda k: (limits[k], list(limits).index(k)))
    qty = limits[decided]
    if qty < 1:
        fixes = {"max loss": "a closer stop from /nse-exit, or a higher max_loss_per_trade_inr",
                 "position": "a higher max_position_pct", "max order": "a higher max_order_inr",
                 "cash": "less money in today's open positions, or more capital_inr"}
        raise SizeError("the %s limit allows 0 shares at %s. Fix: %s." % (decided, M.fmt_inr(entry), fixes[decided]))
    value = round(qty * e_fill, 2)
    if value < cfg["min_order_inr"]:
        raise SizeError("%d shares = %s, under min_order_inr %s: costs would eat it" % (
            qty, M.fmt_inr(value), M.fmt_inr(cfg["min_order_inr"])))
    loss = costs.loss_at_stop(entry, stop, qty, tick, lc)
    gain = costs.gain_at_target(entry, target, qty, tick, lc)
    e_in, s_out = costs.fills(entry, stop, tick, lc)
    net_rr = round(gain / loss, 2)
    if net_rr < cfg["min_reward_risk"]:
        notes.append("Net reward:risk %.2f is under min_reward_risk %.2f: nse-risk will block it." % (
            net_rr, cfg["min_reward_risk"]))
    placeholders = costs.placeholder_keys(c)
    if placeholders:
        notes.append("%d of %d cost rates are still placeholders; put your broker's in trader/config.json."
                     % (len(placeholders), len(costs.RATE_KEYS)))
    return {
        "paper": True, "symbol": sym, "exchange": "NSE", "side": "BUY", "product": "MIS (intraday)",
        "order_type": order_type, "limit_price": limit, "qty": qty, "stop": stop, "target": target,
        "tick": tick, "flat_by": cfg["flat_by"], "session": plan["session"],
        "entry_used": entry, "entry_source": source, "value_inr": value,
        "max_loss_at_stop_inr": round(loss, 2), "gain_at_target_inr": round(gain, 2),
        "costs_at_stop_inr": costs.total(e_in, s_out, qty, lc),
        "slippage_at_stop_inr": round(qty * ((e_in - entry) + (stop - s_out)), 2),
        "costs_at_target_inr": costs.total(e_in, target, qty, lc),
        "net_reward_risk": net_rr, "gross_reward_risk": plan.get("reward_risk"),
        "limits": limits, "decided_by": decided, "capital_inr": cfg["capital_inr"],
        "open_positions_inr": round(open_value, 2), "notes": notes,
        "as_of": now.strftime("%Y-%m-%d %H:%M IST"), "note": NOTE,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="size.py", description="NSE position sizer (paper, long only, no leverage).")
    ap.add_argument("--plan", help="default trader/plan.json")
    ap.add_argument("--limit", type=float, help="size a LIMIT buy at this price (on the tick)")
    ap.add_argument("--prices", help="default trader/prices.json")
    ap.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    ap.add_argument("--config")
    ap.add_argument("--write", action="store_true", help="save trader/order.json")
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg, _ = config.load(a.config)
        plan = read_json(a.plan or trader_path("plan.json"), "plan (run: /nse-exit plan --write)")
        now = M.parse_ist(a.now) if a.now else M.now_ist()
        order = size(plan, cfg, now, a.limit, a.prices)
    except (SizeError, M.MarketError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else "Refused: %s" % e)
        return 1
    path = None
    if a.write:
        path = a.out or trader_path("order.json")
        write_json(path, order)
    if a.json:
        print(json.dumps(order, indent=1, ensure_ascii=False))
        return 0
    o, s = order, M.bare_symbol(order["symbol"])
    print("Size for %s (long, %s, paper), entry %s from %s:" % (s, o["order_type"], M.fmt_number(o["entry_used"]),
                                                               o["entry_source"]))
    for k, n in o["limits"].items():
        print("  %-10s %6d shares%s" % (k, n, "   <- decides it" if k == o["decided_by"] else ""))
    print("  %d shares = %s of %s capital (no leverage)." % (o["qty"], M.fmt_inr(o["value_inr"]), M.fmt_inr(o["capital_inr"])))
    print("  At the stop %s: lose %s, of which %s is charges and %s slippage." % (
        M.fmt_number(o["stop"]), M.fmt_inr(o["max_loss_at_stop_inr"]), M.fmt_inr(o["costs_at_stop_inr"]),
        M.fmt_inr(o["slippage_at_stop_inr"])))
    print("  At the target %s: make %s after %s of costs. Net reward:risk %.2f." % (
        M.fmt_number(o["target"]), M.fmt_inr(o["gain_at_target_inr"]), M.fmt_inr(o["costs_at_target_inr"]),
        o["net_reward_risk"]))
    for n in o["notes"]:
        print("  Note: " + n)
    print(NOTE)
    print("Wrote %s. Next: /nse-risk to check it." % path if path else "Not saved (add --write).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
