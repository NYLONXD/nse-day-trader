"""The opening-range-breakout rules, shared by nse-scanner, nse-entry and nse-timemachine so
all three call a breakout the same way. Standard library only.

  - The opening range is the bars that start before market.OPENING_RANGE_END (09:15, 09:20,
    09:25): range high = their highest high, range low = their lowest low.
  - A trigger is the first COMPLETED 5-minute bar starting at or after the range end whose
    close (rounded to the stock's tick) is above the level (the range high, or a level you
    give). Its time is the bar's end.
  - A chart is usable only when every bar from the open to the cutoff is there with prices.
  - Volume of the 09:15 bar is never used: Yahoo almost always reports 0 there (Phase 0).
"""

import json

import market as M


def day_bars(series, day, cutoff):
    """{bar start: Bar} for the session's grid bars complete by `cutoff`."""
    out = {}
    for b in series.day(day):
        if M.is_bar_start(b.t) and M.bar_complete(b.t, cutoff):
            out[b.t] = b
    return out


def expected_starts(day, cutoff):
    return [t for t in M.bar_starts(day) if M.bar_complete(t, cutoff)]


def missing_bars(bars, day, cutoff):
    """Bars that should exist by `cutoff` but are absent or have a null price ('HH:MM')."""
    return [M.fmt_time(t) for t in expected_starts(day, cutoff)
            if t not in bars or None in (bars[t].o, bars[t].h, bars[t].l, bars[t].c)]


def opening_range(bars, day, tick):
    """(high, low) on the tick grid, or None while the range is incomplete."""
    rng = [bars.get(t) for t in M.opening_range_bar_starts(day)]
    if any(b is None or b.h is None or b.l is None for b in rng):
        return None
    return M.clean_price(max(b.h for b in rng), tick), M.clean_price(min(b.l for b in rng), tick)


def after_range(bars):
    """Completed bars from the range end on, oldest first."""
    return [bars[t] for t in sorted(bars) if t.time() >= M.OPENING_RANGE_END]


def first_close_above(bars, level, tick, min_volume_x=None):
    """The first completed bar after the range whose close is above `level` (and, with
    min_volume_x, whose volume is at least that many times the day's average so far), or None."""
    for b in after_range(bars):
        if b.c is None or M.clean_price(b.c, tick) <= level:
            continue
        if min_volume_x:
            r = volume_ratio(bars, b)
            if r is None or r < min_volume_x:
                continue
        return b
    return None


def closes_only_candidate(series, day, cutoff):
    """Cheap first pass on closes only (spark): a bar after the range closed at or above the
    highest range close. Necessary for a breakout, because range high >= every range close."""
    bars = day_bars(series, day, cutoff)
    rng = [bars.get(t) for t in M.opening_range_bar_starts(day)]
    if any(b is None or b.c is None for b in rng):
        return False
    top = max(b.c for b in rng)
    return any(b.c is not None and b.c >= top for b in after_range(bars))


def volume_ratio(bars, trigger):
    """Trigger-bar volume / average volume of the earlier bars of the day, leaving out the
    09:15 bar (Yahoo's 0). None when there is nothing to compare with."""
    first = M.opening_range_bar_starts(trigger.t.date())[0]
    earlier = [b.v for t, b in sorted(bars.items()) if first < t < trigger.t and b.v]
    if not earlier or not trigger.v:
        return None
    return trigger.v / (sum(earlier) / len(earlier))


def prev_close_for(series, day, wl_entry, cal):
    """(previous session's close, where it came from) for circuit limits."""
    if wl_entry and wl_entry.get("last_session") and cal is not None:
        prev_day = cal.previous_trading_day(day)
        if prev_day is not None and wl_entry["last_session"] == str(prev_day):
            return wl_entry["close"], "daily watchlist"
    pc = series.meta.get("chartPreviousClose")
    if pc and str(series.meta.get("range") or "") == "1d":
        return pc, "Yahoo previous close"
    earlier = [b for b in series.bars if b.t.date() < day and b.c is not None
               and b.t.time() < M.LAST_RELIABLE_BAR_END]
    if earlier:
        return earlier[-1].c, "approx: last reliable 5-minute close of %s" % earlier[-1].t.date()
    return None, "unknown"


def circuit_touch(bar_list, prev_close, band_text, tick):
    """A plain reason if the day so far touched (or, band unknown, looks locked at) a circuit
    limit; None otherwise. band_text: '5', 'No Band', or None/'unknown'."""
    if not prev_close or not bar_list:
        return None
    day_high = max(b.h for b in bar_list)
    day_low = min(b.l for b in bar_list)
    if band_text not in (None, "unknown"):
        band = M.parse_band(band_text)
        if band is None:
            return None
        for price in (day_high, day_low):
            side = M.circuit_side(price, prev_close, band, tick)
            if side:
                return "touched its %s circuit (%s%% band) today" % (side, band_text)
        return None
    side, _ = M.guess_circuit(bar_list[-1].c, prev_close, day_high, day_low, tick)
    if side:
        return "looks locked at the %s circuit (band unknown)" % side
    return None


def band_raw(note):
    """The watchlist's band note ('5%', 'No Band', 'unknown') -> '5', 'No Band' or None."""
    if note in (None, "unknown"):
        return None
    return "No Band" if note == "No Band" else str(note).rstrip("%")


def load_watchlist(path):
    """{SYM.NS: entry} from the daily watchlist, each with 'band_raw'. Missing file -> {}."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for h in doc.get("hits") or []:
        e = dict(h)
        e["band_raw"] = band_raw(h.get("band"))
        out[M.normalize_symbol(h["symbol"])] = e
    return out
