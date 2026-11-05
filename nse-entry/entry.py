"""nse-entry: watch an NSE setup bar by bar and say the moment it triggers. Long only.
Standard library only, Python 3.8+. It never places an order.

  entry.py check SYMBOL [--setup orb|level] [--level PRICE] [--min-volume-x K]
      one look: forming, waiting (and how far), triggered (time, price), expired,
      unusable, circuit, or closed (holiday/weekend/special session). On a trigger it
      writes trader/entry.json for nse-exit.
  entry.py watch SYMBOL [--until HH:MM]
      checks after every 5-minute bar until it triggers, expires or --until.
  entry.py watchlist [--file trader/orb_hits.json]
      one line per symbol from the scanner's list; writes nothing.

Common: --now "YYYY-MM-DD HH:MM" (IST), --offline PATH, --no-write, --json.
Exit code 0 when triggered, 1 otherwise (the reason is one plain line), 2 usage error.

The trigger is the shared rule in shared/orb.py: the first completed 5-minute bar from the
end of the opening range whose close is above the level. A signal is information, not an
order and not a promise.
"""

import argparse
import datetime as dt
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
NOTE = "A signal is information, not an order. Nothing was placed."
TICK_BARS_5M = 375
TERMINAL = ("triggered", "expired", "unusable", "circuit", "closed")


class EntryError(Exception):
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


# ------------------------------------------------------------------ clock and data

class Clock(object):
    """Real time, or a pinned time that `sleep` moves forward (offline replays and tests)."""

    def __init__(self, pinned=None):
        self.pinned = pinned

    def now(self):
        return self.pinned if self.pinned is not None else M.now_ist()

    def cutoff(self):
        """Bars count as complete up to here: live waits yahoo.SETTLE after a bar's end."""
        return self.pinned if self.pinned is not None else M.now_ist() - yahoo.SETTLE

    def sleep_until(self, when):
        if self.pinned is not None:
            self.pinned = when
        else:
            secs = (when - M.now_ist()).total_seconds()
            if secs > 0:
                yahoo.SLEEP(secs)


class Source(object):
    def __init__(self, offline=None):
        self.data = yahoo.load_offline(offline) if offline else None

    def series(self, sym):
        if self.data is not None:
            if sym not in self.data:
                raise yahoo.DataError("%s is not in the offline data" % sym)
            return self.data[sym]
        return yahoo.fetch_chart(sym, "1d", "5m")


# ------------------------------------------------------------------ the rule

