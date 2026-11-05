"""NSE market rules for nse-day-trader. Standard library only, Python 3.8+.

Every skill imports this module; no skill script may hardcode a market rule.
    sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))
    import market

What lives here, and nowhere else:
  - Time:     IST is a fixed UTC+05:30 offset. India has no daylight saving.
              Never New York time.
  - Session:  09:15-15:30 IST, 5-minute bars, opening range = first 15 minutes
              (the 09:15, 09:20 and 09:25 bars), entries only after 09:30.
  - Calendar: weekends, holidays and special sessions (Muhurat, budget-day
              weekend sessions) from data/nse_holidays.json, which the user
              fills from NSE's official circular. A year not marked complete
              in that file is refused, never guessed.
  - Ticks:    per stock (0.01 to 5.00). A tick file the user fills wins;
              otherwise the coarsest tick that all recent real prices sit on;
              otherwise 0.05. Stops round away from the entry, targets round half-up.
  - Symbols:  Yahoo style SYMBOL.NS. "." is never turned into "-" (that is a
              US-only rule); NSE's own "-" and "&" are kept (BAJAJ-AUTO, M&M).
  - Money:    INR as "Rs." with Indian digit grouping (12,34,567.50).
"""

import datetime as _dt
import json
import os
import re
import urllib.parse
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trader_dir():
    """Your working folder: trader/ in the current directory, or $NSE_TRADER_DIR."""
    return os.environ.get("NSE_TRADER_DIR") or "trader"


def user_file(name, env, packaged):
    """Where a file you fill lives. Order: $env, then trader/<name> (your project: survives
    plugin updates), then the copy shipped in this repo's data/ folder."""
    if os.environ.get(env):
        return os.environ[env]
    mine = os.path.join(trader_dir(), name)
    return mine if os.path.exists(mine) else packaged

# ------------------------------------------------------------------ time

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), "IST")
UTC = _dt.timezone.utc


class MarketError(ValueError):
    """A market rule was broken or a market data file is wrong. The message is one plain line."""


def now_ist():
    return _dt.datetime.now(IST)


def ist(year, month, day, hour=0, minute=0, second=0):
    """An IST wall-clock time as an aware datetime."""
    return _dt.datetime(year, month, day, hour, minute, second, tzinfo=IST)


def to_ist(x):
    """Unix seconds (int/float) or an aware datetime -> aware IST datetime.
    A naive datetime is refused: guessing its zone is how New York time sneaks in."""
    if isinstance(x, (int, float)):
        return _dt.datetime.fromtimestamp(x, IST)
    if isinstance(x, _dt.datetime):
        if x.tzinfo is None or x.utcoffset() is None:
            raise MarketError("a time without a time zone; say it is IST: %s" % x)
        return x.astimezone(IST)
    raise MarketError("not a time: %r" % (x,))


def epoch(x):
    """Aware datetime -> unix seconds (int)."""
    return int(to_ist(x).timestamp())


def parse_hhmm(text):
    """'10:15' or '9:30' -> datetime.time. Seconds are not allowed."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise MarketError("time must be HH:MM (24-hour, IST): %r" % (text,))
    return _dt.time(int(m.group(1)), int(m.group(2)))


def parse_ist(text):
    """'2026-11-05 10:15' (IST wall clock) -> aware IST datetime."""
    try:
        naive = _dt.datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise MarketError("time must be 'YYYY-MM-DD HH:MM' in IST: %r" % (text,))
    return naive.replace(tzinfo=IST)


def parse_date(text):
    try:
        return _dt.datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise MarketError("date must be YYYY-MM-DD: %r" % (text,))


def fmt_time(x):
    """Aware datetime or time -> '10:15'."""
    if isinstance(x, _dt.datetime):
        x = to_ist(x)
    return x.strftime("%H:%M")


# ------------------------------------------------------------------ session

SESSION_OPEN = _dt.time(9, 15)
SESSION_CLOSE = _dt.time(15, 30)
BAR_MINUTES = 5
OPENING_RANGE_MINUTES = 15
OPENING_RANGE_END = _dt.time(9, 30)       # entries only after this
# Phase 0: bars starting at 15:15 or later are often null or zero-volume on Yahoo
# (NSE's closing-price window). Nothing may depend on them, so flat_by <= 15:15.
LAST_RELIABLE_BAR_END = _dt.time(15, 15)
BAR = _dt.timedelta(minutes=BAR_MINUTES)
# NSE's official close is computed after 15:30; a session's daily bar is treated as final
# from 16:00 IST. Before that, today's daily bar is left out.
DAILY_BAR_FINAL = _dt.time(16, 0)


def _at(day, t):
    return _dt.datetime.combine(day, t).replace(tzinfo=IST)


def session_open(day):
    return _at(day, SESSION_OPEN)


def session_close(day):
    return _at(day, SESSION_CLOSE)


def bar_starts(day):
    """Start times of every regular 5-minute bar of a session: 09:15 ... 15:25 (75 bars)."""
    out, cur, end = [], session_open(day), session_close(day)
    while cur < end:
        out.append(cur)
        cur += BAR
    return out


def opening_range_bar_starts(day):
    """The bars that make up the opening range: 09:15, 09:20, 09:25."""
    end = _at(day, OPENING_RANGE_END)
    return [b for b in bar_starts(day) if b < end]


def bar_end(start):
    return to_ist(start) + BAR


def is_bar_start(when):
    """True when `when` is exactly on the session's 5-minute grid."""
    when = to_ist(when)
    return when in set(bar_starts(when.date()))


