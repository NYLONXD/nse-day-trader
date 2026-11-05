"""nse-scanner: build the NSE universe, a daily liquidity watchlist, and the intraday
opening-range-breakout list. Standard library only, Python 3.8+.

  scan.py universe [--source nifty500|equity] [--csv PATH] [--bands PATH | --no-bands]
      -> trader/universe.json   (EQ series only, GSM-surveillance stocks out, price bands in)
  scan.py daily [--symbols A,B | --universe FILE] [--limit N] [--setups-only]
      -> trader/watchlist.json  (min price, median traded value, circuit-locked stocks out,
                                 next session's circuit limits, daily setups as tags)
  scan.py orb [--at HH:MM] [--watchlist FILE | --symbols A,B]
      -> trader/orb_hits.json   (first completed 5-minute bar to CLOSE above the
                                 09:15-09:30 range high, before no_entries_after)

Common: --now "YYYY-MM-DD HH:MM" (IST) pins the clock; --offline PATH reads saved Yahoo
answers; --no-write; --json; --show N. Exit code 0 when the scan found something, 1 when it
found nothing or refused (one plain line says why), 2 for a usage error.

A scan is a list of charts that match a rule, not a list of stocks to buy.
All market rules come from shared/market.py; settings from trader/config.json.
"""

import argparse
import json
import os
import statistics
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))

import config     # noqa: E402
import market     # noqa: E402
import nse_files  # noqa: E402
import yahoo      # noqa: E402

M = market
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
DAILY_RANGE = "3mo"          # about 60 sessions of daily bars
VALUE_SESSIONS = 20          # median traded value over this many sessions
MIN_DAILY_BARS = 21
STAGE1_VALUE_FACTOR = 0.25   # spark pass: one quiet day must not drop a liquid stock
MAX_CONFIRM = 200            # orb stage-2 charts per scan, most traded first
TICK_BARS_5M = 375           # about five sessions of 5-minute bars for tick inference


class ScanError(Exception):
    pass


# ------------------------------------------------------------------ small helpers

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
        raise ScanError("no %s at %s" % (what, path))
    except ValueError:
        raise ScanError("%s at %s is not valid JSON" % (what, path))


def say(a, msg):
    if not a.json:
        print(msg)


def progress(a, msg):
    if not a.json and not a.quiet:
        print(msg, file=sys.stderr)


def pct(x):
    return "%+.2f%%" % x


def clock(a):
    return M.parse_ist(a.now) if a.now else M.now_ist()


def parse_symbols(text):
    out = []
    for s in (text or "").split(","):
        if s.strip():
            out.append(M.normalize_symbol(s))
    return out


def tick_info(sym, prices, overrides):
    t, how = M.tick_for(sym, prices, overrides)
    return t, how


# ------------------------------------------------------------------ universe

def build_universe(listing_rows, bands):
    """(entries, dropped). bands: {SYM.NS: {band, remarks}} or None when unknown."""
    entries, seen = [], set()
    dropped = {"series": {}, "bad_symbol": [], "gsm": [], "duplicate": 0}
    for r in listing_rows:
        series = (r.get("series") or "").upper()
        if series and series not in nse_files.TRADABLE_SERIES:
            dropped["series"][series] = dropped["series"].get(series, 0) + 1
            continue
        try:
            sym = M.normalize_symbol(r["symbol"])
        except M.MarketError:
            dropped["bad_symbol"].append(r["symbol"])
            continue
        if sym in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(sym)
        band = remarks = None
        if bands is not None:
            info = bands.get(sym)
            if info:
                band, remarks = info["band"], info["remarks"]
                if "GSM" in (remarks or "").upper():
                    dropped["gsm"].append(M.bare_symbol(sym))
                    continue
        entries.append({"symbol": sym, "name": r.get("name") or "", "band": band})
    return entries, dropped


