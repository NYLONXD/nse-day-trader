---
name: nse-exit
description: >-
  NSE Exit Planner for Indian intraday trading, long only. Sets the stop, the target and the
  time exit BEFORE the entry: the stop from the opening range (midpoint or low), a percent,
  a price or an ATR multiple; the target as a multiple of the risk (2R), a percent or a
  price; out by flat_by (15:10 IST default). Rounds onto the stock's NSE tick (the stop away
  from the entry, the target half-up), refuses a target above the upper circuit limit, and
  writes trader/plan.json for nse-timemachine, nse-budget, nse-risk and nse-alerts. It never
  places an order. Use when the user says "set my stop", "where is my stop loss", "plan my
  exit", "what is my target", "stop and target for INFY", "risk reward", or right after
  /nse-entry triggers.
argument-hint: "plan [--stop or-mid|or-low|pct:X|price:X|atr:K] [--target r:K|pct:X|price:X] [--write]"
---

# nse-exit

The third step: decide how you get out before you get in. A plan has an entry, a stop, a
target and a time to be flat, in plain rupees.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Tick rounding, circuit limits and session times
come from `${CLAUDE_SKILL_DIR}/../shared/market.py`; `flat_by`, `no_entries_after` and
`min_reward_risk` from `trader/config.json`.

```bash
python "${CLAUDE_SKILL_DIR}/exit.py" plan                                  # or-mid stop, 2R target, from trader/entry.json
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-low --target r:1.5 --write   # -> trader/plan.json
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5                   # 1.5 x the 14-bar ATR up to the trigger bar
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol SBIN --entry 1006.40 --stop pct:0.8 --target r:2
```

Exit code 0 for a plan, 1 when refused (the reason and the fix in one line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | from nse-entry: entry price, range high/low, trigger bar, tick, circuit limits |
| `trader/config.json` | `flat_by` (15:10), `no_entries_after` (14:30), `min_reward_risk` (1.5) |
| Yahoo chart | only for `atr:K`: the session's 5-minute bars up to the trigger bar |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its rule, risk and reward a share, reward:risk, flat_by, tick, limits, notes |

## Stops and targets

| stop | where |
| --- | --- |
| `or-mid` (default) | the opening-range midpoint |
| `or-low` | one tick under the opening-range low: wider, so fewer shares for the same max loss |
| `pct:X` | X % under the entry |
| `price:X` | your price |
| `atr:K` | K x the average true range of the session's 5-minute bars up to the trigger bar (14 bars, or all of them before 10:25, and it says so). The first bar's range ignores the overnight gap |

| target | where |
| --- | --- |
| `r:K` (default `r:2`) | entry + K x the risk a share |
| `pct:X` | X % over the entry |
| `price:X` | your price |

- **Ticks**: the stock's own tick (0.01 to 5.00, from nse-entry). The stop rounds AWAY from the
  entry (down for a long), so rounding never tightens it; the target rounds half-up.
- **Refused, with the fix**: a stop at or above the entry; a stop within 2 ticks of it (noise
  would hit it); a target at or below the entry; a target above today's upper circuit limit
  (no trade can happen above it); a trigger at or after `no_entries_after`.
- **Noted**: a stop below today's lower circuit limit (in a lower-circuit lock you may not be
  able to sell at all); circuit limits unknown; reward:risk under `min_reward_risk` (nse-risk
  will refuse it).
- **Flat by** `flat_by` (15:10 IST): out by then whatever the price. Brokers also auto
  square-off intraday positions at their own time (often 15:15-15:25, sometimes with a
  charge); keep `flat_by` before your broker's time.
- Reward:risk here is before costs. nse-timemachine, nse-budget and nse-risk add the
  round-trip cost (brokerage, STT, exchange charges, GST, stamp duty).

## Steps

1. Run it right after `/nse-entry` triggers (or with `--symbol --entry` for a price the user
   gives).
2. Use the default stop (`or-mid`) for an opening-range breakout unless the user names another.
3. Run `exit.py plan --write`. Read back the entry, stop and its rule, target and its rule,
   risk a share, reward:risk and the flat-by time.
4. If it refuses, say the reason and the fix (a wider stop, a lower target).
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/nse-timemachine` to replay the plan, then `/nse-budget` to size it.

## Example (real data, Thu 5 Nov 2026)

```
exit.py plan        (after INFY triggered at 09:40 IST at Rs. 1,160.50)
Plan for INFY (long), session 2026-11-05:
  entry     Rs. 1,160.50  the trigger bar's close at 09:40 IST
  stop      Rs. 1,149.80  or-mid: opening-range midpoint 1,149.80, rounded down to the 0.05 tick
  target    Rs. 1,181.90  r:2: 2 x the risk a share
  risk a share Rs. 10.70 (0.92%), reward a share Rs. 21.40, reward:risk 2.00
  flat by 15:10 IST whatever the price.
```

BIRLACABLE (5 % band, upper circuit Rs. 346.60 that day), entry Rs. 340.25, `--target r:3`:
`Refused: the target 357.50 is above today's upper circuit limit 346.60: it cannot fill there.`

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. No profit forecasts.
- Long side only in v1.

## What it never does

It never places an order, never sizes a position (that is nse-budget), never promises the stop
will fill at its price, never widens a stop after the entry, and never uses New York time.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Every stop is a plan,
not a guarantee. Most retail intraday traders lose money.