def bar_complete(start, now):
    """A bar counts only once it has finished: now >= start + 5 minutes."""
    return to_ist(now) >= bar_end(start)


def validate_session_times(no_entries_after, flat_by):
    """Check the user's config times against the session. Returns a list of plain
    problems (empty = fine). Accepts 'HH:MM' strings or datetime.time."""
    probs = []
    try:
        nea = no_entries_after if isinstance(no_entries_after, _dt.time) else parse_hhmm(no_entries_after)
        fb = flat_by if isinstance(flat_by, _dt.time) else parse_hhmm(flat_by)
    except MarketError as e:
        return [str(e)]
    for name, t in (("no_entries_after", nea), ("flat_by", fb)):
        if t.minute % BAR_MINUTES:
            probs.append("%s %s is not on the 5-minute bar grid" % (name, fmt_time(t)))
    if nea <= OPENING_RANGE_END:
        probs.append("no_entries_after %s must be after the opening range ends (%s)"
                     % (fmt_time(nea), fmt_time(OPENING_RANGE_END)))
    if fb <= nea:
        probs.append("flat_by %s must be after no_entries_after %s" % (fmt_time(fb), fmt_time(nea)))
    if fb > LAST_RELIABLE_BAR_END:
        probs.append("flat_by %s is too late: Yahoo bars from %s on are unreliable"
                     % (fmt_time(fb), fmt_time(LAST_RELIABLE_BAR_END)))
    return probs


def session_phase(now, no_entries_after, flat_by):
    """Where the clock is in today's session (ignores the calendar; check day_status first):
    before_open, opening_range, entries_open, manage_only, flat, closed."""
    now = to_ist(now)
    t = now.time()
    nea = no_entries_after if isinstance(no_entries_after, _dt.time) else parse_hhmm(no_entries_after)
    fb = flat_by if isinstance(flat_by, _dt.time) else parse_hhmm(flat_by)
    if t < SESSION_OPEN:
        return "before_open"
    if t < OPENING_RANGE_END:
        return "opening_range"
    if t < nea:
        return "entries_open"
    if t < fb:
        return "manage_only"
    if t < SESSION_CLOSE:
        return "flat"
    return "closed"


# ------------------------------------------------------------------ calendar

HOLIDAYS_FILE = os.path.join(ROOT, "data", "nse_holidays.json")


class DayStatus(object):
    """tradable: bool; kind: trading | weekend | holiday | special | unknown_year; reason: one line."""

    def __init__(self, day, tradable, kind, reason):
        self.day, self.tradable, self.kind, self.reason = day, tradable, kind, reason

    def __repr__(self):
        return "DayStatus(%s, %s, %s)" % (self.day, self.kind, self.reason)


