---
name: nse-timemachine
description: >-
  NSE Time Machine for Indian intraday trading, long only. Before you size or place
  anything, it replays your plan (entry, stop, target, flat by 15:10 IST) 10,000 times from
  the stock's own last 60 days of real 5-minute .NS bars, and re-runs the same opening-range
  rule on every one of those past sessions, all NET OF COSTS (brokerage, STT, exchange
  charges, SEBI fee, GST, stamp duty, slippage). Gives the odds of target first, stop first
  or closed at flat_by, net R and rupees, the break-even win rate after costs, and a one-line
  verdict. Fixed seed, so the numbers repeat. A simulation, not a prediction. Use when the
  user says "replay this trade", "what are the odds", "backtest my plan", "would this have
  worked", "time machine", "simulate INFY", "how often does the stop get hit", or after
  /nse-exit, before /nse-budget.
argument-hint: "run [--plan FILE] [--replays 10000] [--seed 915] [--qty N] [--write]"
---

# nse-timemachine

Between the plan and the money. nse-exit has set the entry, the stop, the target and the
flat-by time. This asks: if the rest of today plays out like this stock's own recent days,
how often does the plan reach the target first, the stop first, or neither, and what is left
after costs? It answers with 10,000 replays and with the real record of the same rule on every
past session. A simulation, not a prediction. Most retail intraday traders lose money; a
good-looking replay does not change that.

`${CLAUDE_SKILL_DIR}` is this skill's folder. The breakout, stop and target rules come from
`${CLAUDE_SKILL_DIR}/../shared/orb.py`, costs from `${CLAUDE_SKILL_DIR}/../shared/costs.py`,
market rules from `${CLAUDE_SKILL_DIR}/../shared/market.py`.

```bash
python "${CLAUDE_SKILL_DIR}/timemachine.py" run                      # trader/plan.json, 10,000 replays, seed 915
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --write              # also save trader/timemachine.json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --qty 40             # cost it for 40 shares
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol INFY --date 2026-11-05 \
    --entry 1160.5 --stop 1149.8 --target 1181.9 --entry-bar 09:35
```

