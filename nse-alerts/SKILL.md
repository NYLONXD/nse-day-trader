---
name: nse-alerts
description: >-
  NSE Price Alerts for Indian intraday trading. Rings when an NSE stock touches a price,
  when your plan's stop or target is hit (they cancel each other, like OCO), and at the times
  that matter in IST: the 09:15 opening bell, 09:30 opening range complete,
  no_entries_after, flat_by and the 15:30 close. Also gets a quote and saves it to
  trader/prices.json so nse-budget and nse-risk measure risk from a fresh price. Free Yahoo
  .NS bars, bar by bar. It never places an order. Use when the user says "alert me", "tell me
  when RELIANCE hits", "ring at my stop", "watch my target", "remind me at 3:10", "what is the
  price of INFY", "quote SBIN", "set alerts from my plan", or after /nse-broker.
argument-hint: "[quote SYMBOL | add SYMBOL above|below PRICE | from-plan | bells | check | watch]"
---

# nse-alerts

The watcher. After a trade is placed by hand, it tells you when the plan says to act: the stop,
the target, flat_by. It also gets a fresh price for the sizing and risk checks.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Session times come from
`${CLAUDE_SKILL_DIR}/../shared/market.py`, bars from `${CLAUDE_SKILL_DIR}/../shared/yahoo.py`.

```bash
python "${CLAUDE_SKILL_DIR}/alerts.py" quote INFY --write        # -> trader/prices.json
python "${CLAUDE_SKILL_DIR}/alerts.py" from-plan                 # stop, target, no_entries_after, flat_by
python "${CLAUDE_SKILL_DIR}/alerts.py" bells                     # 09:15, 09:30, 15:30
python "${CLAUDE_SKILL_DIR}/alerts.py" add RELIANCE above 1325 --note "range high"
python "${CLAUDE_SKILL_DIR}/alerts.py" add-time 13:00 --note "check the plan"
python "${CLAUDE_SKILL_DIR}/alerts.py" check                     # one look; exit 0 if something rang
python "${CLAUDE_SKILL_DIR}/alerts.py" watch --every 60          # until flat_by (or --until HH:MM)
python "${CLAUDE_SKILL_DIR}/alerts.py" list
```

Flags: `--now` and `--offline` replay saved charts, `--bell` sounds the terminal bell.

## How it works

- **Price alerts** ring when a completed 5-minute bar since the alert was made touched the price
  (its high for `above`, its low for `below`), or the latest trade did. It says which bar.
- **The plan's stop and target** (from `from-plan`) are a pair: when one rings, the other is
  cancelled. They carry the action: at the stop, check that your stop-loss order filled; at the
  target, CANCEL the resting stop-loss first, then sell (never two resting sells for the same
  shares: the second one would make a short).
- **Time alerts** ring at their IST time on a trading day. Added late, they say the time now.
- Each alert rings once. Alerts belong to one session; tomorrow starts empty.
- On a holiday, weekend or special session it says so and watches nothing.
- **quote** live is Yahoo's latest trade and its time; if it is more than 15 minutes old during the
  session it warns (nse-budget and nse-risk will not use it). `--write` saves it to `trader/prices.json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | for `from-plan`: symbol, stop, target, session |
| `trader/config.json` | `no_entries_after`, `flat_by` |
| `trader/alerts.json` | today's alerts and when each rang |
| `trader/prices.json` | written by `quote --write`: `{"INFY.NS": {"price", "time"}}` |

## Steps

1. Before sizing, if the plan is a few minutes old: `quote SYMBOL --write`, then /nse-budget and /nse-risk.
2. After the user places the ticket by hand: `from-plan`, then `watch` in the background (it checks
   every `--every` seconds, at least 30, until `flat_by`).
3. When it rings, tell the user the alert and the action in one line, and that nothing was placed.
4. After they sell, `/nse-broker closed SYMBOL --price P` records it.

## Example (real data, INFY of Thu 5 Nov 2026, plan stop 1,154.65 / target 1,169.30)

```
RING [10:26] INFY above 1,169.30: touched 1,169.90 at the 10:20 bar. TARGET: your plan says sell now
(cancel the resting stop-loss first) (The below alert is cancelled.)
RING [14:31] 14:30 IST (now 14:31): no new entries from now (no_entries_after)
RING [15:11] 15:10 IST (now 15:11): FLAT BY: sell anything still open, at market
```

## What it never does

It never places, changes or cancels an order, never calls the data real time, never uses New York
time, and never rings on a closed day.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Yahoo bars can be late: an
alert can ring after the price has moved on. Every stop is a plan, not a guarantee.
