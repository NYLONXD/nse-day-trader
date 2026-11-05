"""Yahoo Finance chart data for nse-day-trader. Standard library only.

Free, unofficial, can lag; 5-minute bars, never ticks. Market rules (IST, the session
grid, symbols) come from market.py; this module only fetches and parses.

  fetch_chart(sym, "1d", "5m")      one v8 chart: open/high/low/close/volume
  fetch_spark(syms, "1d", "5m")     closes only, 20 symbols a request (cheap first pass)
  load_offline(path)                saved answers instead of the network (tests, replays)

The User-Agent says what this is. Never disguise it as a browser.
"""

import gzip
import json
import os
import time
import urllib.error
import urllib.request

import market

UA = "nse-day-trader/0.1 (personal research script; Python urllib)"
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
SPARK_MAX = 20     # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3        # seconds between requests: be polite to a free endpoint
BACKOFF = 5.0      # seconds before the one retry after a 429
SLEEP = time.sleep  # tests swap this out


class DataError(Exception):
    """Data could not be fetched or read. The message is one plain line."""


class Bar(object):
    __slots__ = ("t", "o", "h", "l", "c", "v")

    def __init__(self, t, o, h, l, c, v):
        self.t, self.o, self.h, self.l, self.c, self.v = t, o, h, l, c, v

    def __repr__(self):
        return "Bar(%s o=%s h=%s l=%s c=%s v=%s)" % (market.fmt_time(self.t), self.o, self.h,
                                                    self.l, self.c, self.v)


class Series(object):
    """One symbol's bars (IST times) plus Yahoo's meta. o/h/l/v are None in spark answers."""

    def __init__(self, symbol, bars, meta):
        self.symbol, self.bars, self.meta = symbol, bars, meta

    def day(self, d):
        return [b for b in self.bars if b.t.date() == d]

    def days(self):
        return sorted({b.t.date() for b in self.bars})

    def prices(self, n_bars=None):
        """Every non-null open/high/low/close of the last n bars (for tick inference)."""
        bars = self.bars[-n_bars:] if n_bars else self.bars
        out = []
        for b in bars:
            out.extend(p for p in (b.o, b.h, b.l, b.c) if p is not None)
        return out


# ------------------------------------------------------------------ network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo path: query2 first, query1 on a 429 or 5xx, one more round after BACKOFF
    seconds if a 429 was seen. A 4xx with a JSON body (Yahoo's 'no data') is returned."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                body = ""
                try:
                    if 400 <= e.code < 500 and e.code != 429:
                        body = e.read().decode("utf-8", "replace")
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body
                raise DataError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise DataError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=false" % (
        market.url_symbol(sym), rng, interval)


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(market.url_symbol(s) for s in symbols), rng, interval)


# ------------------------------------------------------------------ parsing

def _series(res, sym=None):
    meta = res.get("meta") or {}
    sym = market.normalize_symbol(sym or meta.get("symbol"))
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def col(k):
        v = q.get(k)
        return (list(v) + [None] * (len(ts) - len(v))) if isinstance(v, list) else [None] * len(ts)
    o, h, l, c, v = col("open"), col("high"), col("low"), col("close"), col("volume")
    bars = [Bar(market.to_ist(t), o[i], h[i], l[i], c[i], v[i]) for i, t in enumerate(ts)]
    return Series(sym, bars, meta)


def _doc(text_or_doc):
    if isinstance(text_or_doc, (dict, list)):
        return text_or_doc
    try:
        return json.loads(text_or_doc)
    except ValueError:
        raise DataError("Yahoo sent something that is not JSON")


def parse_chart(text_or_doc, sym=None):
    """v8 chart JSON -> Series. Raises DataError for Yahoo's 'no data' answers."""
    doc = _doc(text_or_doc)
    try:
        chart = doc["chart"]
    except (KeyError, TypeError):
        raise DataError("not a Yahoo chart answer")
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise DataError("Yahoo has no data for %s: %s" % (sym or "?", err.get("description") or "empty"))
    return _series(res[0], sym)


def parse_spark(text_or_doc):
    """Spark JSON -> {SYM.NS: Series} (closes only). Unknown symbols are simply missing."""
    doc = _doc(text_or_doc)
    out = {}
    for item in ((doc.get("spark") or {}).get("result") or []):
        resp = item.get("response") or []
        if resp and resp[0].get("timestamp"):
            try:
                s = _series(resp[0], item.get("symbol"))
            except market.MarketError:
                continue
            out[s.symbol] = s
    return out


# ------------------------------------------------------------------ fetching

def fetch_chart(sym, rng, interval):
    return parse_chart(yahoo_get(chart_path(sym, rng, interval)), sym)


def fetch_spark(symbols, rng, interval, progress=None):
    """({sym: Series}, {sym: reason}) for many symbols, SPARK_MAX a request."""
    got, failed = {}, {}
    syms = [market.normalize_symbol(s) for s in symbols]
    for i in range(0, len(syms), SPARK_MAX):
        batch = syms[i:i + SPARK_MAX]
        try:
            ans = parse_spark(yahoo_get(spark_path(batch, rng, interval)))
        except DataError as e:
            ans = {}
            for s in batch:
                failed[s] = str(e)
        for s in batch:
            if s in ans:
                got[s] = ans[s]
            elif s not in failed:
                failed[s] = "no data from Yahoo"
        if progress:
            progress(min(i + SPARK_MAX, len(syms)), len(syms))
        if i + SPARK_MAX < len(syms):
            SLEEP(PAUSE)
    return got, failed


# ------------------------------------------------------------------ offline

def read_maybe_gz(path):
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")


def _absorb(doc, out, hint):
    if isinstance(doc, dict) and "chart" in doc:
        s = parse_chart(doc)
        out[s.symbol] = s
    elif isinstance(doc, dict) and "spark" in doc:
        out.update(parse_spark(doc))
    elif isinstance(doc, dict):
        for sym, sub in doc.items():
            if sym.startswith("_"):
                continue
            try:
                out[market.normalize_symbol(sym)] = parse_chart(sub, sym)
            except DataError:
                continue  # a symbol Yahoo had no data for, kept in the bundle on purpose
    else:
        raise DataError("%s is not a Yahoo chart, a spark answer or a bundle" % hint)


def load_offline(path):
    """{SYM.NS: Series} from saved Yahoo answers: one file (a v8 chart, a spark answer, or a
    bundle {"SYM.NS": <v8 chart>}; .gz is fine) or a folder of such files."""
    if not os.path.exists(path):
        raise DataError("no offline data at %s" % path)
    files = ([os.path.join(path, n) for n in sorted(os.listdir(path)) if n.endswith((".json", ".json.gz"))]
             if os.path.isdir(path) else [path])
    out = {}
    for f in files:
        try:
            _absorb(json.loads(read_maybe_gz(f)), out, f)
        except ValueError:
            raise DataError("%s is not JSON" % f)
    return out


def save_bundle(path, docs):
    """{sym: raw v8 doc} -> one bundle file (gzip when the name ends in .gz)."""
    raw = json.dumps(docs, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as f:
        f.write(gzip.compress(raw, 9) if path.endswith(".gz") else raw)