class Calendar(object):
    def __init__(self, holidays, special_sessions, years_covered, sources, path=""):
        self.holidays = holidays                  # {date: name}
        self.special_sessions = special_sessions  # {date: name}
        self.years_covered = years_covered        # set of ints
        self.sources = sources                    # {year: "NSE circular ..."}
        self.path = path

    def status(self, day):
        """Is `day` a normal NSE equity session? Refuses (tradable=False) with the reason."""
        if isinstance(day, _dt.datetime):
            day = to_ist(day).date()
        label = day.strftime("%a %d %b %Y")
        if day in self.special_sessions:
            return DayStatus(day, False, "special",
                             "%s is a special session (%s). Its hours and liquidity are not a normal "
                             "09:15-15:30 day, so this toolkit does not trade it." % (label, self.special_sessions[day]))
        if day.weekday() >= 5:
            return DayStatus(day, False, "weekend", "%s is a %s; NSE is closed."
                             % (label, day.strftime("%A")))
        if day.year not in self.years_covered:
            return DayStatus(day, False, "unknown_year",
                             "No NSE holiday list for %d in %s. Fill it from NSE's official circular "
                             "and add %d to years_covered; until then every %d date is refused."
                             % (day.year, os.path.relpath(self.path) if self.path else "the holidays file",
                                day.year, day.year))
        if day in self.holidays:
            return DayStatus(day, False, "holiday", "%s is an NSE trading holiday (%s)."
                             % (label, self.holidays[day]))
        return DayStatus(day, True, "trading", "%s is a normal NSE trading day." % label)

    def is_trading_day(self, day):
        return self.status(day).tradable

    def previous_trading_day(self, day, max_back=15):
        """The last tradable day before `day` (None if the calendar cannot say)."""
        d = day
        for _ in range(max_back):
            d -= _dt.timedelta(days=1)
            st = self.status(d)
            if st.tradable:
                return d
            if st.kind == "unknown_year":
                return None
        return None


def load_calendar(path=None):
    """Read and check the holidays file. Raises MarketError with one plain line if it is wrong.
    Path order: the argument, $NSE_HOLIDAYS_FILE, trader/nse_holidays.json, data/nse_holidays.json."""
    path = path or user_file("nse_holidays.json", "NSE_HOLIDAYS_FILE", HOLIDAYS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise MarketError("cannot read the NSE holidays file %s: %s" % (path, e))
    try:
        years = set(int(y) for y in doc.get("years_covered") or [])
    except (TypeError, ValueError):
        raise MarketError("%s: years_covered must be a list of years like [2026]" % path)
    sources = {}
    for y, s in (doc.get("source") or {}).items():
        sources[int(y)] = str(s).strip()
    for y in sorted(years):
        if not sources.get(y):
            raise MarketError("%s: %d is in years_covered but source[\"%d\"] is empty; name the NSE "
                              "circular the dates came from" % (path, y, y))

    def read_list(key):
        out = {}
        for i, row in enumerate(doc.get(key) or []):
            if not isinstance(row, dict) or "date" not in row:
                raise MarketError("%s: %s[%d] needs a \"date\" and a \"name\"" % (path, key, i))
            d = parse_date(row["date"])
            name = str(row.get("name") or "").strip()
            if not name:
                raise MarketError("%s: %s[%d] (%s) has no name" % (path, key, i, d))
            out[d] = name
        return out

    holidays = read_list("holidays")
    specials = read_list("special_sessions")
    for d in holidays:
        if d.year not in years:
            raise MarketError("%s: holiday %s is in %d, which is not in years_covered; add the year "
                              "once its whole list is in" % (path, d, d.year))
    return Calendar(holidays, specials, years, sources, path)


# ------------------------------------------------------------------ ticks

TICK_DEFAULT = Decimal("0.05")
# Coarsest first. Real data (Phase 2): low-priced stocks trade in 0.01 steps (PASHUPATI
# 120.47, KOTIC 126.99), most in 0.05, RELIANCE and TCS in 0.10. The default stays 0.05 when
# nothing is known: a 0.05 price is also valid for a 0.01 stock, so it is the safe floor.
TICK_CANDIDATES = (Decimal("5.00"), Decimal("1.00"), Decimal("0.50"), Decimal("0.10"),
                   Decimal("0.05"), Decimal("0.01"))
TICK_MIN_SAMPLES = 100     # prices needed before a tick is inferred
TICKS_FILE = os.path.join(ROOT, "data", "nse_tick_sizes.json")
# Yahoo sends prices as 32-bit floats: 2481.39990234375 for 2481.40, and the error grows
# with the price (up to about 0.008 at Rs. 1.3 lakh, where 131072.35 arrives as
# 131072.34375). So "is this market price on the tick?" allows a float32-sized error, and
# cleaning rounds straight to the nearest tick. Our own arithmetic (a raw stop) only snaps
# off double-precision noise (99.99999999 -> 100), so a stop never tightens.
_SNAP = Decimal("0.000001")
_F32_REL = Decimal("1.2e-7")   # 2 x float32 relative precision (2^-24)


def _f32_tolerance(d):
    return abs(d) * _F32_REL + _SNAP


def _dec(x, snap=_SNAP):
    """Float -> Decimal with float noise snapped off."""
    if isinstance(x, Decimal):
        d = x
    else:
        try:
            d = Decimal(repr(float(x)))
        except (TypeError, ValueError):
            raise MarketError("not a price: %r" % (x,))
    if not d.is_finite():
        raise MarketError("not a price: %r" % (x,))
    return d.quantize(snap, rounding=ROUND_HALF_EVEN)


def _tick(tick):
    t = TICK_DEFAULT if tick is None else _dec(tick)
    if t <= 0:
        raise MarketError("tick must be positive: %r" % (tick,))
    return t


def round_to_tick(price, tick=None, how="nearest"):
    """Round a price onto the tick grid. how: nearest (half-up), down, up. Returns a float
    with at most 2 decimals (1312.3, not 1312.300048828125)."""
    p, t = _dec(price), _tick(tick)
    if p < 0:
        raise MarketError("negative price: %r" % (price,))
    mode = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}.get(how)
    if mode is None:
        raise MarketError("rounding must be nearest, down or up: %r" % (how,))
    n = (p / t).quantize(Decimal(1), rounding=mode)
    return float((n * t).quantize(Decimal("0.01")))


