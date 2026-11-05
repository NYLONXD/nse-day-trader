---
name: nse-budget
description: >-
  NSE Budget Manager for Indian intraday trading, long only, no leverage. Sizes one trade
  to your capital and your max loss per trade in rupees, NET OF COSTS: the most whole shares
  whose loss at the stop, with slippage and every charge (brokerage, STT, exchange, SEBI,
  GST, stamp duty), stays within max_loss_per_trade_inr, capped by a position limit, a max
  order and your own cash (never the broker's MIS margin). Writes the proposed order to
  trader/order.json for nse-risk. It never places an order. Use when the user says "how many
  shares", "size my position", "position size", "how much should I buy", "what can I afford",
  "set my budget", "my capital is", or after /nse-timemachine, before /nse-risk.
argument-hint: "[--limit PRICE] [--write]"
---

# nse-budget

The fourth step: how many shares, in whole rupees, so one stopped trade costs no more than
you decided, after costs. It sizes; it does not place.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Costs and the sizing rule come from
`${CLAUDE_SKILL_DIR}/../shared/costs.py` (nse-risk and nse-timemachine use the same one), tick
and money rules from `${CLAUDE_SKILL_DIR}/../shared/market.py`.

```bash
python "${CLAUDE_SKILL_DIR}/../shared/config.py" init      # once: trader/config.json with the defaults
python "${CLAUDE_SKILL_DIR}/size.py"                         # size trader/plan.json at its entry
python "${CLAUDE_SKILL_DIR}/size.py" --write                 # -> trader/order.json
python "${CLAUDE_SKILL_DIR}/size.py" --limit 1160.00 --write # a LIMIT buy at your price (on the tick)
```

Exit code 0 sized, 1 refused (one line: the reason and the fix).

## Step 1: set the rules once

If `trader/config.json` does not exist, run `config.py init`, then ask the user for each value
in plain words and write their answers in:

- **capital_inr** (100000): the money this is for. Only money they can afford to lose.
- **max_loss_per_trade_inr** (500): the most one trade may lose if the stop fills at the stop,
  costs included.
- **daily_loss_limit_inr** (1500) and **max_trades_per_day** (3): nse-risk stops the day there.
- **cash_buffer_pct** (10), **max_position_pct** (50), **max_order_inr** (50000),
  **min_order_inr** (1000), **min_reward_risk** (1.5, net of costs), **blocked** ([]).
- **costs**: placeholders until they put in their broker's real rates (see nse-timemachine).
- **paper** stays true: v1 is paper and simulation only.

## Step 2: size the trade

The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | the most shares whose loss at the stop (entry + slippage to stop - slippage, plus every charge) is at most `max_loss_per_trade_inr` |
| position | `capital_inr` x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (`capital_inr` less `cash_buffer_pct`, less today's open positions from the journal) / entry. Your own cash only: v1 never uses the broker's intraday (MIS) margin |

The entry it sizes at: `--limit PRICE` (a limit buy, no slippage); otherwise the plan's entry,
or a fresh (15 minutes or less) HIGHER price from `trader/prices.json` (nse-alerts writes it).
A price that ran up since the plan means a bigger loss at the same stop, so fewer shares.

It refuses, and writes nothing, when: the size is 0 shares (it says which limit and the fix),
the order is under `min_order_inr`, the stock is blocked, the plan is for another day, the price
is at or under the stop (the setup is gone) or at or over the target (the move happened), the
limit price is off the tick, or the plan is not a long.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | from nse-exit: entry, stop, target, tick, session |
| `trader/config.json` | the limits and `costs` |
| `trader/prices.json` | optional, from nse-alerts: a fresh price |
| `trader/state/journal-YYYY-MM-DD.json` | today's open positions (they use cash) |
| `trader/order.json` | with `--write`: the proposed order (symbol, BUY, MIS, MARKET or LIMIT, qty, stop, target, loss at stop, gain at target, charges, slippage, net reward:risk, the four limits) |

## Steps

1. Make sure `trader/config.json` holds the user's own numbers (Step 1).
2. `size.py --write`. Show the four limits, which one decided the size, the shares, the rupees,
   the loss at the stop split into price, charges and slippage, and the net reward:risk.
3. Say plainly that a stop is a plan, not a guarantee: a gap or a circuit lock can lose more.
4. Hand over to `/nse-risk`.

## Example (real data, INFY plan of Thu 5 Nov 2026)

Entry 1,160.50, stop 1,149.80, target 1,181.90, capital Rs. 1,00,000, max loss Rs. 500:

```
  max loss       41 shares   <- decides it
  position       43 shares
  max order      43 shares
  cash           77 shares
  41 shares = Rs. 47,582.55 of Rs. 1,00,000.00 capital (no leverage).
  At the stop 1,149.80: lose Rs. 492.98, of which Rs. 50.18 is charges and Rs. 4.10 slippage.
  At the target 1,181.90: make Rs. 824.33 after Rs. 51.02 of costs. Net reward:risk 1.67.
```

Sizing on the price alone (Rs. 500 / 10.70) would say 46 shares, which would lose about Rs. 553
at the stop once costs are in.

## What it never does

It never places an order, never uses margin or leverage, never sizes a short, an option or a
future, never rounds a size up, and never changes a limit in the config by itself.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Only money you can
afford to lose. Most retail intraday traders lose money.