def cmd_universe(a):
    if a.csv:
        text, source = nse_files.read_text(a.csv), os.path.abspath(a.csv)
    else:
        text, source = nse_files.download(a.source), nse_files.URLS[a.source]
    rows, kind = nse_files.parse_listing(text)
    bands, bands_source = None, None
    if not a.no_bands:
        try:
            btext = nse_files.read_text(a.bands) if a.bands else nse_files.download("bands")
            bands = nse_files.parse_bands(btext)
            bands_source = os.path.abspath(a.bands) if a.bands else nse_files.URLS["bands"]
        except yahoo.DataError as e:
            say(a, "Price bands not loaded: %s" % e)
            say(a, "Without bands the circuit check falls back to a guess from the daily bar.")
    entries, dropped = build_universe(rows, bands)
    if not entries:
        raise ScanError("no EQ symbols in %s" % source)
    doc = {"as_of": M.now_ist().strftime("%Y-%m-%d %H:%M IST"), "source": source, "kind": kind,
           "bands_source": bands_source, "count": len(entries), "dropped": dropped, "symbols": entries}
    path = a.out or trader_path("universe.json")
    if not a.no_write:
        write_json(path, doc)
    if a.json:
        print(json.dumps({k: v for k, v in doc.items() if k != "symbols"}, indent=1))
        return 0
    off = ", ".join("%d %s" % (n, s) for s, n in sorted(dropped["series"].items()))
    no_band = sum(1 for e in entries if e["band"] is None)
    print("Read %d rows (%s) from %s." % (len(rows), kind, source))
    print("Kept %d EQ symbols%s%s%s." % (
        len(entries), "; left out " + off + " (trade-for-trade or other series)" if off else "",
        "; %d under GSM surveillance" % len(dropped["gsm"]) if dropped["gsm"] else "",
        "; %d not NSE symbols" % len(dropped["bad_symbol"]) if dropped["bad_symbol"] else ""))
    if bands is not None:
        print("Price bands from %s (%d without a band row)." % (bands_source, no_band))
    print("%s %s" % ("Would write" if a.no_write else "Wrote", path))
    return 0


def universe_symbols(a):
    """[(symbol, name, band)] from --symbols, or the universe file."""
    if a.symbols:
        return [(s, "", None) for s in parse_symbols(a.symbols)]
    doc = read_json(a.universe or trader_path("universe.json"), "universe (run: scan.py universe)")
    out = [(e["symbol"], e.get("name") or "", e.get("band")) for e in doc.get("symbols") or []]
    if a.limit:
        out = out[:a.limit]
    return out


# ------------------------------------------------------------------ daily

def completed_daily(series, now):
    """Daily bars of finished sessions with every price present (today's bar only after
    DAILY_BAR_FINAL)."""
    out = []
    for b in series.bars:
        d = b.t.date()
        if d > now.date() or (d == now.date() and now.time() < M.DAILY_BAR_FINAL):
            continue
        if None in (b.o, b.h, b.l, b.c) or not b.v:
            continue
        out.append(b)
    return out


def rsi(closes, n=14):
    """Wilder's RSI of the last close, None when too short."""
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def setups(bars):
    """Daily setup tags on finished OHLC bars (oldest first). Informational, never a signal."""
    tags = []
    if len(bars) < 22:
        return tags
    last, prev = bars[-1], bars[-2]
    hi20 = max(b.h for b in bars[-21:-1])
    if last.c > hi20:
        tags.append("breakout_20d")
    elif last.c >= 0.98 * hi20:
        tags.append("near_20d_high")
    closes = [b.c for b in bars]
    ma_now = sum(closes[-20:]) / 20
    ma_prev = sum(closes[-21:-1]) / 20
    if prev.c < ma_prev and last.c > ma_now:
        tags.append("reclaim_ma20")
    if last.h < prev.h and last.l > prev.l:
        tags.append("inside_day")
    r = rsi(closes)
    if r is not None and r < 30:
        tags.append("oversold_rsi14")
    return tags


def circuit_check(sym, band_text, close, prev_close, high, low, tick):
    """(skip reason or None, band note)."""
    if band_text is None:
        side, b = M.guess_circuit(close, prev_close, high, low, tick)
        if side:
            return ("looks locked at the %s circuit (closed at its %s, %s%% from the previous close; "
                    "band unknown)" % (side, "high" if side == "upper" else "low", b)), "unknown"
        return None, "unknown"
    band = M.parse_band(band_text)
    if band is None:
        return None, "No Band"
    side = M.circuit_side(close, prev_close, band, tick)
    if side:
        return "closed at its %s circuit (%s%% band)" % (side, band_text), band_text + "%"
    return None, band_text + "%"