def evaluate(sym, series, now, cutoff, cfg, cal, overrides, setup="orb", level=None,
             min_volume_x=None, wl_entry=None):
    """The status of one setup at `now` (bars complete by `cutoff`). Returns a dict with
    'status' in forming | waiting | triggered | expired | unusable | circuit | closed | not_open."""
    day = now.date()
    st = cal.status(day)
    base = {"symbol": sym, "session": str(day), "as_of": now.strftime("%Y-%m-%d %H:%M IST"), "setup": setup}
    if not st.tradable:
        return dict(base, status="closed", reason=st.reason)
    if now.time() < M.SESSION_OPEN:
        return dict(base, status="not_open", reason="NSE opens at %s IST." % M.fmt_time(M.SESSION_OPEN))
    if now.time() >= M.SESSION_CLOSE:
        return dict(base, status="expired", reason="The session has closed.")
    tick, tick_how = M.tick_for(sym, series.prices(TICK_BARS_5M), overrides)
    bars = orb.day_bars(series, day, cutoff)
    base.update(tick=float(tick), tick_source=tick_how)
    missing = orb.missing_bars(bars, day, cutoff)
    lagging = False
    if missing:
        last_expected = orb.expected_starts(day, cutoff)[-1]
        if missing == [M.fmt_time(last_expected)]:
            lagging = True   # only the newest bar: Yahoo is late, not the chart broken
            bars.pop(last_expected, None)
        else:
            return dict(base, status="unusable",
                        reason="missing or empty 5-minute bar(s) %s; the chart cannot be read" % ",".join(missing[:4]))
    rng = orb.opening_range(bars, day, tick)
    if rng is None:
        so_far = [b for b in bars.values()]
        out = dict(base, status="forming",
                   reason="The opening range (%s-%s IST) is still forming." % (
                       M.fmt_time(M.SESSION_OPEN), M.fmt_time(M.OPENING_RANGE_END)))
        if so_far:
            out["range_so_far"] = {"high": M.clean_price(max(b.h for b in so_far), tick),
                                   "low": M.clean_price(min(b.l for b in so_far), tick)}
        return out
    or_high, or_low = rng
    lvl = or_high if setup == "orb" else M.clean_price(level, tick)
    ordered = [bars[t] for t in sorted(bars)]
    last = ordered[-1]
    prev_close, pc_how = orb.prev_close_for(series, day, wl_entry, cal)
    band_text = (wl_entry or {}).get("band_raw")
    base.update(or_high=or_high, or_low=or_low, level=lvl, last_close=M.clean_price(last.c, tick),
                last_bar_end=M.fmt_time(M.bar_end(last.t)), prev_close=prev_close and M.clean_price(prev_close, tick),
                prev_close_source=pc_how, band=band_text or "unknown")
    if lagging:
        base["data_note"] = "Yahoo has not sent the %s bar yet; judged on the bars before it." % missing[0]
    limits = None
    if prev_close and band_text not in (None, "No Band"):
        lo, up = M.circuit_limits(prev_close, M.parse_band(band_text), tick)
        limits = {"lower": lo, "upper": up}
    base["limits"] = limits
    nea = M.parse_hhmm(cfg["no_entries_after"])
    trig = orb.first_close_above(bars, lvl, tick, min_volume_x)
    if trig is not None and M.bar_end(trig.t).time() >= nea:
        trig = None   # a close above at or after no_entries_after is not an entry
    reason = orb.circuit_touch(ordered if trig is None else [b for b in ordered if b.t <= trig.t],
                               prev_close, band_text, tick)
    if reason:
        return dict(base, status="circuit", reason="%s %s; skip it." % (M.bare_symbol(sym), reason))
    if trig is None:
        if now.time() >= nea:
            return dict(base, status="expired",
                        reason="No close above %s before no_entries_after (%s IST)." % (
                            M.fmt_number(lvl), cfg["no_entries_after"]))
        gap = (lvl / base["last_close"] - 1) * 100
        if base["last_close"] > lvl and min_volume_x:
            why = ("Waiting: last close %s at %s is above the level %s, but no close above it has had "
                   "%g x the day's average volume yet." % (M.fmt_number(base["last_close"]), base["last_bar_end"],
                                                          M.fmt_number(lvl), min_volume_x))
        else:
            why = "Waiting: last close %s at %s is %.2f%% below the level %s." % (
                M.fmt_number(base["last_close"]), base["last_bar_end"], gap, M.fmt_number(lvl))
        return dict(base, status="waiting", distance_pct=round(gap, 2), reason=why)
    entry = M.clean_price(trig.c, tick)
    nxt = [b for b in ordered if b.t > trig.t]
    out = dict(base, status="triggered", trigger_bar=M.fmt_time(trig.t), trigger_time=M.fmt_time(M.bar_end(trig.t)),
               entry_price=entry, over_pct=round((entry / lvl - 1) * 100, 2),
               volume_ratio=(lambda r: r and round(r, 2))(orb.volume_ratio(bars, trig)),
               next_bar_open=nxt and M.clean_price(nxt[0].o, tick) or None,
               move_since_pct=round((base["last_close"] / entry - 1) * 100, 2))
    msg = "Triggered at %s IST: the %s bar closed at %s, above %s (%+.2f%%)." % (
        out["trigger_time"], out["trigger_bar"], M.fmt_inr(entry), M.fmt_number(lvl), out["over_pct"])
    if nxt:
        msg += " Now %s at %s (%+.2f%% since)." % (M.fmt_number(base["last_close"]), base["last_bar_end"],
                                                   out["move_since_pct"])
        if base["last_close"] <= lvl:
            msg += " The last close is back at or below the level: the breakout has failed so far."
    out["reason"] = msg
    return out


# ------------------------------------------------------------------ commands

def setup_args(a):
    if a.setup == "level" and a.level is None:
        raise EntryError("--setup level needs --level PRICE")
    if a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")


def one(a, cfg, cal, overrides, src, clock, sym, wl):
    try:
        series = src.series(sym)
    except yahoo.DataError as e:
        return {"symbol": sym, "status": "unusable", "reason": str(e)}
    return evaluate(sym, series, clock.now(), clock.cutoff(), cfg, cal, overrides, a.setup, a.level,
                    a.min_volume_x, wl.get(sym))


def save_entry(a, res):
    if res["status"] != "triggered" or a.no_write:
        return None
    path = a.out or trader_path("entry.json")
    write_json(path, dict(res, note=NOTE))
    return path


def report(a, res, path=None):
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return
    print("%s %s: %s" % (M.bare_symbol(res["symbol"]), res["status"].upper(), res["reason"]))
    if res.get("or_high") is not None:
        print("  opening range %s to %s (%s-%s IST), tick %s (%s)" % (
            M.fmt_number(res["or_low"]), M.fmt_number(res["or_high"]), M.fmt_time(M.SESSION_OPEN),
            M.fmt_time(M.OPENING_RANGE_END), res["tick"], res["tick_source"]))
    if res.get("limits"):
        print("  circuit limits today %s to %s (%s%% band)" % (
            M.fmt_number(res["limits"]["lower"]), M.fmt_number(res["limits"]["upper"]), res["band"]))
    if res.get("data_note"):
        print("  " + res["data_note"])
    if path:
        print("  Wrote %s. Next: /nse-exit to set the stop and target before anything else." % path)
    if res["status"] == "triggered":
        print(NOTE)


