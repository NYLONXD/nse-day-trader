"""nse-alerts: price and time alerts for an NSE intraday day. Standard library only, Python 3.8+.
It never places an order.

  alerts.py quote SYMBOL [--write]       latest price and its age; --write -> trader/prices.json
  alerts.py add SYMBOL above|below PRICE [--note TEXT]
  alerts.py add-time HH:MM [--note TEXT]
  alerts.py from-plan                    stop (below), target (above), no_entries_after, flat_by
  alerts.py bells                        09:15 open, 09:30 range complete, 15:30 close
  alerts.py list | clear
  alerts.py check                        rings what has happened (exit 0 if something rang)
  alerts.py watch [--until HH:MM] [--every 60]

Alerts live in trader/alerts.json, one session at a time. A price alert rings when a completed
5-minute bar since the alert was made touched the price, or the latest price did. A time alert
rings at its IST time on a trading day. Each rings once. Yahoo data is free and can be late.
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
NOTE = "An alert is information, not an order. Nothing was placed."


class AlertError(Exception):
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


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError:
        raise AlertError("%s is not valid JSON; fix or remove it" % path)


# ------------------------------------------------------------------ clock and data

class Clock(object):
    def __init__(self, pinned=None):
        self.pinned = pinned

    def now(self):
        return self.pinned if self.pinned is not None else M.now_ist()

    def cutoff(self):
        return self.pinned if self.pinned is not None else M.now_ist() - yahoo.SETTLE

    def sleep(self, seconds):
        if self.pinned is not None:
            import datetime as _dt
            self.pinned = self.pinned + _dt.timedelta(seconds=seconds)
        else:
            yahoo.SLEEP(seconds)


class Source(object):
    def __init__(self, offline=None):
        self.data = yahoo.load_offline(offline) if offline else None
        self.cache = {}

    def series(self, sym, fresh=True):
        if self.data is not None:
            if sym not in self.data:
                raise yahoo.DataError("%s is not in the offline data" % sym)
            return self.data[sym]
        if fresh or sym not in self.cache:
            self.cache[sym] = yahoo.fetch_chart(sym, "1d", "5m")
        return self.cache[sym]

    def quote(self, sym, clock):
        """(price, IST time, how). Live: Yahoo's latest trade. Offline: the last completed bar's
        close by the pinned clock (Yahoo's meta is the end of the day, so it is not used)."""
        s = self.series(sym)
        tick = M.tick_for(sym, s.prices(375))[0]
        if self.data is None and s.meta.get("regularMarketPrice") and s.meta.get("regularMarketTime"):
            return (M.clean_price(s.meta["regularMarketPrice"], tick), M.to_ist(s.meta["regularMarketTime"]),
                    "Yahoo's latest trade")
        day = clock.now().date()
        bars = orb.day_bars(s, day, clock.cutoff())
        if not bars:
            raise AlertError("no completed bar for %s on %s yet" % (M.bare_symbol(sym), day))
        last = bars[max(bars)]
        return M.clean_price(last.c, tick), M.bar_end(last.t), "the close of the %s bar" % M.fmt_time(last.t)


# ------------------------------------------------------------------ the alert file

def load_alerts(today):
    doc = read_json(trader_path("alerts.json"), {"session": str(today), "alerts": []})
    if doc.get("session") != str(today):
        doc = {"session": str(today), "alerts": [], "note": "older alerts were for %s" % doc.get("session")}
    return doc


def add(doc, alert):
    for a in doc["alerts"]:
        if all(a.get(k) == alert.get(k) for k in ("symbol", "kind", "price", "time")):
            return False
    alert["id"] = (max([a["id"] for a in doc["alerts"]] or [0]) + 1)
    alert.setdefault("fired_at", None)
    doc["alerts"].append(alert)
    return True


def describe(a):
    if a["kind"] == "time":
        return "%s IST%s" % (a["time"], ": " + a["note"] if a.get("note") else "")
    return "%s %s %s%s" % (M.bare_symbol(a["symbol"]), a["kind"], M.fmt_number(a["price"]),
                           " (" + a["note"] + ")" if a.get("note") else "")


# ------------------------------------------------------------------ evaluation

def evaluate(doc, src, clock, cal):
    """Ring every alert that has happened and was not rung. Returns [(alert, message)]."""
    now = clock.now()
    st = cal.status(now.date())
    if not st.tradable:
        raise AlertError(st.reason + " Nothing to watch.")
    rang = []
    for a in doc["alerts"]:
        if a.get("fired_at"):
            continue
        if a["kind"] == "time":
            t = M.parse_hhmm(a["time"])
            if now.time() >= t:
                a["fired_at"] = now.strftime("%H:%M")
                rang.append((a, "%s IST%s: %s" % (a["time"], "" if M.fmt_time(now) == a["time"]
                                                  else " (now %s)" % M.fmt_time(now), a.get("note") or "time alert")))
            continue
        try:
            s = src.series(a["symbol"], fresh=False)
        except yahoo.DataError as e:
            rang.append((None, "%s: %s" % (M.bare_symbol(a["symbol"]), e)))
            continue
        since = M.parse_ist(a["created"]) if a.get("created") else M.session_open(now.date())
        bars = orb.day_bars(s, now.date(), clock.cutoff())
        tick = M.tick_for(a["symbol"], s.prices(375))[0]
        hit = None
        for t in sorted(bars):
            if M.bar_end(t) <= since:
                continue
            b = bars[t]
            if b.h is None or b.l is None:
                continue
            if a["kind"] == "above" and b.h >= a["price"]:
                hit = (M.clean_price(b.h, tick), M.fmt_time(t))
            elif a["kind"] == "below" and b.l <= a["price"]:
                hit = (M.clean_price(b.l, tick), M.fmt_time(t))
            if hit:
                break
        if hit is None and src.data is None:
            try:
                p, when, _ = src.quote(a["symbol"], clock)
                if (a["kind"] == "above" and p >= a["price"]) or (a["kind"] == "below" and p <= a["price"]):
                    hit = (p, "latest trade at %s" % M.fmt_time(when))
            except AlertError:
                pass
        if hit:
            a["fired_at"] = now.strftime("%H:%M")
            msg = "%s %s %s: touched %s at the %s bar." % (M.bare_symbol(a["symbol"]), a["kind"],
                                                          M.fmt_number(a["price"]), M.fmt_number(hit[0]), hit[1])
            if a.get("note"):
                msg += " " + a["note"]
            for other in doc["alerts"]:      # the plan's stop and target cancel each other (like OCO)
                if other is not a and a.get("group") and other.get("group") == a["group"] and not other.get("fired_at"):
                    other["fired_at"] = "cancelled"
                    msg += " (The %s alert is cancelled.)" % other["kind"]
            rang.append((a, msg))
    return rang


# ------------------------------------------------------------------ commands

def cmd_quote(a, cfg, cal, src, clock):
    sym = M.normalize_symbol(a.symbol)
    p, when, how = src.quote(sym, clock)
    age = (clock.now() - when).total_seconds() / 60.0
    print("%s %s at %s IST (%s, %.0f min old)." % (M.bare_symbol(sym), M.fmt_inr(p), M.fmt_time(when), how, age))
    if age > 15 and M.session_phase(clock.now(), cfg["no_entries_after"], cfg["flat_by"]) not in ("before_open", "closed"):
        print("  Warning: older than 15 minutes; nse-budget and nse-risk will not use it.")
    if a.write:
        path = trader_path("prices.json")
        doc = read_json(path, {}) or {}
        doc[sym] = {"price": p, "time": when.strftime("%Y-%m-%d %H:%M IST"), "source": how}
        write_json(path, doc)
        print("  Wrote %s." % path)
    return 0


def cmd_add(a, cfg, cal, src, clock):
    doc = load_alerts(clock.now().date())
    now = clock.now().strftime("%Y-%m-%d %H:%M")
    if a.cmd == "add-time":
        alert = {"kind": "time", "time": M.fmt_time(M.parse_hhmm(a.time)), "note": a.note or ""}
    else:
        if a.price <= 0:
            raise AlertError("the price must be above 0")
        alert = {"kind": a.direction, "symbol": M.normalize_symbol(a.symbol), "price": a.price,
                 "note": a.note or "", "created": now}
    made = add(doc, alert)
    write_json(trader_path("alerts.json"), doc)
    print(("Added: %s" if made else "Already there: %s") % describe(alert))
    return 0


def cmd_from_plan(a, cfg, cal, src, clock):
    plan = read_json(trader_path("plan.json"))
    if not plan:
        raise AlertError("no trader/plan.json: run /nse-exit plan --write first")
    if plan.get("session") != str(clock.now().date()):
        raise AlertError("the plan is for %s, today is %s" % (plan.get("session"), clock.now().date()))
    doc = load_alerts(clock.now().date())
    sym, now = plan["symbol"], clock.now().strftime("%Y-%m-%d %H:%M")
    rows = [{"kind": "below", "symbol": sym, "price": plan["stop"], "created": now, "group": "plan",
             "note": "STOP: your plan says sell now (check your stop-loss order filled)"},
            {"kind": "above", "symbol": sym, "price": plan["target"], "created": now, "group": "plan",
             "note": "TARGET: your plan says sell now (cancel the resting stop-loss first)"},
            {"kind": "time", "time": cfg["no_entries_after"], "note": "no new entries from now (no_entries_after)"},
            {"kind": "time", "time": cfg["flat_by"], "note": "FLAT BY: sell anything still open, at market"}]
    for r in rows:
        print(("Added: %s" if add(doc, r) else "Already there: %s") % describe(r))
    write_json(trader_path("alerts.json"), doc)
    return 0


def cmd_bells(a, cfg, cal, src, clock):
    doc = load_alerts(clock.now().date())
    rows = [{"kind": "time", "time": M.fmt_time(M.SESSION_OPEN), "note": "NSE opening bell"},
            {"kind": "time", "time": M.fmt_time(M.OPENING_RANGE_END), "note": "opening range complete; entries may trigger"},
            {"kind": "time", "time": M.fmt_time(M.SESSION_CLOSE), "note": "NSE close"}]
    for r in rows:
        print(("Added: %s" if add(doc, r) else "Already there: %s") % describe(r))
    write_json(trader_path("alerts.json"), doc)
    return 0


def cmd_list(a, cfg, cal, src, clock):
    doc = load_alerts(clock.now().date())
    if a.cmd == "clear":
        write_json(trader_path("alerts.json"), {"session": doc["session"], "alerts": []})
        print("Cleared %d alert(s)." % len(doc["alerts"]))
        return 0
    if not doc["alerts"]:
        print("No alerts for %s." % doc["session"])
        return 1
    for x in doc["alerts"]:
        state = ("cancelled" if x.get("fired_at") == "cancelled" else "rang at " + x["fired_at"]) if x.get("fired_at") else "waiting"
        print("  #%d %-48s %s" % (x["id"], describe(x), state))
    return 0


def ring(rang, a):
    for alert, msg in rang:
        print("%sRING [%s] %s" % ("\a" if a.bell else "", alert["fired_at"] if alert else "--", msg))


def cmd_check(a, cfg, cal, src, clock):
    doc = load_alerts(clock.now().date())
    if not doc["alerts"]:
        raise AlertError("no alerts for today: add some (from-plan, bells, add SYMBOL above PRICE)")
    rang = evaluate(doc, src, clock, cal)
    write_json(trader_path("alerts.json"), doc)
    ring(rang, a)
    waiting = sum(1 for x in doc["alerts"] if not x.get("fired_at"))
    print("%d rang, %d waiting, at %s IST. %s" % (len(rang), waiting, M.fmt_time(clock.now()), NOTE))
    return 0 if any(x for x, _ in rang) else 1


ALERT_KEY = ("id", "symbol", "kind", "price", "time")


def refresh(doc, today):
    """Re-read trader/alerts.json so alerts added in another window (a second stock's from-plan)
    are watched too, and cleared ones stay cleared; what already rang here stays rung.
    Returns (the fresh doc, the alerts new since the last read)."""
    disk = load_alerts(today)
    mine = {tuple(x.get(k) for k in ALERT_KEY): x for x in doc["alerts"]}
    new = []
    for x in disk["alerts"]:
        old = mine.get(tuple(x.get(k) for k in ALERT_KEY))
        if old is None:
            new.append(x)
        elif old.get("fired_at") and not x.get("fired_at"):
            x["fired_at"] = old["fired_at"]
    return disk, new


def cmd_watch(a, cfg, cal, src, clock):
    doc = load_alerts(clock.now().date())
    if not doc["alerts"]:
        raise AlertError("no alerts for today")
    until = M.parse_hhmm(a.until or cfg["flat_by"])
    any_rang = False
    first = True
    while True:
        if not first:
            doc, new = refresh(doc, clock.now().date())
            for x in new:
                print("Now also watching: %s" % describe(x))
        first = False
        for x in doc["alerts"]:
            if x.get("symbol"):
                try:
                    src.series(x["symbol"], fresh=True)
                except yahoo.DataError:
                    pass
        rang = evaluate(doc, src, clock, cal)
        write_json(trader_path("alerts.json"), doc)
        ring(rang, a)
        any_rang = any_rang or bool(rang)
        if all(x.get("fired_at") for x in doc["alerts"]):
            print("Every alert has rung. %s" % NOTE)
            return 0
        if clock.now().time() >= until:
            print("Stopped watching at %s IST. %s" % (M.fmt_time(clock.now()), NOTE))
            return 0 if any_rang else 1
        clock.sleep(a.every)


def build_parser():
    ap = argparse.ArgumentParser(prog="alerts.py", description="NSE price and time alerts (paper).")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    common.add_argument("--offline", help="saved Yahoo 5-minute charts instead of the network")
    common.add_argument("--config")
    common.add_argument("--holidays")
    common.add_argument("--bell", action="store_true", help="also sound the terminal bell")
    q = sub.add_parser("quote", parents=[common])
    q.add_argument("symbol")
    q.add_argument("--write", action="store_true")
    ad = sub.add_parser("add", parents=[common])
    ad.add_argument("symbol")
    ad.add_argument("direction", choices=("above", "below"))
    ad.add_argument("price", type=float)
    ad.add_argument("--note")
    at = sub.add_parser("add-time", parents=[common])
    at.add_argument("time")
    at.add_argument("--note")
    for name in ("from-plan", "bells", "list", "clear", "check"):
        sub.add_parser(name, parents=[common])
    w = sub.add_parser("watch", parents=[common])
    w.add_argument("--until", help="HH:MM IST (default flat_by)")
    w.add_argument("--every", type=int, default=60, help="seconds between checks (at least 30)")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        cfg, _ = config.load(a.config)
        cal = M.load_calendar(a.holidays)
        if a.offline and not a.now:
            raise AlertError("--offline needs --now \"YYYY-MM-DD HH:MM\"")
        if a.cmd == "watch" and a.every < 30:
            raise AlertError("--every must be at least 30 seconds (be polite to a free endpoint)")
        clock = Clock(M.parse_ist(a.now) if a.now else None)
        if a.cmd in ("check", "watch"):
            st = cal.status(clock.now().date())
            if not st.tradable:
                raise AlertError(st.reason + " Nothing to watch.")
        src = Source(a.offline)
        fn = {"quote": cmd_quote, "add": cmd_add, "add-time": cmd_add, "from-plan": cmd_from_plan,
              "bells": cmd_bells, "list": cmd_list, "clear": cmd_list, "check": cmd_check, "watch": cmd_watch}[a.cmd]
        return fn(a, cfg, cal, src, clock)
    except (AlertError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