def on_tick(price, tick=None):
    """Is a market price (Yahoo float noise allowed) on the tick grid?"""
    p, t = _dec(price), _tick(tick)
    r = p % t
    return min(r, t - r) <= _f32_tolerance(p)


def clean_price(price, tick=None):
    """A Yahoo price with float noise -> the nearest tick."""
    return round_to_tick(price, tick, "nearest")


def stop_price(raw_stop, entry, tick=None):
    """A stop rounds AWAY from the entry, so rounding never tightens it: a long stop below
    the entry rounds down. A stop equal to the entry is refused."""
    s, e = _dec(raw_stop), _dec(entry)
    if s == e:
        raise MarketError("the stop %s equals the entry; a stop must be away from the entry" % s)
    return round_to_tick(s, tick, "down" if s < e else "up")


def target_price(raw_target, tick=None):
    """A target rounds half-up to the nearest tick."""
    return round_to_tick(raw_target, tick, "nearest")


def infer_tick(prices, min_samples=TICK_MIN_SAMPLES):
    """The coarsest candidate tick that every price sits on, or None when there are too few
    prices. Pass RECENT real prices only (a few days of 5-minute OHLC): a stock's tick changes
    when its price moves into another band, and older prices would mix the two ticks."""
    ds = [_dec(p) for p in prices if p is not None]
    if len(ds) < min_samples or len(set(ds)) < 20:
        return None
    for t in TICK_CANDIDATES:
        if all(on_tick(d, t) for d in ds):
            return t
    return None  # not even on a 0.01 grid: not NSE prices