def scan_daily(names, stage1, stage2, now, cfg, overrides):
    """names: [(sym, name, band)]; stage1: {sym: Series} closes (spark); stage2(sym) -> Series
    (daily OHLCV) or raises DataError. Returns the watchlist doc (no I/O)."""
    min_price, min_value = cfg["min_price_inr"], cfg["min_traded_value_inr"]
    failed, low_price, low_value, circuit, hits = {}, [], [], [], []
    survivors = []
    for sym, name, band in names:
        s = stage1.get(sym)
        if s is None:
            failed[sym] = "no data from Yahoo"
            continue
        closes = [b.c for b in completed_daily_closes(s, now)]
        if not closes:
            failed[sym] = "no finished daily bar"
            continue
        if closes[-1] < min_price:
            low_price.append(sym)
            continue
        vol = s.meta.get("regularMarketVolume")
        if vol and closes[-1] * vol < STAGE1_VALUE_FACTOR * min_value and s.bars[-1].t.date() == now.date():
            low_value.append(sym)   # even a busy day would not reach the floor
            continue
        survivors.append((sym, name, band))
    last_sessions = set()
    for sym, name, band in survivors:
        try:
            ser = stage2(sym)
        except yahoo.DataError as e:
            failed[sym] = str(e)
            continue
        bars = completed_daily(ser, now)
        if len(bars) < MIN_DAILY_BARS:
            failed[sym] = "only %d finished daily bars" % len(bars)
            continue
        tick, tick_how = tick_info(sym, ser.prices(60), overrides)
        last, prev = bars[-1], bars[-2]
        last_sessions.add(str(last.t.date()))
        close = M.clean_price(last.c, tick)
        values = [b.c * b.v for b in bars[-VALUE_SESSIONS:]]
        value = statistics.median(values)
        if close < min_price:
            low_price.append(sym)
            continue
        if value < min_value:
            low_value.append(sym)
            continue
        reason, band_note = circuit_check(sym, band, last.c, prev.c, last.h, last.l, tick)
        if reason:
            circuit.append({"symbol": sym, "reason": "%s on %s" % (reason, last.t.date())})
            continue
        hit = {"symbol": sym, "name": name or ser.meta.get("shortName") or "", "last_session": str(last.t.date()),
               "close": close, "median_traded_value_inr": round(value), "band": band_note,
               "tick": float(tick), "tick_source": tick_how, "setups": setups(bars)}
        b = M.parse_band(band) if band is not None else None
        if b is not None:
            lo, up = M.circuit_limits(close, b, tick)
            hit["next_session_limits"] = {"lower": lo, "upper": up}
        hits.append(hit)
    hits.sort(key=lambda h: -h["median_traded_value_inr"])
    return {"failed": failed, "low_price": low_price, "low_value": low_value, "circuit": circuit,
            "hits": hits, "last_sessions": sorted(last_sessions), "stage1_survivors": len(survivors)}


def completed_daily_closes(series, now):
    """Spark-style series (closes only) -> finished daily bars with a close."""
    out = []
    for b in series.bars:
        d = b.t.date()
        if d > now.date() or (d == now.date() and now.time() < M.DAILY_BAR_FINAL):
            continue
        if b.c is not None:
            out.append(b)
    return out


