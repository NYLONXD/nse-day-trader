"""Phase 0 feasibility probe: can Yahoo's free chart endpoint feed an NSE intraday toolkit?

Standard library only. Fetches 5-minute bars for a few .NS symbols, saves the raw
Yahoo JSON as test fixtures, and prints a plain report:
  - do the bars start at 09:15 IST and end at 15:25 IST (the last 5-minute bar)?
  - how old is the latest bar compared with now?
  - how many trading days of 5-minute bars does Yahoo give?
  - are any 5-minute slots missing, or bars null?
  - is volume present?

Usage:
  python tools/probe_yahoo.py                      # fetch live, save fixtures, report
  python tools/probe_yahoo.py --offline DIR        # re-analyse saved fixtures, no network
  python tools/probe_yahoo.py --no-save            # live check during market hours (measures delay)

This is a one-off research tool. The real market rules will live in shared/market.py
(Phase 1); IST is defined here on its own only because Phase 1 does not exist yet.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")  # fixed, no daylight saving
UA = "Mozilla/5.0 (compatible; nse-day-trader/0.1 feasibility probe)"
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
SYMBOLS = ("RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS")
SESSION_OPEN = dt.time(9, 15)
LAST_BAR = dt.time(15, 25)  # the 15:25 bar covers 15:25-15:30, the close
BARS_PER_DAY = 75           # 09:15 .. 15:25 in 5-minute steps


def get(path):
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(host + path, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last = (e.code, body)
            if e.code == 429 or e.code >= 500:
                continue
            return last
        except (urllib.error.URLError, OSError) as e:
            last = (0, str(e))
    return last


def to_ist(ts):
    return dt.datetime.fromtimestamp(ts, IST)


def analyse(sym, doc, now):
    res = doc["chart"]["result"][0]
    meta = res["meta"]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    days = {}
    for i, t in enumerate(ts):
        d = to_ist(t)
        days.setdefault(d.date(), []).append((d, {k: q[k][i] for k in ("open", "high", "low", "close", "volume")}))

    out = {"symbol": sym, "exchange": meta.get("exchangeName"), "tz": meta.get("exchangeTimezoneName"),
           "gmtoffset": meta.get("gmtoffset"), "currency": meta.get("currency"),
           "days": len(days), "first_day": str(min(days)) if days else None,
           "last_day": str(max(days)) if days else None, "bars": len(ts), "problems": []}

    start_ok = end_ok = 0
    null_bars = zero_vol = 0
    for day, rows in sorted(days.items()):
        times = [r[0].time() for r in rows]
        if times[0] == SESSION_OPEN:
            start_ok += 1
        else:
            out["problems"].append("%s first bar %s" % (day, times[0]))
        on_grid = [t for t in times if t.minute % 5 == 0 and t.second == 0]
        off_grid = [t for t in times if t not in on_grid]
        if off_grid:
            out["problems"].append("%s off-grid bar(s) %s (live tick)" % (day, ",".join(map(str, off_grid))))
        if on_grid and on_grid[-1] == LAST_BAR:
            end_ok += 1
        expected, cur = [], dt.datetime.combine(day, SESSION_OPEN)
        while cur.time() <= LAST_BAR:
            expected.append(cur.time())
            cur += dt.timedelta(minutes=5)
        is_today_partial = day == now.date() and now.time() < dt.time(15, 30)
        missing = [t for t in expected if t not in times]
        if is_today_partial:
            missing = [t for t in missing if t < now.time()]
        if missing:
            out["problems"].append("%s missing %d slot(s): %s" % (
                day, len(missing), ",".join(t.strftime("%H:%M") for t in missing[:8])))
        for _, b in rows:
            if b["close"] is None:
                null_bars += 1
            elif not b["volume"]:
                zero_vol += 1
    out["days_starting_0915"] = start_ok
    out["days_ending_1525"] = end_ok
    out["null_bars"] = null_bars
    out["zero_volume_bars"] = zero_vol
    if ts:
        out["latest_bar_ist"] = to_ist(ts[-1]).strftime("%Y-%m-%d %H:%M")
        out["latest_bar_age_min"] = round((now - to_ist(ts[-1])).total_seconds() / 60, 1)
    rmt = meta.get("regularMarketTime")
    if rmt:
        out["regular_market_time_ist"] = to_ist(rmt).strftime("%Y-%m-%d %H:%M:%S")
    out["bars_per_day"] = sorted({len(r) for r in days.values()})
    return out


def depth_probe(sym, now):
    """How far back do 5-minute bars go? Ask for windows ending further in the past."""
    results = []
    for back in (55, 59, 61, 70, 90):
        p2 = int(now.timestamp()) - back * 86400
        p1 = p2 - 3 * 86400
        status, body = get("/v8/finance/chart/%s?period1=%d&period2=%d&interval=5m" % (sym, p1, p2))
        try:
            doc = json.loads(body)
            err = doc["chart"].get("error")
            n = len((doc["chart"]["result"] or [{}])[0].get("timestamp") or []) if not err else 0
            msg = (err or {}).get("description", "") if err else "%d bars" % n
        except Exception:
            msg = "HTTP %s" % status
        results.append((back, msg))
        time.sleep(0.4)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="folder of saved <SYMBOL>-5m-60d.json files")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "phase0"))
    ap.add_argument("--no-save", action="store_true", help="fetch live but do not overwrite fixtures")
    ap.add_argument("--now", help="pin the clock, 'YYYY-MM-DD HH:MM' IST")
    a = ap.parse_args(argv)
    now = (dt.datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if a.now
           else dt.datetime.now(IST))
    print("Now: %s IST" % now.strftime("%Y-%m-%d %H:%M:%S"))
    os.makedirs(a.out, exist_ok=True)
    reports = []
    for sym in SYMBOLS:
        path = os.path.join(a.offline or a.out, "%s-5m-60d.json" % sym)
        if a.offline:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        else:
            status, body = get("/v8/finance/chart/%s?range=60d&interval=5m&includePrePost=false" % sym)
            if status != 200:
                print("%s: Yahoo answered HTTP %s: %s" % (sym, status, body[:200]))
                continue
            doc = json.loads(body)
            doc["_fetched_at_ist"] = now.strftime("%Y-%m-%d %H:%M:%S")
            if not a.no_save:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(doc, f, separators=(",", ":"))
            time.sleep(0.4)
        reports.append(analyse(sym, doc, now))
    print(json.dumps(reports, indent=1, default=str))
    if not a.offline:
        print("Depth probe (RELIANCE.NS, 3-day windows ending N days ago):")
        for back, msg in depth_probe("RELIANCE.NS", now):
            print("  %3d days ago: %s" % (back, msg))
    return 0 if reports else 1


if __name__ == "__main__":
    sys.exit(main())
