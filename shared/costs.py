"""Round-trip costs of one NSE intraday (MIS) equity trade, so every result is net of costs.
Standard library only. Used by nse-timemachine, nse-budget and nse-risk.

THE DEFAULT RATES ARE PLACEHOLDERS. Replace them in trader/config.json ("costs") with your
own broker's numbers. Where to find them:
  - your broker's pricing / brokerage-calculator page (brokerage, and which charges it passes on);
  - a contract note from your broker: it lists every charge of a real trade line by line;
  - NSE's circulars for the exchange transaction charge, SEBI's for the turnover fee;
  - the Finance Act / your contract note for STT and stamp duty.
Rates change; check them again when your broker or the government announces a revision.

Model (percent of the order's value; a round trip is one buy and one sell):
  brokerage   min(brokerage_pct_per_order % of value, brokerage_max_inr_per_order) per order
  STT         stt_sell_pct % of the SELL value (intraday equity)
  exchange    exchange_txn_pct % of both legs
  SEBI fee    sebi_fee_pct % of both legs
  stamp duty  stamp_duty_buy_pct % of the BUY value
  GST         gst_pct % of (brokerage + exchange + SEBI fee)
Plus slippage: slippage_ticks ticks against you on the entry and on stop / time exits (market
orders). A target is a limit order and fills at its price.
"""

DEFAULT_COSTS = {
    "_note": "PLACEHOLDERS: replace with your broker's real numbers (see shared/costs.py)",
    "brokerage_pct_per_order": 0.03,
    "brokerage_max_inr_per_order": 20.0,
    "stt_sell_pct": 0.025,
    "exchange_txn_pct": 0.00297,
    "sebi_fee_pct": 0.0001,
    "stamp_duty_buy_pct": 0.003,
    "gst_pct": 18.0,
    "slippage_ticks": 1,
}
RATE_KEYS = [k for k in DEFAULT_COSTS if not k.startswith("_")]


def check(c):
    """Plain problems with a costs dict (empty = fine)."""
    probs = []
    for k in RATE_KEYS:
        v = c.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            probs.append("costs.%s must be a number, 0 or more (got %r)" % (k, v))
    for k in c:
        if not k.startswith("_") and k not in DEFAULT_COSTS:
            probs.append("costs.%s is not a known cost (known: %s)" % (k, ", ".join(RATE_KEYS)))
    return probs


def placeholder_keys(c):
    """Rates still at the shipped placeholder value (say so next to every net result)."""
    return [k for k in RATE_KEYS if c.get(k) == DEFAULT_COSTS[k]]


def brokerage(value, c):
    return min(value * c["brokerage_pct_per_order"] / 100.0, c["brokerage_max_inr_per_order"])


def round_trip(buy_price, sell_price, qty, c):
    """Every charge of buying `qty` at buy_price and selling at sell_price, in rupees."""
    buy, sell = buy_price * qty, sell_price * qty
    brk = brokerage(buy, c) + brokerage(sell, c)
    stt = sell * c["stt_sell_pct"] / 100.0
    exch = (buy + sell) * c["exchange_txn_pct"] / 100.0
    sebi = (buy + sell) * c["sebi_fee_pct"] / 100.0
    stamp = buy * c["stamp_duty_buy_pct"] / 100.0
    gst = (brk + exch + sebi) * c["gst_pct"] / 100.0
    parts = {"brokerage": brk, "stt": stt, "exchange": exch, "sebi": sebi, "stamp": stamp, "gst": gst}
    out = {k: round(v, 2) for k, v in parts.items()}
    out["total"] = round(sum(parts.values()), 2)
    return out


def total(buy_price, sell_price, qty, c):
    return round_trip(buy_price, sell_price, qty, c)["total"]
