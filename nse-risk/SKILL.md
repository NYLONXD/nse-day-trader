---
name: nse-risk
description: >-
  NSE Risk Manager for Indian intraday trading, the safety gate. Checks one proposed order
  (trader/order.json from nse-budget) against every rule before a ticket can be built: long
  only, intraday MIS, prices on the stock's tick, a stop and a target, not blocked, a normal
  NSE day (holidays, weekends, special sessions refused), inside the entry window (09:30 to
  no_entries_after IST), circuit limits, max loss per trade and net reward:risk after costs,
  the daily loss limit, trades per day, duplicates, order size and cash with no leverage. Says
  PASS or BLOCK check by check, and stamps a fingerprint nse-broker needs. It never places an
  order. Use when the user says "check this trade", "is this within my rules", "risk check",
  "can I take this trade", "am I over my daily limit", or after /nse-budget, before /nse-broker.
argument-hint: "[--order FILE] [--now \"YYYY-MM-DD HH:MM\"]"
---

# nse-risk

The fifth step and the gate. Nothing reaches nse-broker without a PASS from here for that exact
order, today. It re-computes every number itself; it never trusts the order file's arithmetic,
and it never changes a limit to make a trade pass.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Rules come from
`${CLAUDE_SKILL_DIR}/../shared/market.py` (calendar, window, ticks), costs from
`${CLAUDE_SKILL_DIR}/../shared/costs.py`, today's entries from
`${CLAUDE_SKILL_DIR}/../shared/journal.py`.

```bash
python "${CLAUDE_SKILL_DIR}/check.py"                 # trader/order.json, now
python "${CLAUDE_SKILL_DIR}/check.py" --json
```

Exit code 0 PASS, 1 BLOCK. The verdict and the order's fingerprint go to `trader/state/risk.json`.

## The checks

| check | blocks when |
| --- | --- |
| symbol, quantity | not an NSE symbol; not a whole number of shares, 1 or more |
| long only, product, order type | not a BUY; not intraday (MIS); not MARKET or LIMIT |
| ticks | the stop, the target or the limit price is off the stock's tick (the broker would reject it) |
| blocked list | the symbol is in `blocked` |
| calendar | today is a weekend, an NSE holiday, a special session, or a year `data/nse_holidays.json` does not cover |
| session | the order is for another day |
| entry window | before 09:30 (opening range still forming) or at/after `no_entries_after` |
| stop / target side | the stop is not under the entry; the price is already at or over the target |
| upper circuit | the entry is within a tick of the upper circuit, or the target is above it |
| max loss | the loss at the stop, with slippage and every charge, is over `max_loss_per_trade_inr` |
| reward:risk | the NET reward:risk (gain at target after costs / loss at stop with costs) is under `min_reward_risk` |
| daily loss | today's loss used + this trade's max loss is over `daily_loss_limit_inr` |
| trades today | `max_trades_per_day` entries already in today's journal |
| duplicate | the symbol was already entered today |
| min/max order, position | the order value is under `min_order_inr`, over `max_order_inr`, or over `max_position_pct` of capital |
| cash, no leverage | the order plus today's open positions would use more than capital less the cash buffer |

How the numbers work:

- **The entry it measures from**: the limit price for a LIMIT order. For a MARKET order, the
  HIGHER of the order's entry and a fresh price from `trader/prices.json` (today, 15 minutes old
  or less). A plan written at 10:15 cannot understate the risk at 10:40 after a run-up.
- **Daily loss used** = realised losses of today's closed trades + the full max loss of open ones.
  A win does not give room back.
- **Warnings** never block but are said: circuit limits unknown, a stop under the lower circuit,
  an old price, the Time Machine leaning against the plan (or not run), placeholder cost rates.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/order.json` | from nse-budget |
| `trader/plan.json` | circuit limits, tick, entry |
| `trader/config.json` | every limit and `costs` |
| `trader/prices.json` | optional fresh price |
| `trader/timemachine.json` | optional: the replay verdict, a warning only |
| `trader/state/journal-YYYY-MM-DD.json` | today's entries (nse-broker records them) |
| `trader/state/risk.json` | the verdict, the checks, the numbers, the order and its fingerprint |

## Steps

1. If the price may have moved since the plan, refresh it first (`/nse-alerts quote SYMBOL --write`).
2. Run `check.py`. Exit 0 means PASS, 1 means BLOCK.
3. Tell the user in plain words: the loss at the stop with costs, the net reward:risk, how much of
   today's loss limit and trades it uses, and every WARN. For a BLOCK give the reason and the fix
   (fewer shares from /nse-budget, a closer stop from /nse-exit, wait for 09:30, or skip it).
   Never change a limit yourself.
4. If the order changes in any way, check it again: nse-broker matches the fingerprint, and a
   PASS from another day does not count.

## Example (real data, INFY of Thu 5 Nov 2026, at 10:16 IST)

```
  PASS  max loss          Rs. 492.98 at the stop with costs and slippage, limit Rs. 500.00
  PASS  reward:risk       net 1.67, at least 1.50
  PASS  daily loss        Rs. 0.00 used today + Rs. 492.98 = Rs. 492.98 of Rs. 1,500.00
  WARN  time machine      the replays lean against this plan: Strongly toward the stop (average -0.41R net)
PASS: every rule holds (fingerprint 91afb06471e6a085).
```

A PASS means the rules hold, not that the trade is good: here the replays lean against it, and
whether to skip it is the user's call.

## What it never does

It never places an order, never edits an order or a limit to make it pass, never passes a short,
an option, a future or a leveraged order, never checks a holiday or weekend as open, and never
uses New York time.

## Safety

Paper and simulation only. Education and tooling, not financial advice. A PASS is a rules check,
not a recommendation. Every stop is a plan, not a guarantee. Most retail intraday traders lose money.