def cmd_daily(a, cfg, overrides):
    now = clock(a)
    names = universe_symbols(a)
    if not names:
        raise ScanError("no symbols to scan")
    syms = [n[0] for n in names]
    if a.offline:
        data = yahoo.load_offline(a.offline)
        stage1 = {s: data[s] for s in syms if s in data}

        def stage2(sym):
            return data[sym]
    else:
        progress(a, "Daily closes for %d symbols (%d requests)..." % (len(syms), -(-len(syms) // yahoo.SPARK_MAX)))
        stage1, _ = yahoo.fetch_spark(syms, DAILY_RANGE, "1d")
        count = [0]

        def stage2(sym):
            count[0] += 1
            if count[0] % 25 == 0:
                progress(a, "  daily charts: %d" % count[0])
            yahoo.SLEEP(yahoo.PAUSE)
            return yahoo.fetch_chart(sym, DAILY_RANGE, "1d")
    res = scan_daily(names, stage1, stage2, now, cfg, overrides)
    hits = res["hits"]
    if a.setups_only:
        hits = [h for h in hits if h["setups"]]
    doc = {"mode": "daily", "as_of": now.strftime("%Y-%m-%d %H:%M IST"),
           "last_session": res["last_sessions"][-1] if res["last_sessions"] else None,
           "universe": len(names), "fetched": len(names) - len(res["failed"]),
           "filters": {"min_price_inr": cfg["min_price_inr"], "min_traded_value_inr": cfg["min_traded_value_inr"],
                       "traded_value": "median of the last %d sessions" % VALUE_SESSIONS},
           "skipped": {"low_price": len(res["low_price"]), "low_traded_value": len(res["low_value"]),
                       "circuit": res["circuit"]},
           "failed": res["failed"], "setups_only": bool(a.setups_only), "count": len(hits), "hits": hits,
           "note": NOTE}
    if len(res["last_sessions"]) > 1:
        doc["warning"] = "charts end on different sessions: %s" % ", ".join(res["last_sessions"])
    path = a.out or trader_path("watchlist.json")
    if not a.no_write:
        write_json(path, doc)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0 if hits else 1
    n_fail = len(res["failed"])
    print("Fetched %d of %d daily charts%s. Last finished session: %s." % (
        len(names) - n_fail, len(names), " (%d failed or had no data)" % n_fail if n_fail else "",
        doc["last_session"] or "none"))
    print("%d below %s; %d below %s median daily traded value; %d skipped at a circuit limit." % (
        len(res["low_price"]), M.fmt_inr(cfg["min_price_inr"], 0), len(res["low_value"]),
        M.fmt_inr_compact(cfg["min_traded_value_inr"]), len(res["circuit"])))
    for c in res["circuit"][:a.show]:
        print("  skip %-12s %s" % (M.bare_symbol(c["symbol"]), c["reason"]))
    tagged = sum(1 for h in hits if h["setups"])
    print("Watchlist: %d stocks%s, most traded first (%d with a daily setup tag)." % (
        len(hits), " with a setup" if a.setups_only else "", tagged))
    for h in hits[:a.show]:
        print("  %-12s %14s  traded %-16s band %-8s %s" % (
            M.bare_symbol(h["symbol"]), M.fmt_inr(h["close"]), M.fmt_inr_compact(h["median_traded_value_inr"]),
            h["band"], ",".join(h["setups"]) or "-"))
    if "warning" in doc:
        print("Warning: " + doc["warning"])
    print("%s %s" % ("Would write" if a.no_write else "Wrote", path))
    print(NOTE)
    return 0 if hits else 1


# ------------------------------------------------------------------ orb

def day_bars(series, day, cutoff):
    """{bar start: Bar} for the session's grid bars that are complete by `cutoff`."""
    out = {}
    for b in series.day(day):
        if M.is_bar_start(b.t) and M.bar_complete(b.t, cutoff):
            out[b.t] = b
    return out


def expected_starts(day, cutoff):
    return [t for t in M.bar_starts(day) if M.bar_complete(t, cutoff)]


def orb_candidate(series, day, cutoff):
    """Stage 1 on closes only: some completed bar after the range closed at or above the
    highest range close. Necessary for a breakout (range high >= every range close)."""
    bars = day_bars(series, day, cutoff)
    rng = [bars.get(t) for t in M.opening_range_bar_starts(day)]
    if any(b is None or b.c is None for b in rng):
        return False
    top = max(b.c for b in rng)
    return any(b.c is not None and b.c >= top for t, b in bars.items() if t.time() >= M.OPENING_RANGE_END)


def prev_close_for(sym, series, day, wl_entry, cal):
    """(previous close, where it came from)."""
    if wl_entry and wl_entry.get("last_session") and cal is not None:
        prev_day = cal.previous_trading_day(day)
        if prev_day is not None and wl_entry["last_session"] == str(prev_day):
            return wl_entry["close"], "daily watchlist"
    pc = series.meta.get("chartPreviousClose")
    rng = str(series.meta.get("range") or "")
    if pc and rng == "1d":
        return pc, "Yahoo previous close"
    earlier = [b for b in series.bars if b.t.date() < day and b.c is not None
               and b.t.time() < M.LAST_RELIABLE_BAR_END]
    if earlier:
        return earlier[-1].c, "approx: last reliable 5-minute close of %s" % earlier[-1].t.date()
    return None, "unknown"


def orb_confirm(sym, series, day, cutoff, cfg, overrides, wl_entry, cal):
    """Stage 2 on real highs and lows: (hit dict, None) or (None, reason it is not a hit)."""
    bars = day_bars(series, day, cutoff)
    need = expected_starts(day, cutoff)
    missing = [M.fmt_time(t) for t in need if t not in bars or None in (bars[t].o, bars[t].h, bars[t].l, bars[t].c)]
    if missing:
        return None, "unusable: missing or empty bar(s) %s" % ",".join(missing[:4])
    tick, tick_how = tick_info(sym, series.prices(TICK_BARS_5M), overrides)
    rng = [bars[t] for t in M.opening_range_bar_starts(day)]
    or_high = M.clean_price(max(b.h for b in rng), tick)
    or_low = M.clean_price(min(b.l for b in rng), tick)
    ordered = [bars[t] for t in need]
    last = ordered[-1]
    last_close = M.clean_price(last.c, tick)
    if last_close < cfg["min_price_inr"]:
        return None, "below the minimum price"
    band_text = (wl_entry or {}).get("band_raw")
    prev_close, pc_how = prev_close_for(sym, series, day, wl_entry, cal)
    day_high, day_low = max(b.h for b in ordered), min(b.l for b in ordered)
    if prev_close:
        reason = None
        if band_text not in (None, "unknown"):
            band = M.parse_band(band_text)
            if band is not None:
                for price, label in ((day_high, "high"), (day_low, "low")):
                    side = M.circuit_side(price, prev_close, band, tick)
                    if side:
                        reason = "touched its %s circuit (%s%% band) today" % (side, band_text)
                        break
        elif band_text is None or band_text == "unknown":
            side, b = M.guess_circuit(last.c, prev_close, day_high, day_low, tick)
            if side:
                reason = "looks locked at the %s circuit (band unknown)" % side
        if reason:
            return None, reason
    hit_bar = None
    for b in ordered:
        if b.t.time() >= M.OPENING_RANGE_END and M.clean_price(b.c, tick) > or_high:
            hit_bar = b
            break
    if hit_bar is None:
        return None, "no close above the range high"
    hit_close = M.clean_price(hit_bar.c, tick)
    return {"symbol": sym, "name": (wl_entry or {}).get("name") or series.meta.get("shortName") or "",
            "hit_bar": M.fmt_time(hit_bar.t), "hit_time": M.fmt_time(M.bar_end(hit_bar.t)),
            "hit_close": hit_close, "or_high": or_high, "or_low": or_low,
            "or_width_pct": round((or_high / or_low - 1) * 100, 2),
            "over_pct": round((hit_close / or_high - 1) * 100, 2),
            "last_close": last_close, "last_bar_end": M.fmt_time(M.bar_end(last.t)),
            "prev_close": prev_close and M.clean_price(prev_close, tick), "prev_close_source": pc_how,
            "tick": float(tick), "tick_source": tick_how}, None


def orb_window(a, cfg, cal):
    """(day, cutoff) or raises ScanError with the plain reason nothing can be scanned."""
    now = clock(a)
    day = now.date()
    st = cal.status(day)
    if not st.tradable:
        raise ScanError(st.reason + " No opening-range scan.")
    cutoff = now
    if a.at:
        at = M.parse_hhmm(a.at)
        cutoff = M.ist(day.year, day.month, day.day, at.hour, at.minute)
        if cutoff > now:
            raise ScanError("--at %s is later than now (%s)" % (a.at, M.fmt_time(now)))
    first_end = M.bar_end(M.ist(day.year, day.month, day.day, M.OPENING_RANGE_END.hour, M.OPENING_RANGE_END.minute))
    if cutoff < first_end:
        raise ScanError("Too early: the opening range ends at %s and the first bar after it completes at %s IST."
                        % (M.fmt_time(M.OPENING_RANGE_END), M.fmt_time(first_end)))
    nea = M.parse_hhmm(cfg["no_entries_after"])
    if cutoff.time() > nea:
        raise ScanError("It is past no_entries_after (%s IST): a breakout now would be an entry your rules "
                        "forbid. Use --at HH:MM to look back at an earlier time." % cfg["no_entries_after"])
    return day, cutoff


def watchlist_entries(a):
    """{sym: entry} from --symbols or the daily watchlist."""
    if a.symbols:
        return {s: {} for s in parse_symbols(a.symbols)}, "--symbols"
    path = a.watchlist or trader_path("watchlist.json")
    doc = read_json(path, "watchlist (run: scan.py daily, or pass --symbols)")
    out = {}
    for h in doc.get("hits") or []:
        e = dict(h)
        band = h.get("band")
        e["band_raw"] = (None if band in (None, "unknown") else "No Band" if band == "No Band"
                         else str(band).rstrip("%"))
        out[M.normalize_symbol(h["symbol"])] = e
    return out, path


def cmd_orb(a, cfg, overrides, cal):
    day, cutoff = orb_window(a, cfg, cal)
    entries, wl_source = watchlist_entries(a)
    syms = list(entries)
    if not syms:
        raise ScanError("the watchlist is empty")
    if a.offline:
        data = yahoo.load_offline(a.offline)
        stage1 = {s: data[s] for s in syms if s in data}

        def stage2(sym):
            return data[sym]
    else:
        progress(a, "5-minute closes for %d symbols..." % len(syms))
        stage1, _ = yahoo.fetch_spark(syms, "1d", "5m")

        def stage2(sym):
            yahoo.SLEEP(yahoo.PAUSE)
            return yahoo.fetch_chart(sym, "1d", "5m")
    failed = {s: "no data from Yahoo" for s in syms if s not in stage1}
    have = [s for s in syms if s in stage1]
    usable_1 = [s for s in have if stage1[s].day(day)]
    for s in have:
        if s not in usable_1:
            failed[s] = "no bars for %s" % day
    cands = [s for s in usable_1 if orb_candidate(stage1[s], day, cutoff)]

    def traded(s):
        bars = day_bars(stage1[s], day, cutoff)
        v = stage1[s].meta.get("regularMarketVolume") or 0
        last = [b.c for b in bars.values() if b.c is not None]
        return (last[-1] if last else 0) * v
    cands.sort(key=traded, reverse=True)
    limit = a.max_confirm if a.max_confirm else len(cands)
    to_check, skipped_n = cands[:limit], max(0, len(cands) - limit)
    hits, notes, usable = [], {}, len(usable_1)
    for s in to_check:
        try:
            ser = stage2(s)
        except yahoo.DataError as e:
            failed[s] = str(e)
            continue
        hit, why = orb_confirm(s, ser, day, cutoff, cfg, overrides, entries.get(s), cal)
        if hit:
            hits.append(hit)
        else:
            notes[s] = why
            if why.startswith("unusable"):
                usable -= 1
    hits.sort(key=lambda h: (h["hit_time"], -h["over_pct"]))
    circuit = {s: r for s, r in notes.items() if "circuit" in r}
    doc = {"mode": "orb", "session": str(day), "cutoff": cutoff.strftime("%Y-%m-%d %H:%M IST"),
           "as_of": clock(a).strftime("%Y-%m-%d %H:%M IST"), "source": wl_source,
           "rules": {"opening_range": "%s-%s IST" % (M.fmt_time(M.SESSION_OPEN), M.fmt_time(M.OPENING_RANGE_END)),
                     "hit": "first completed 5-minute bar to close above the range high",
                     "no_entries_after": cfg["no_entries_after"], "flat_by": cfg["flat_by"]},
           "scanned": len(syms), "fetched": len(have), "usable": usable, "candidates": len(cands),
           "confirmed": len(to_check), "partial": skipped_n, "failed": failed,
           "skipped_circuit": circuit, "count": len(hits), "hits": hits, "note": NOTE}
    path = a.out or trader_path("orb_hits.json")
    if not a.no_write:
        write_json(path, doc)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0 if hits else 1
    print("Fetched %d of %d charts%s; %d usable; %d candidates, %d confirmed with real highs and lows." % (
        len(have), len(syms), " (%d failed or had no data)" % len(failed) if failed else "",
        usable, len(cands), len(to_check)))
    if skipped_n:
        print("PARTIAL: %d candidates not checked (--max-confirm %d). --max-confirm 0 checks them all."
              % (skipped_n, a.max_confirm))
    for s, r in sorted(circuit.items()):
        print("  skip %-12s %s" % (M.bare_symbol(s), r))
    print("%d of %d usable charts closed above their opening-range high by %s IST on %s:" % (
        len(hits), usable, M.fmt_time(cutoff), day))
    for h in hits[:a.show]:
        print("  %s  %-12s %14s  range %s to %s  %s over  (now %s at %s)" % (
            h["hit_time"], M.bare_symbol(h["symbol"]), M.fmt_inr(h["hit_close"]), M.fmt_number(h["or_low"]),
            M.fmt_number(h["or_high"]), pct(h["over_pct"]), M.fmt_number(h["last_close"]), h["last_bar_end"]))
    if len(hits) > a.show:
        print("  ... %d more in %s" % (len(hits) - a.show, path))
    print("%s %s" % ("Would write" if a.no_write else "Wrote", path))
    print(NOTE + " A later hit is closer to the price now; re-run before acting.")
    return 0 if hits else 1


# ------------------------------------------------------------------ main

def build_parser():
    ap = argparse.ArgumentParser(prog="scan.py", description="NSE chart scanner (paper research only).")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    common.add_argument("--offline", help="saved Yahoo answers (file or folder) instead of the network")
    common.add_argument("--config", help="settings file (default trader/config.json)")
    common.add_argument("--holidays", help="NSE holidays file (default data/nse_holidays.json)")
    common.add_argument("--ticks", help="tick-size file (default data/nse_tick_sizes.json)")
    common.add_argument("--out", help="write here instead of the default trader/ file")
    common.add_argument("--no-write", action="store_true")
    common.add_argument("--json", action="store_true")
    common.add_argument("--quiet", action="store_true")
    common.add_argument("--show", type=int, default=15)
    u = sub.add_parser("universe", parents=[common])
    u.add_argument("--source", choices=("nifty500", "equity"), default="nifty500")
    u.add_argument("--csv", help="a local EQUITY_L.csv, index list, or one-symbol-a-line file")
    u.add_argument("--bands", help="a local sec_list.csv")
    u.add_argument("--no-bands", action="store_true")
    d = sub.add_parser("daily", parents=[common])
    d.add_argument("--symbols")
    d.add_argument("--universe")
    d.add_argument("--limit", type=int)
    d.add_argument("--setups-only", action="store_true")
    o = sub.add_parser("orb", parents=[common])
    o.add_argument("--at", help="cut the clock at HH:MM IST today")
    o.add_argument("--symbols")
    o.add_argument("--watchlist")
    o.add_argument("--max-confirm", type=int, default=MAX_CONFIRM)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        if a.cmd == "universe":
            return cmd_universe(a)
        cfg, warnings = config.load(a.config)
        for w in warnings:
            progress(a, "Warning: " + w)
        overrides = M.load_tick_overrides(a.ticks)
        if a.offline and not a.now:
            raise ScanError("--offline needs --now \"YYYY-MM-DD HH:MM\" (the time to scan as)")
        if a.cmd == "daily":
            return cmd_daily(a, cfg, overrides)
        return cmd_orb(a, cfg, overrides, M.load_calendar(a.holidays))
    except (ScanError, M.MarketError, yahoo.DataError, config.ConfigError) as e:
        if a.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