def load_tick_overrides(path=None):
    """{"RELIANCE.NS": Decimal("0.10"), ...} from the tick file the user fills. A missing file
    is fine (empty). Path order: the argument, $NSE_TICK_SIZES_FILE, trader/nse_tick_sizes.json,
    data/nse_tick_sizes.json."""
    path = path or user_file("nse_tick_sizes.json", "NSE_TICK_SIZES_FILE", TICKS_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise MarketError("cannot read the tick-size file %s: %s" % (path, e))
    out = {}
    for sym, t in (doc.get("ticks") or {}).items():
        tick = _tick(t)
        if tick not in TICK_CANDIDATES:
            raise MarketError("%s: tick %s for %s is not one of %s"
                              % (path, t, sym, ", ".join(str(c) for c in TICK_CANDIDATES[::-1])))
        out[normalize_symbol(sym)] = tick
    return out


def tick_for(symbol, recent_prices=None, overrides=None):
    """(tick as Decimal, where it came from). Order: the tick file, then the stock's own recent
    prices, then 0.05."""
    sym = normalize_symbol(symbol)
    if overrides and sym in overrides:
        return overrides[sym], "tick file"
    if recent_prices:
        t = infer_tick(recent_prices)
        if t is not None:
            n = sum(1 for p in recent_prices if p is not None)
            return t, "inferred from %d recent prices" % n
    return TICK_DEFAULT, "default 0.05 (no tick file entry, too few recent prices)"


# ------------------------------------------------------------------ symbols

SUFFIX = ".NS"
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")


def normalize_symbol(text):
    """'reliance' / 'RELIANCE' / 'RELIANCE.NS' -> 'RELIANCE.NS'. Keeps '-' and '&'
    (BAJAJ-AUTO.NS, M&M.NS) and never turns '.' into '-'. Other exchanges are refused."""
    s = str(text or "").strip().upper()
    if s.endswith(SUFFIX):
        s = s[:-len(SUFFIX)]
    elif "." in s:
        raise MarketError("only NSE symbols (SYMBOL.NS) are supported: %r" % (text,))
    if not _SYMBOL_RE.match(s):
        raise MarketError("not an NSE symbol: %r" % (text,))
    return s + SUFFIX


def bare_symbol(text):
    """'RELIANCE.NS' -> 'RELIANCE' (NSE's own code, as in EQUITY_L.csv and broker apps)."""
    return normalize_symbol(text)[:-len(SUFFIX)]


def url_symbol(text):
    """The symbol as it goes into a Yahoo URL path: 'M&M.NS' -> 'M%26M.NS'."""
    return urllib.parse.quote(normalize_symbol(text), safe=".-")


# ------------------------------------------------------------------ money

CURRENCY = "INR"
CURRENCY_LABEL = "Rs."


def group_indian(digits):
    """'1234567' -> '12,34,567' (last three, then twos)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def fmt_number(x, decimals=2):
    """1234567.5 -> '12,34,567.50'; -500 -> '-500.00'."""
    q = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    d = _dec(x).quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    whole, _, frac = str(abs(d)).partition(".")
    return sign + group_indian(whole) + ("." + frac if decimals else "")


def fmt_inr(x, decimals=2):
    """1234567.5 -> 'Rs. 12,34,567.50'; -500 -> '-Rs. 500.00'."""
    s = fmt_number(x, decimals)
    return ("-" + CURRENCY_LABEL + " " + s[1:]) if s.startswith("-") else (CURRENCY_LABEL + " " + s)


def fmt_inr_compact(x):
    """Big amounts in lakh and crore: 52300000 -> 'Rs. 5.23 cr', 420000 -> 'Rs. 4.20 lakh'."""
    d = _dec(x)
    sign, a = ("-" if d < 0 else ""), abs(d)
    if a >= Decimal(10000000):
        s = "%s %s cr" % (CURRENCY_LABEL, fmt_number(a / Decimal(10000000), 2))
    elif a >= Decimal(100000):
        s = "%s %s lakh" % (CURRENCY_LABEL, fmt_number(a / Decimal(100000), 2))
    else:
        s = fmt_inr(a)
    return sign + s


# ------------------------------------------------------------------ circuit limits

# NSE gives most stocks a fixed daily price band (2, 5, 10, 20 or 40 % of the previous
# close) in sec_list.csv. F&O stocks say "No Band": no fixed circuit (dynamic bands).
# A stock at its circuit cannot be bought (upper: no sellers) or sold (lower: no buyers),
# so the scanner skips it.
BAND_CANDIDATES = (Decimal(2), Decimal(5), Decimal(10), Decimal(20))


def parse_band(text):
    """sec_list.csv 'Band' -> Decimal percent, None for 'No Band'. Raises on anything else."""
    s = str(text or "").strip()
    if s.lower() == "no band":
        return None
    try:
        b = Decimal(s)
    except Exception:
        raise MarketError("not a price band: %r" % (text,))
    if b <= 0 or b > 100:
        raise MarketError("not a price band: %r" % (text,))
    return b


def circuit_limits(prev_close, band_pct, tick=None):
    """(lower, upper) for a session whose previous close is `prev_close`. The limits are
    pulled inside the band onto the tick grid."""
    p, b = _dec(prev_close), _dec(band_pct)
    raw_up, raw_lo = p * (1 + b / 100), p * (1 - b / 100)
    return round_to_tick(raw_lo, tick, "up"), round_to_tick(raw_up, tick, "down")


def circuit_side(price, prev_close, band_pct, tick=None):
    """'upper', 'lower' or None: is `price` at a circuit limit? Exact, apart from Yahoo's
    float noise (the limits matched NSE's to the paisa on real locked stocks). A coarser
    tick than the real one only pulls the limits inward, so it errs towards skipping."""
    lo, up = circuit_limits(prev_close, band_pct, tick)
    p = _dec(price)
    tol = _f32_tolerance(p)
    if p >= _dec(up) - tol:
        return "upper"
    if p <= _dec(lo) + tol:
        return "lower"
    return None


def guess_circuit(close, prev_close, high, low, tick=None):
    """When the band is unknown: (side, band) if the day looks locked at a standard band,
    i.e. it closed at its high (or low) within one tick of that band's limit. Errs on the
    side of skipping the stock. (None, None) when it does not look locked."""
    t = _tick(tick)
    c = _dec(close)
    for b in BAND_CANDIDATES:
        lo, up = circuit_limits(prev_close, b, t)
        if abs(c - _dec(up)) <= t and c >= _dec(high) - t:
            return "upper", b
        if abs(c - _dec(lo)) <= t and c <= _dec(low) + t:
            return "lower", b
    return None, None
