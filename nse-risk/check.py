"""nse-risk: check one proposed NSE order against every rule before a ticket can be built.
Standard library only, Python 3.8+. It never places an order and never changes a limit.

  check.py [--order trader/order.json] [--now "YYYY-MM-DD HH:MM"]
      PASS or BLOCK, check by check, and trader/state/risk.json with the verdict and the
      order's fingerprint (nse-broker builds a ticket only for a PASSED, unchanged order).

It re-computes every number itself (it does not trust order.json): the loss at the stop with
slippage and every charge (shared/costs.py), net reward:risk, today's loss used and trades
from the journal (shared/journal.py), the entry window and the calendar (shared/market.py).
Exit code 0 PASS, 1 BLOCK (each block gives the reason and the fix), 2 usage error.
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
NOTE = "A PASS is a rules check, not a recommendation. Nothing was placed."


class RiskError(Exception):
    pass


def trader_path(name):
    return os.path.join(config.trader_dir(), name)


def read_json(path, what, required=True):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    except ValueError:
        raise RiskError("%s at %s is not valid JSON" % (what, path))


def write_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


class Checks(object):
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail, warn=False):
        self.rows.append({"check": name, "result": "PASS" if ok else ("WARN" if warn else "BLOCK"), "detail": detail})

    def warn(self, name, detail):
        self.rows.append({"check": name, "result": "WARN", "detail": detail})

    def blocked(self):
        return [r for r in self.rows if r["result"] == "BLOCK"]


def check(order, plan, cfg, cal, now, prices_path=None, tm=None):
    c = Checks()
    # format
    try:
        sym = M.normalize_symbol(order.get("symbol"))
        c.add("symbol", True, "%s on NSE" % M.bare_symbol(sym))
    except M.MarketError as e:
        c.add("symbol", False, str(e))
        return c, None
    qty = order.get("qty")
    ok_qty = isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1
    c.add("quantity", ok_qty, "%s whole shares" % qty if ok_qty else "quantity must be a whole number of shares, 1 or more")
    c.add("long only", order.get("side") == "BUY",
          "a buy" if order.get("side") == "BUY" else "v1 checks buys only: no shorts, options, futures or leverage")
    c.add("product", str(order.get("product", "")).startswith("MIS"),
          "intraday (MIS)" if str(order.get("product", "")).startswith("MIS") else "v1 checks intraday (MIS) orders only")
    otype = order.get("order_type")
    c.add("order type", otype in ("MARKET", "LIMIT"), otype if otype in ("MARKET", "LIMIT") else "MARKET or LIMIT only")
    tick = float(order.get("tick") or (plan or {}).get("tick") or M.TICK_DEFAULT)
    stop, target = order.get("stop"), order.get("target")
    for name, p in (("stop", stop), ("target", target)) + ((("limit price", order.get("limit_price")),) if otype == "LIMIT" else ()):
        if p is None:
            c.add(name, False, "missing: every order needs a stop and a target from /nse-exit")
        else:
            c.add("%s tick" % name, M.on_tick(p, tick), "%s on the %s tick" % (M.fmt_number(p), tick)
                  if M.on_tick(p, tick) else "%s is not on the stock's %s tick: the broker would reject it" % (p, tick))
    if not ok_qty or stop is None or target is None:
        return c, None
    # who and when
    blocked = {M.normalize_symbol(s) for s in cfg["blocked"]}
    c.add("blocked list", sym not in blocked, "not on your blocked list" if sym not in blocked
          else "%s is on your blocked list" % M.bare_symbol(sym))
    st = cal.status(now.date())
    c.add("calendar", st.tradable, st.reason)
    c.add("session", order.get("session") == str(now.date()),
          "for today" if order.get("session") == str(now.date())
          else "the order is for %s, today is %s: make a new plan" % (order.get("session"), now.date()))
    phase = M.session_phase(now, cfg["no_entries_after"], cfg["flat_by"])
    reasons = {"before_open": "NSE has not opened", "opening_range": "the opening range is still forming (until %s)"
               % M.fmt_time(M.OPENING_RANGE_END), "manage_only": "it is past no_entries_after %s" % cfg["no_entries_after"],
               "flat": "it is past flat_by %s" % cfg["flat_by"], "closed": "the session has closed"}
    c.add("entry window", phase == "entries_open", "%s IST is inside the entry window (%s to %s)" % (
        M.fmt_time(now), M.fmt_time(M.OPENING_RANGE_END), cfg["no_entries_after"]) if phase == "entries_open"
        else "%s IST: %s" % (M.fmt_time(now), reasons[phase]))
    # the price it measures from
    if otype == "LIMIT":
        entry, lc = float(order["limit_price"]), dict(cfg["costs"], slippage_ticks=0)
        c.add("entry price", True, "the limit price %s" % M.fmt_number(entry))
    else:
        entry, lc = float(order.get("entry_used") or (plan or {}).get("entry")), cfg["costs"]
        p, why = journal.fresh_price(sym, now, prices_path)
        if p is not None and M.clean_price(p, tick) > entry:
            entry = M.clean_price(p, tick)
            c.warn("entry price", "measured from a fresher, higher price %s (%s)" % (M.fmt_number(entry), why))
        elif p is None:
            c.warn("entry price", "measured from the order's entry %s (%s)" % (M.fmt_number(entry), why))
        else:
            c.add("entry price", True, "the order's entry %s (fresh price %s is not higher)" % (M.fmt_number(entry), M.fmt_number(p)))
    c.add("stop side", stop < entry, "stop %s under the entry %s" % (M.fmt_number(stop), M.fmt_number(entry))
          if stop < entry else "the stop %s is not under the entry %s" % (M.fmt_number(stop), M.fmt_number(entry)))
    c.add("target side", target > entry, "target %s over the entry" % M.fmt_number(target) if target > entry
          else "the price %s is already at or over the target %s: the move happened" % (M.fmt_number(entry), M.fmt_number(target)))
    if not (stop < entry < target):
        return c, None
    # circuit limits
    limits = (plan or {}).get("limits")
    if limits:
        c.add("upper circuit", entry < limits["upper"] - tick and target <= limits["upper"],
              "entry and target under the upper circuit %s" % M.fmt_number(limits["upper"])
              if entry < limits["upper"] - tick and target <= limits["upper"]
              else "at or above the upper circuit %s: no sellers there, or the target cannot fill" % M.fmt_number(limits["upper"]))
        if stop < limits["lower"]:
            c.warn("lower circuit", "the stop %s is under the lower circuit %s: in a lock you may not be able to sell"
                   % (M.fmt_number(stop), M.fmt_number(limits["lower"])))
    elif (plan or {}).get("band") == "No Band":
        c.add("circuit", True, "no fixed price band (an F&O stock): no circuit limit applies")
    else:
        c.warn("circuit", "circuit limits unknown (band unknown); run /nse-scanner daily for them")
    # money
    loss = costs.loss_at_stop(entry, stop, order["qty"], tick, lc)
    gain = costs.gain_at_target(entry, target, order["qty"], tick, lc)
    mx = cfg["max_loss_per_trade_inr"]
    c.add("max loss", loss <= mx + 1e-9, "%s at the stop with costs and slippage, limit %s" % (M.fmt_inr(loss), M.fmt_inr(mx))
          if loss <= mx + 1e-9 else "%s at the stop with costs and slippage is over max_loss_per_trade_inr %s. "
          "Fix: fewer shares (/nse-budget again) or a closer stop" % (M.fmt_inr(loss), M.fmt_inr(mx)))
    rr = gain / loss if loss > 0 else 0
    c.add("reward:risk", rr >= cfg["min_reward_risk"], "net %.2f, at least %.2f" % (rr, cfg["min_reward_risk"])
          if rr >= cfg["min_reward_risk"] else "net reward:risk %.2f (after costs) is under min_reward_risk %.2f. "
          "Fix: a farther target or a closer stop from /nse-exit (if the price ran on: exit.py plan "
          "--entry PRICE re-plans from it), or skip it" % (rr, cfg["min_reward_risk"]))
    day = journal.load(now.date())
    used = journal.loss_used(day)
    dl = cfg["daily_loss_limit_inr"]
    c.add("daily loss", used + loss <= dl + 1e-9, "%s used today + %s = %s of %s" % (
        M.fmt_inr(used), M.fmt_inr(loss), M.fmt_inr(used + loss), M.fmt_inr(dl)) if used + loss <= dl + 1e-9
        else "%s used today + this trade's %s is over daily_loss_limit_inr %s. Stop for today" % (
        M.fmt_inr(used), M.fmt_inr(loss), M.fmt_inr(dl)))
    n = len(day["entries"])
    c.add("trades today", n < cfg["max_trades_per_day"], "%d of %d used" % (n, cfg["max_trades_per_day"])
          if n < cfg["max_trades_per_day"] else "%d of %d trades already today. Stop for today" % (n, cfg["max_trades_per_day"]))
    c.add("duplicate", sym not in journal.symbols_entered(day), "no %s entry yet today" % M.bare_symbol(sym)
          if sym not in journal.symbols_entered(day) else "you already entered %s today" % M.bare_symbol(sym))
    e_fill = costs.fills(entry, stop, tick, lc)[0]
    value = order["qty"] * e_fill
    open_value = sum(e["qty"] * e["entry"] for e in day["entries"] if not e.get("closed"))
    cap = cfg["capital_inr"]
    checks = [("min order", value >= cfg["min_order_inr"], "under min_order_inr %s" % M.fmt_inr(cfg["min_order_inr"])),
              ("max order", value <= cfg["max_order_inr"] + 1e-9, "over max_order_inr %s" % M.fmt_inr(cfg["max_order_inr"])),
              ("position", value <= cap * cfg["max_position_pct"] / 100.0 + 1e-9,
               "over max_position_pct %s%% of capital" % cfg["max_position_pct"]),
              ("cash, no leverage", value + open_value <= cap * (1 - cfg["cash_buffer_pct"] / 100.0) + 1e-9,
               "with today's open positions %s it would use more than your cash less the %s%% buffer (v1 never uses "
               "the broker's margin)" % (M.fmt_inr(open_value), cfg["cash_buffer_pct"]))]
    for name, ok, why in checks:
        c.add(name, ok, "%s order value" % M.fmt_inr(value) if ok else "%s: %s" % (M.fmt_inr(value), why))
    # information only
    if tm:
        same = all(tm["plan"].get(k) == (plan or {}).get(k) for k in ("symbol", "session", "entry", "stop", "target"))
        if not same:
            c.warn("time machine", "trader/timemachine.json is for another plan; replay this one with /nse-timemachine")
        elif tm["mc"]["mean_net_r"] < 0 or "toward the stop" in tm["verdict"]:
            c.warn("time machine", "the replays lean against this plan: %s (average %+.2fR net)" % (
                tm["verdict"].split(":")[0], tm["mc"]["mean_net_r"]))
        else:
            c.add("time machine", True, "%s (average %+.2fR net)" % (tm["verdict"].split(":")[0], tm["mc"]["mean_net_r"]))
    else:
        c.warn("time machine", "not replayed; /nse-timemachine shows the odds net of costs")
    ph = costs.placeholder_keys(cfg["costs"])
    if ph:
        c.warn("cost rates", "%d of %d are still placeholders: the costs above are estimates" % (len(ph), len(costs.RATE_KEYS)))
    return c, {"entry": entry, "loss_at_stop_inr": round(loss, 2), "gain_at_target_inr": round(gain, 2),
               "net_reward_risk": round(rr, 2), "daily_loss_used_inr": used, "value_inr": round(value, 2)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check.py", description="NSE risk check (paper, long only).")
    ap.add_argument("--order", help="default trader/order.json")
    ap.add_argument("--plan", help="default trader/plan.json")
    ap.add_argument("--prices", help="default trader/prices.json")
    ap.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    ap.add_argument("--config")
    ap.add_argument("--holidays")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg, _ = config.load(a.config)
        cal = M.load_calendar(a.holidays)
        order = read_json(a.order or trader_path("order.json"), "order (run: /nse-budget --write)")
        plan = read_json(a.plan or trader_path("plan.json"), "plan", required=False)
        tm = read_json(trader_path("timemachine.json"), "time machine", required=False)
        now = M.parse_ist(a.now) if a.now else M.now_ist()
        c, nums = check(order, plan, cfg, cal, now, a.prices, tm)
    except (RiskError, M.MarketError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else str(e))
        return 1
    verdict = "BLOCK" if c.blocked() else "PASS"
    doc = {"date": str(now.date()), "as_of": now.strftime("%Y-%m-%d %H:%M IST"), "verdict": verdict,
           "fingerprint": journal.fingerprint(order), "numbers": nums, "checks": c.rows, "order": order, "note": NOTE}
    if not a.no_write:
        write_json(journal.risk_path(), doc)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        print("Risk check: %s %d shares of %s (%s), %s IST." % (
            order.get("side"), order.get("qty") or 0, M.bare_symbol(order.get("symbol") or "?"),
            order.get("order_type"), now.strftime("%Y-%m-%d %H:%M")))
        for r in c.rows:
            print("  %-5s %-17s %s" % (r["result"], r["check"], r["detail"]))
        if verdict == "PASS":
            print("PASS: every rule holds (fingerprint %s). Next: /nse-broker builds the ticket you place by hand." % doc["fingerprint"])
        else:
            print("BLOCK: %d rule(s) broken. Fix them and check again; never change a limit just to pass." % len(c.blocked()))
        print(NOTE)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
