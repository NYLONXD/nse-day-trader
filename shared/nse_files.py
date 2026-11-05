"""NSE's public list files: the universe (EQUITY_L.csv, index lists) and price bands
(sec_list.csv). Standard library only.

NSE's site may refuse scripts. This module asks once, with an honest User-Agent, and if NSE
says no, the message tells you to download the file in your browser and pass its path.
"""

import csv
import io

import market
import yahoo

URLS = {
    "equity": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "nifty50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "bands": "https://nsearchives.nseindia.com/content/equities/sec_list.csv",
}
FILE_NAMES = {"equity": "EQUITY_L.csv", "nifty500": "ind_nifty500list.csv",
              "nifty50": "ind_nifty50list.csv", "bands": "sec_list.csv"}
TRADABLE_SERIES = ("EQ",)  # BE/BZ are trade-for-trade (delivery only): no intraday


def download(kind):
    url = URLS[kind]
    try:
        return yahoo.http_get(url, timeout=30)
    except Exception as e:
        raise yahoo.DataError("NSE did not send %s (%s). Download it in your browser from %s "
                              "and pass the saved file's path instead." % (FILE_NAMES[kind], e, url))


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError as e:
        raise yahoo.DataError("cannot read %s: %s" % (path, e))


def _rows(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        yield {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def parse_listing(text):
    """EQUITY_L.csv, an index list (ind_nifty500list.csv), or a plain list (one symbol a line,
    optional SYMBOL header) -> (rows, kind). rows: [{"symbol", "name", "series"}]; series is
    "" when the file has none."""
    lines = [l for l in text.lstrip("﻿").splitlines() if l.strip()]
    if not lines:
        raise yahoo.DataError("the symbol list is empty")
    if "," not in lines[0]:
        body = lines[1:] if lines[0].strip().lower() == "symbol" else lines
        return [{"symbol": l.strip(), "name": "", "series": ""} for l in body], "plain list"
    out, kind = [], None
    for r in _rows(text):
        if "symbol" not in r:
            raise yahoo.DataError("the CSV has no SYMBOL column (columns: %s)" % ", ".join(r))
        if kind is None:
            kind = ("EQUITY_L" if "name of company" in r else "index list" if "company name" in r else "csv")
        out.append({"symbol": r["symbol"], "name": r.get("name of company") or r.get("company name") or "",
                    "series": r.get("series", "")})
    return out, kind or "csv"


def parse_bands(text):
    """sec_list.csv -> {SYM.NS: {"band": "20" | "No Band", "remarks": "-"}} for EQ rows."""
    out = {}
    for r in _rows(text):
        if r.get("series") not in TRADABLE_SERIES:
            continue
        try:
            sym = market.normalize_symbol(r.get("symbol"))
            market.parse_band(r.get("band"))
        except market.MarketError:
            continue
        out[sym] = {"band": r.get("band"), "remarks": r.get("remarks", "")}
    if not out:
        raise yahoo.DataError("the price-band file has no EQ rows (is it sec_list.csv?)")
    return out