Flags: `--replays N`, `--seed N`, `--window N` (default 2 bars), `--no-history`,
`--offline PATH` and `--now "YYYY-MM-DD HH:MM"` (IST) to replay saved data, `--json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | from nse-exit: symbol, session, entry, trigger bar, stop and stop rule, target and target rule, tick |
| `trader/config.json` | `flat_by`, `no_entries_after`, `max_loss_per_trade_inr` (sets the quantity), `costs` |
| `data/nse_holidays.json` | the plan's day must be a normal session; pool days must be too |
| Yahoo chart | one request: `range=60d&interval=5m` (Yahoo keeps 5-minute bars about 60 trading days) |
| `trader/timemachine.json` | with `--write`: plan, quantity, costs, odds, net R and rupee percentiles, fan, verdict, history rows, what really happened |

## How it works

1. **The pool.** Every FULL session before the plan's day: a normal NSE day by the holidays
   file, and every 5-minute bar from 09:15 to `flat_by` present with prices. Bars from 15:15
   on are never needed (Yahoo leaves them null). Days with a gap (like 15 Oct 2026, empty at
   11:45 and 11:50) are left out and counted. Fewer than 20 full sessions: it refuses.
2. **One replay.** Start at the entry. For each bar until `flat_by`, draw a past bar from the
   same time of day (+-2 bars) of a random pool session and apply its moves (open, high, low,
   close over the bar before) to the current price. Each bar: an open at or under the stop
   exits at that open (a gap through the stop, worse than the stop); otherwise a low at or under
   the stop exits at the stop; otherwise a high at or over the target exits at the target. A bar
   that touches both counts as the stop. Still open at `flat_by`: out at the close.
3. **Net of costs.** The quantity is `max_loss_per_trade_inr / risk a share` (or `--qty`).
   Slippage: `slippage_ticks` against you on the entry and on stop and time exits (market
   orders), none on the target (a limit order). Then the round-trip costs: brokerage, STT on
   the sell, exchange transaction charges, SEBI fee, GST, stamp duty on the buy. R = net rupees
   / (risk a share x quantity), so a stop is a bit worse than -1R and a 2R target a bit less
   than +2R.
4. **The history.** The plan's own rule (the day's own opening range, the same stop rule and
   target rule, the same tick, `no_entries_after`) on every pool session: what really
   happened, net of costs. A `price:X` stop or target becomes the same percent of the entry.
   Level setups skip the history (a price level does not carry to other days).
5. **The verdict** compares the replays that hit a side with the **break-even after costs**:
   the share of target-first a plan needs so that wins pay for losses, net. Under 5 points from
   it: "Close to break-even after costs". 5 to 15: "Leans toward". 15 or more: "Strongly
   toward". Extra lines when more than 40 % close at `flat_by`, and when the average net R
   points against the lean.
6. **What really happened** on the plan's own day, once that session is past `flat_by`.

**The seed.** `random.Random(seed)`, seed 915 by default: same plan, same data, same seed, the
same numbers on any machine. Say the seed with every result.

**Unbiased.** A test replays a pool made of every real session and its mirror image (every up
move turned down): a symmetric plan then comes out even (target 37.8 %, stop 37.9 %).

## Costs: placeholders until you change them

The rates in `trader/config.json` under `"costs"` ship as PLACEHOLDERS (0.03 % or Rs. 20
brokerage an order, STT 0.025 % on the sell, exchange 0.00297 %, SEBI 0.0001 %, stamp 0.003 %
on the buy, GST 18 %, 1 tick slippage). Every result says how many are still placeholders.
Get your broker's real numbers from its pricing page or brokerage calculator, and check them
against a real contract note, which lists every charge line by line.

## Steps

1. Run it after `/nse-exit plan --write`: `timemachine.py run`.
2. Tell the user the pool (sessions, dates), the quantity and the costs, then the odds (target
   first, stop first, closed at flat_by), the average net R, the verdict, and the history line.
   Say the seed and that the cost rates are placeholders if they are.
3. If it refuses (a holiday, too few sessions, a bad plan), say why in one line.
4. Hand off to `/nse-budget` to size it, then `/nse-risk` to check it.

## Example (real data, INFY plan of Thu 5 Nov 2026)

```
Pool: 58 full sessions, 2026-08-11 to 2026-11-04; left out 1 missing or empty bars.
Quantity 46, risk Rs. 492.20. Round-trip costs at the target Rs. 57.24 (0.12R).
Net of costs and 1 tick(s) slippage: a target hit is +1.88R, a stop -1.12R.
10000 replays (seed 915, +-2 bars):
  target first   5.2%   stop first  52.3%   closed at flat_by  42.5%   net positive  29.9%
Verdict: Strongly toward the stop: of the replays that hit a side, 9% hit the target first;
break-even after costs needs 37%.
History, the same rule on 58 past sessions: triggered on 19. Target 1, stop 11, flat_by 7.
```

The INFY plan needs a +1.84 % move (1.7 standard deviations of its rest-of-day moves) before a
-0.92 % one, and INFY's intraday drift after 09:35 was slightly down over those 58 sessions.

## Rules

- A simulation from the stock's own recent bars, not a forecast. Say so.
- Always net of costs; say when the cost rates are placeholders.
- Never round a result up, never hide the history when it disagrees with the replays.
- 5-minute bars only, never the plan's own day after the entry (no peeking).

## What it never does

It never places, sizes or approves an order, never says a plan "will work", never uses New
York time, never replays a holiday, weekend or special session, and never leaves costs out.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Every stop is a plan,
not a guarantee: a gap or a circuit lock can fill worse than the stop, or not at all.