def cmd_check(a, cfg, cal, overrides, src, clock, wl):
    sym = M.normalize_symbol(a.symbol)
    res = one(a, cfg, cal, overrides, src, clock, sym, wl)
    report(a, res, save_entry(a, res))
    return 0 if res["status"] == "triggered" else 1


def next_check(now):
    """The next bar end after `now` (when a new bar completes)."""
    for t in M.bar_starts(now.date()):
        end = M.bar_end(t)
        if end > now:
            return end
    return None


def cmd_watch(a, cfg, cal, overrides, src, clock, wl):
    sym = M.normalize_symbol(a.symbol)
    now = clock.now()
    until = M.parse_hhmm(a.until or cfg["no_entries_after"])
    until_dt = M.ist(now.year, now.month, now.day, until.hour, until.minute)
    last_status = None
    while True:
        res = one(a, cfg, cal, overrides, src, clock, sym, wl)
        if res["status"] != last_status or a.verbose:
            if not a.json:
                print("[%s] " % M.fmt_time(clock.now()), end="")
            report(a, res)
            last_status = res["status"]
        if res["status"] in TERMINAL:
            path = save_entry(a, res)
            if path and not a.json:
                print("  Wrote %s. Next: /nse-exit." % path)
            return 0 if res["status"] == "triggered" else 1
        nxt = next_check(clock.now())
        if nxt is None or nxt > until_dt:
            if not a.json:
                print("Stopped watching at %s IST (--until). Status: %s." % (M.fmt_time(until_dt), res["status"]))
            return 1
        clock.sleep_until(nxt if clock.pinned is not None else nxt + yahoo.SETTLE)


def cmd_watchlist(a, cfg, cal, overrides, src, clock, wl):
    path = a.file or trader_path("orb_hits.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        raise EntryError("no scanner list at %s (run: /nse-scanner orb, or pass --file)" % path)
    syms = [M.normalize_symbol(h["symbol"]) for h in doc.get("hits") or []]
    if not syms:
        raise EntryError("the scanner list %s is empty" % path)
    rows = [one(a, cfg, cal, overrides, src, clock, s, wl) for s in syms[:a.show]]
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print("%d symbols from %s, checked at %s:" % (len(rows), path, clock.now().strftime("%Y-%m-%d %H:%M IST")))
        for r in rows:
            print("  %-12s %-9s %s" % (M.bare_symbol(r["symbol"]), r["status"], r["reason"]))
        print("Pick one and run: entry.py check SYMBOL (it writes trader/entry.json). " + NOTE)
    return 0 if any(r["status"] == "triggered" for r in rows) else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="entry.py", description="NSE entry timer (long only, paper research).")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    common.add_argument("--offline", help="saved Yahoo 5-minute charts instead of the network")
    common.add_argument("--setup", choices=("orb", "level"), default="orb")
    common.add_argument("--level", type=float, help="the price to close above (--setup level)")
    common.add_argument("--min-volume-x", type=float, help="trigger bar volume at least K x the day's average")
    common.add_argument("--watchlist", help="daily watchlist for bands and previous close (default trader/watchlist.json)")
    common.add_argument("--config")
    common.add_argument("--holidays")
    common.add_argument("--ticks")
    common.add_argument("--out", help="write the trigger here instead of trader/entry.json")
    common.add_argument("--no-write", action="store_true")
    common.add_argument("--json", action="store_true")
    c = sub.add_parser("check", parents=[common])
    c.add_argument("symbol")
    w = sub.add_parser("watch", parents=[common])
    w.add_argument("symbol")
    w.add_argument("--until", help="stop watching at HH:MM IST (default no_entries_after)")
    w.add_argument("--verbose", action="store_true", help="print every check, not only changes")
    l = sub.add_parser("watchlist", parents=[common])
    l.add_argument("--file", help="scanner list (default trader/orb_hits.json)")
    l.add_argument("--show", type=int, default=20)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        setup_args(a)
        cfg, _ = config.load(a.config)
        cal = M.load_calendar(a.holidays)
        overrides = M.load_tick_overrides(a.ticks)
        if a.offline and not a.now:
            raise EntryError("--offline needs --now \"YYYY-MM-DD HH:MM\" (the time to check as)")
        clock = Clock(M.parse_ist(a.now) if a.now else None)
        src = Source(a.offline)
        wl = orb.load_watchlist(a.watchlist or trader_path("watchlist.json"))
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](
            a, cfg, cal, overrides, src, clock, wl)
    except (EntryError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}) if a.json else str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
