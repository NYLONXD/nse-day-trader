"""nse-broker: turn a PASSED order into a ticket you place BY HAND in your own broker app, and
keep today's paper journal. Standard library only, Python 3.8+.

This script never connects to a broker or to anything else: it has no network code, reads no
API key, password, PIN or token, and places nothing. You type the ticket into your broker's
app yourself, or you skip it.

  ticket.py ticket                       the ticket for trader/order.json, only if nse-risk PASSED
                                         that exact order (same fingerprint) today, minutes ago
  ticket.py placed --price P [--qty N]   you placed it: record the fill in today's journal
  ticket.py closed SYMBOL --price P      you sold: record the exit and the net result
  ticket.py flatten                      sell tickets for everything still open
  ticket.py status                       today's trades, open positions, loss used, limits left
"""

import argparse
import datetime as dt
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
RISK_MAX_AGE = dt.timedelta(minutes=15)
SL_LIMIT_BUFFER_PCT = 0.3     # an SL order's limit this far under its trigger, so it fills in a fall
FILL_SANITY_PCT = 5           # a fill further than this from the plan is probably a typo
NOTE = "Nothing was sent anywhere. Place it by hand in your own broker app, or skip it."
SHORT_WARNING = ("Never leave a stop-loss SELL and a target SELL resting for the same shares: when one fills, "
                 "the other would sell shares you no longer hold, which is a short.")


class TicketError(Exception):
    pass


def trader_path(name):
    return os.path.join(config.trader_dir(), name)


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise TicketError("no %s at %s" % (what, path))
    except ValueError:
        raise TicketError("%s at %s is not valid JSON" % (what, path))


