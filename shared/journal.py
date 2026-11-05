"""Today's paper journal: what you told the toolkit you placed by hand, and how it ended.
Standard library only. nse-broker writes it (after you place a ticket yourself in your own
broker app and say so); nse-risk reads it for the daily loss, trades a day and duplicates.

  trader/state/journal-YYYY-MM-DD.json
  {"date": "2026-11-05",
   "entries": [{"symbol", "qty", "entry", "stop", "target", "max_loss_inr", "fingerprint",
                "placed_at", "closed": false, "exit_price": null, "realised_inr": null}]}

Daily loss used = the realised loss of closed trades + the full max loss (at the stop, with
costs) of open ones. A winning trade does not give room back: the limit is about losses.
"""

import datetime as _dt
import hashlib
import json
import os

import config
import market


def path(day):
    return os.path.join(config.trader_dir(), "state", "journal-%s.json" % day)


def load(day):
    try:
        with open(path(day), encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return {"date": str(day), "entries": []}
    except ValueError:
        raise market.MarketError("the journal %s is not valid JSON; fix or remove it" % path(day))
    if doc.get("date") != str(day):
        raise market.MarketError("the journal %s says it is for %s" % (path(day), doc.get("date")))
    doc.setdefault("entries", [])
    return doc


def save(doc):
    p = path(doc["date"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    os.replace(tmp, p)


def loss_used(doc):
    used = 0.0
    for e in doc["entries"]:
        if e.get("closed"):
            used += max(0.0, -(e.get("realised_inr") or 0.0))
        else:
            used += e.get("max_loss_inr") or 0.0
    return round(used, 2)


def symbols_entered(doc):
    return {e["symbol"] for e in doc["entries"]}


# ------------------------------------------------------------------ order fingerprints
# nse-risk stamps the exact order it passed; nse-broker builds a ticket only for an order
# whose fingerprint matches a PASS from today. Any edit (one share more) needs a new check.

FINGERPRINT_FIELDS = ("symbol", "side", "product", "order_type", "limit_price", "qty", "stop", "target", "session")


def fingerprint(order):
    core = {k: order.get(k) for k in FINGERPRINT_FIELDS}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def risk_path():
    return os.path.join(config.trader_dir(), "state", "risk.json")



# ------------------------------------------------------------------ fresh prices
# trader/prices.json {"INFY.NS": {"price": 1164.2, "time": "2026-11-05 10:15 IST"}} (nse-alerts
# writes it). nse-budget and nse-risk both size and check from a price only if it is fresh.

FRESH = _dt.timedelta(minutes=15)


def fresh_price(sym, now, path=None):
    """(price, why) for today and 15 minutes old or less; (None, why not) otherwise."""
    path = path or os.path.join(config.trader_dir(), "prices.json")
    if not os.path.exists(path):
        return None, "no trader/prices.json"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except ValueError:
        return None, "trader/prices.json is not valid JSON"
    row = doc.get(sym)
    if not row:
        return None, "no %s in trader/prices.json" % market.bare_symbol(sym)
    when = market.parse_ist(str(row["time"]).replace(" IST", ""))
    if when.date() != now.date() or now - when > FRESH or when > now:
        return None, "the price in trader/prices.json is from %s: too old to use" % row["time"]
    return float(row["price"]), row["time"]
