---
name: nse-broker
description: >-
  NSE Order Ticket for Indian intraday trading, paper only. Turns an order that nse-risk
  PASSED (same fingerprint, today, minutes ago, inside the entry window) into a plain ticket you
  type into your own broker app by hand: BUY at market or a capped limit, the stop-loss as SL-M
  or SL with its trigger and limit, the target as an OCO/GTT-OCO or an alert, and the flat_by
  exit, with the warning that two resting sells make a short. Then records your fills and exits
  in today's paper journal for the daily limits. It never connects to any broker, never asks for
  or reads an API key, password, PIN or token, and places nothing. Use when the user says "make
  the order ticket", "how do I place this", "what do I type in my broker app", "I bought at",
  "I sold at", "record my fill", "flatten", "close everything", "how much have I lost today",
  or after /nse-risk passes.
argument-hint: "[ticket | placed --price P | closed SYMBOL --price P | flatten | status]"
---

# nse-broker

The last step, and it stops at paper. The ticket is something you read and type yourself into
your own broker's app (or skip). This skill has no network code at all: no broker API, no keys,
no Yahoo. You stay the only one who can place an order.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Costs come from `${CLAUDE_SKILL_DIR}/../shared/costs.py`,
the journal and fingerprints from `${CLAUDE_SKILL_DIR}/../shared/journal.py`, ticks and times from
`${CLAUDE_SKILL_DIR}/../shared/market.py`.

```bash
python "${CLAUDE_SKILL_DIR}/ticket.py" ticket                   # only after /nse-risk PASSED this order
python "${CLAUDE_SKILL_DIR}/ticket.py" placed --price 1160.60   # you bought; record your real fill
python "${CLAUDE_SKILL_DIR}/ticket.py" closed INFY --price 1167.40 --reason flat_by
python "${CLAUDE_SKILL_DIR}/ticket.py" flatten                  # sell tickets for everything open
python "${CLAUDE_SKILL_DIR}/ticket.py" status                   # today's trades and loss used
```

## The gate

`ticket` refuses, in one line, unless all of these hold:

1. `trader/state/risk.json` says PASS for **this exact order**: its fingerprint (symbol, side,
   product, type, limit, quantity, stop, target, session) matches `trader/order.json`. One share
   more and it must be checked again.
2. The check is from **today** and at most **15 minutes** old (prices move).
3. It is inside the entry window (09:30 to `no_entries_after`, IST).
4. `paper` is true, and this order is not already recorded as placed.

## The ticket

- **Buy**: at MARKET, or a LIMIT at the highest price where the same shares still fit your max
  loss with costs (it says "do not place it above Rs. X").
- **Stop-loss**: SELL, SL-M with the trigger at the stop; or, where the broker has no SL-M for
  intraday equity, SL with the trigger at the stop and a limit 0.3 % under it so it fills in a fall.
- **Target**: an OCO / GTT-OCO with the stop-loss if the broker offers it for MIS; otherwise no
  order, and nse-alerts rings at the target: cancel the stop-loss first, then sell.
- **Never two resting sells** for the same shares: when one fills the other would sell shares you
  no longer hold, a short. v1 is long only.
- **Flat by** `flat_by` (15:10 IST): cancel the stop-loss and sell at market. Brokers also square
  off MIS positions themselves near the close, often for a fee.
- Product MIS (intraday), sized to your own cash: no leverage.

## The journal

`placed --price P` records your real fill (quantity up to the ticket's, price on the tick and
within 5 % of the plan) with its max loss at the stop re-computed from that fill; a fill that
pushes it over your limit gets a warning to consider selling. `closed SYMBOL --price P` records the
exit and the net result after costs. nse-risk reads the journal for the daily loss limit, trades
per day and duplicates. It lives in `trader/state/journal-YYYY-MM-DD.json`.

## Steps

1. After `/nse-risk` PASSES: `ticket.py ticket`. Read the ticket to the user, step by step.
2. Say plainly: nothing was sent anywhere; they place it by hand in their own broker app, or skip it.
   Say once that most retail intraday traders lose money.
3. When they say they bought: `placed --price P` (their real fill). Then `/nse-alerts from-plan` and `watch`.
4. When they say they sold: `closed SYMBOL --price P --reason stop|target|flat_by|manual`, and remind
   them to cancel any order still resting for that stock.
5. At `flat_by`, or when they want out: `flatten`.
6. If the user offers an API key, a password, a PIN, a TOTP or a token: do not take it, do not
   store it, and tell them never to paste it into a chat. This skill has no use for it.

## Example (real data, INFY of Thu 5 Nov 2026)

```
PAPER TICKET 2026-11-05 10:17 IST  (fingerprint 91afb06471e6a085)
Nothing was sent anywhere. Place it by hand in your own broker app, or skip it.
  1. BUY 41 INFY at MARKET, or a LIMIT buy at Rs. 1,160.65 so you never pay more than your max loss allows.
  2. Right after it fills, the stop-loss: SELL 41 INFY, order type SL-M, trigger Rs. 1,149.80.
  3. The target, Rs. 1,181.90, ONE of: (a) an OCO / GTT-OCO ... or (b) no order: /nse-alerts rings ...
  4. By 15:10 IST, if still open: cancel the stop-loss, then SELL 41 INFY at MARKET.
...
placed --price 1160.60   ->  Max loss at the stop with costs: Rs. 495.03.
closed INFY --price 1167.40 --reason flat_by  ->  Net Rs. 228.16 after Rs. 50.64 of costs.
```

## What it never does

It never connects to a broker or any server, never places, changes or cancels an order, never asks
for, reads or stores an API key, password, PIN, TOTP or token, never builds a ticket for an order
that did not pass nse-risk unchanged today, never builds a short, an option, a future or a
leveraged order, and never uses New York time.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Every stop is a plan, not a
guarantee: a gap or a circuit lock can fill worse, or not at all. Most retail intraday traders lose money.