def write(path, text_or_doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if isinstance(text_or_doc, str):
            f.write(text_or_doc)
        else:
            json.dump(text_or_doc, f, indent=1, ensure_ascii=False)
            f.write("\n")
    os.replace(tmp, path)


def max_entry_for_loss(entry, stop, qty, tick, max_loss, c):
    """The highest price (on the tick) at which `qty` shares still lose at most max_loss at the stop."""
    p, t = M.clean_price(entry, tick), float(tick)
    if costs.loss_at_stop(p, stop, qty, tick, c) > max_loss:
        return None
    while costs.loss_at_stop(p + t, stop, qty, tick, c) <= max_loss:
        p = M.clean_price(p + t, tick)
    return p


# ------------------------------------------------------------------ ticket

def gate(order, risk, cfg, now):
    """Raise TicketError unless this exact order PASSED nse-risk today, recently, in the window."""
    if cfg["paper"] is not True:
        raise TicketError("paper must be true in trader/config.json: v1 is paper only")
    fp = journal.fingerprint(order)
    if risk.get("verdict") != "PASS":
        raise TicketError("nse-risk did not pass this order (%s). Fix it and run /nse-risk again." % risk.get("verdict"))
    if risk.get("fingerprint") != fp:
        raise TicketError("the order changed after nse-risk checked it (fingerprint %s, checked %s). "
                          "Run /nse-risk again." % (fp, risk.get("fingerprint")))
    if risk.get("date") != str(now.date()):
        raise TicketError("the risk check is from %s, not today. Run /nse-risk again." % risk.get("date"))
    checked = M.parse_ist(str(risk.get("as_of", "")).replace(" IST", ""))
    if now - checked > RISK_MAX_AGE or checked > now:
        raise TicketError("the risk check is from %s IST, more than %d minutes ago: prices move. Run /nse-risk again."
                          % (M.fmt_time(checked), RISK_MAX_AGE.seconds // 60))
    phase = M.session_phase(now, cfg["no_entries_after"], cfg["flat_by"])
    if phase != "entries_open":
        raise TicketError("%s IST is outside the entry window (%s to %s). No ticket." % (
            M.fmt_time(now), M.fmt_time(M.OPENING_RANGE_END), cfg["no_entries_after"]))
    day = journal.load(now.date())
    if any(e.get("fingerprint") == fp for e in day["entries"]):
        raise TicketError("you already recorded this order as placed today")
    return fp


def build_ticket(order, cfg, now, fp):
    sym, s = order["symbol"], M.bare_symbol(order["symbol"])
    tick, qty, stop, target = float(order["tick"]), order["qty"], order["stop"], order["target"]
    c = cfg["costs"] if order["order_type"] == "MARKET" else dict(cfg["costs"], slippage_ticks=0)
    cap = max_entry_for_loss(order["entry_used"], stop, qty, tick, cfg["max_loss_per_trade_inr"], c)
    sl_limit = M.round_to_tick(stop * (1 - SL_LIMIT_BUFFER_PCT / 100.0), tick, "down")
    steps = []
    if order["order_type"] == "LIMIT":
        steps.append("BUY %d %s, order type LIMIT at Rs. %s." % (qty, s, M.fmt_number(order["limit_price"])))
    else:
        steps.append("BUY %d %s at MARKET, or a LIMIT buy at Rs. %s so you never pay more than your max loss allows."
                     % (qty, s, M.fmt_number(cap)))
        steps.append("   Do not place it if %s is above Rs. %s: from there %d shares would lose more than your %s at the stop."
                     % (s, M.fmt_number(cap), qty, M.fmt_inr(cfg["max_loss_per_trade_inr"])))
    steps.append("Right after it fills, the stop-loss: SELL %d %s, order type SL-M, trigger Rs. %s." % (qty, s, M.fmt_number(stop)))
    steps.append("   If your broker has no SL-M for intraday equity: order type SL, trigger Rs. %s, limit Rs. %s "
                 "(%.1f%% under, so it fills in a fast fall)." % (M.fmt_number(stop), M.fmt_number(sl_limit), SL_LIMIT_BUFFER_PCT))
    steps.append("The target, Rs. %s, ONE of: (a) an OCO / GTT-OCO order with the stop-loss, if your broker offers "
                 "it for MIS; or (b) no order: /nse-alerts rings at the target, then CANCEL the stop-loss first and "
                 "SELL %d %s." % (M.fmt_number(target), qty, s))
    steps.append("   " + SHORT_WARNING)
    steps.append("By %s IST, if still open: cancel the stop-loss, then SELL %d %s at MARKET. (Your broker also squares "
                 "off MIS positions by itself near the close, often for a fee.)" % (cfg["flat_by"], qty, s))
    steps.append("Tell me the fill: ticket.py placed --price PRICE. Later: ticket.py closed %s --price PRICE." % s)
    lines = ["PAPER TICKET %s IST  (fingerprint %s)" % (now.strftime("%Y-%m-%d %H:%M"), fp),
             NOTE,
             "Valid until %s IST (no_entries_after) and only while the price is near the plan." % cfg["no_entries_after"],
             "",
             "  Exchange NSE | Equity | Product MIS (Intraday) | Symbol %s" % s,
             ""]
    n = 0
    for st in steps:
        if st.startswith("   "):
            lines.append("    " + st.strip())
        else:
            n += 1
            lines.append("  %d. %s" % (n, st))
    lines += ["",
              "Max loss at the stop with costs: %s. Net reward:risk %.2f. Order value %s, no leverage." % (
                  M.fmt_inr(order["max_loss_at_stop_inr"]), order["net_reward_risk"], M.fmt_inr(order["value_inr"])),
              "A stop is a plan, not a guarantee: a gap or a circuit lock can fill worse, or not at all."]
    return "\n".join(lines) + "\n", {"fingerprint": fp, "created": now.strftime("%Y-%m-%d %H:%M IST"),
                                      "symbol": sym, "qty": qty, "stop": stop, "target": target, "tick": tick,
                                      "max_entry": cap, "sl_limit": sl_limit, "order": order, "steps": steps}


def cmd_ticket(a, cfg, now):
    order = read_json(trader_path("order.json"), "order (run: /nse-budget --write)")
    risk = read_json(journal.risk_path(), "risk check (run: /nse-risk)")
    fp = gate(order, risk, cfg, now)
    text, doc = build_ticket(order, cfg, now, fp)
    write(trader_path("ticket.txt"), text)
    write(trader_path("ticket.json"), doc)
    print(text, end="")
    print("Saved trader/ticket.txt.")
    return 0


# ------------------------------------------------------------------ journal

def cmd_placed(a, cfg, now):
    t = read_json(trader_path("ticket.json"), "ticket (run: ticket.py ticket)")
    if not t["created"].startswith(str(now.date())):
        raise TicketError("the ticket is from %s, not today" % t["created"])
    qty = a.qty or t["qty"]
    if qty < 1 or qty > t["qty"]:
        raise TicketError("the quantity must be 1 to %d (the ticket's)" % t["qty"])
    tick = t["tick"]
    if a.price <= 0 or not M.on_tick(a.price, tick):
        raise TicketError("the fill price %s is not on the %s tick" % (a.price, tick))
    if abs(a.price / t["order"]["entry_used"] - 1) * 100 > FILL_SANITY_PCT:
        raise TicketError("a fill of %s is more than %d%% from the planned entry %s: check the number" % (
            M.fmt_number(a.price), FILL_SANITY_PCT, M.fmt_number(t["order"]["entry_used"])))
    day = journal.load(now.date())
    if any(e.get("fingerprint") == t["fingerprint"] for e in day["entries"]):
        raise TicketError("this ticket is already recorded as placed")
    c = cfg["costs"]
    s_out = t["stop"] - c["slippage_ticks"] * tick
    loss = qty * (a.price - s_out) + costs.total(a.price, s_out, qty, c)
    day["entries"].append({"symbol": t["symbol"], "qty": qty, "entry": a.price, "stop": t["stop"], "target": t["target"],
                           "tick": tick, "max_loss_inr": round(loss, 2), "fingerprint": t["fingerprint"],
                           "placed_at": now.strftime("%H:%M"), "closed": False, "exit_price": None, "realised_inr": None})
    journal.save(day)
    print("Recorded: BUY %d %s at %s (%s IST). Max loss at the stop with costs: %s." % (
        qty, M.bare_symbol(t["symbol"]), M.fmt_number(a.price), M.fmt_time(now), M.fmt_inr(loss)))
    if loss > cfg["max_loss_per_trade_inr"]:
        print("Warning: that fill makes the max loss %s, over your %s. Consider selling now." % (
            M.fmt_inr(loss), M.fmt_inr(cfg["max_loss_per_trade_inr"])))
    print("Next: /nse-alerts from-plan, then watch. Make sure your stop-loss order is in.")
    return 0


def cmd_closed(a, cfg, now):
    sym = M.normalize_symbol(a.symbol)
    day = journal.load(now.date())
    open_ = [e for e in day["entries"] if e["symbol"] == sym and not e.get("closed")]
    if not open_:
        raise TicketError("no open %s position in today's journal" % M.bare_symbol(sym))
    e = open_[0]
    if a.price <= 0 or not M.on_tick(a.price, e.get("tick") or M.TICK_DEFAULT):
        raise TicketError("the exit price %s is not on the %s tick" % (a.price, e.get("tick")))
    cost = costs.total(e["entry"], a.price, e["qty"], cfg["costs"])
    net = e["qty"] * (a.price - e["entry"]) - cost
    e.update(closed=True, exit_price=a.price, realised_inr=round(net, 2), costs_inr=cost,
             exit_reason=a.reason, closed_at=now.strftime("%H:%M"))
    journal.save(day)
    print("Recorded: SELL %d %s at %s (%s). Net %s after %s of costs." % (
        e["qty"], M.bare_symbol(sym), M.fmt_number(a.price), a.reason, M.fmt_inr(net), M.fmt_inr(cost)))
    print("Cancel any stop-loss or target order still resting for %s, so it cannot sell shares you no longer hold."
          % M.bare_symbol(sym))
    return 0


def cmd_flatten(a, cfg, now):
    day = journal.load(now.date())
    open_ = [e for e in day["entries"] if not e.get("closed")]
    if not open_:
        print("Nothing open in today's journal.")
        return 0
    print("FLATTEN %s IST. %s" % (M.fmt_time(now), NOTE))
    for i, e in enumerate(open_, 1):
        print("  %d. Cancel the resting stop-loss and target orders for %s, then SELL %d %s at MARKET (MIS)." % (
            i, M.bare_symbol(e["symbol"]), e["qty"], M.bare_symbol(e["symbol"])))
    print("Then record each: ticket.py closed SYMBOL --price PRICE --reason flat_by")
    return 0


def cmd_status(a, cfg, now):
    day = journal.load(now.date())
    used = journal.loss_used(day)
    closed = [e for e in day["entries"] if e.get("closed")]
    realised = sum(e["realised_inr"] for e in closed)
    print("Today %s: %d of %d trades, %d open. Realised %s net. Daily loss used %s of %s." % (
        day["date"], len(day["entries"]), cfg["max_trades_per_day"], len(day["entries"]) - len(closed),
        M.fmt_inr(realised), M.fmt_inr(used), M.fmt_inr(cfg["daily_loss_limit_inr"])))
    for e in day["entries"]:
        if e.get("closed"):
            print("  %-12s %d @ %s -> %s (%s) %s" % (M.bare_symbol(e["symbol"]), e["qty"], M.fmt_number(e["entry"]),
                                                    M.fmt_number(e["exit_price"]), e.get("exit_reason"), M.fmt_inr(e["realised_inr"])))
        else:
            print("  %-12s %d @ %s open, stop %s, target %s, max loss %s" % (
                M.bare_symbol(e["symbol"]), e["qty"], M.fmt_number(e["entry"]), M.fmt_number(e["stop"]),
                M.fmt_number(e["target"]), M.fmt_inr(e["max_loss_inr"])))
    if used >= cfg["daily_loss_limit_inr"]:
        print("The daily loss limit is used up: no more entries today.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="ticket.py", description="NSE paper ticket builder (never calls a broker).")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    common.add_argument("--config")
    sub.add_parser("ticket", parents=[common])
    p = sub.add_parser("placed", parents=[common])
    p.add_argument("--price", type=float, required=True, help="your actual fill price")
    p.add_argument("--qty", type=int)
    c = sub.add_parser("closed", parents=[common])
    c.add_argument("symbol")
    c.add_argument("--price", type=float, required=True, help="your actual exit price")
    c.add_argument("--reason", choices=("stop", "target", "flat_by", "manual"), default="manual")
    sub.add_parser("flatten", parents=[common])
    sub.add_parser("status", parents=[common])
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        cfg, _ = config.load(a.config)
        now = M.parse_ist(a.now) if a.now else M.now_ist()
        return {"ticket": cmd_ticket, "placed": cmd_placed, "closed": cmd_closed, "flatten": cmd_flatten,
                "status": cmd_status}[a.cmd](a, cfg, now)
    except (TicketError, M.MarketError, config.ConfigError) as e:
        print("Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
