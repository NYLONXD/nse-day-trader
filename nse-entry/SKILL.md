---
name: nse-entry
description: >-
  NSE Entry Timer for Indian intraday trading, long only. Watches one NSE stock bar by
  bar on free Yahoo 5-minute .NS bars, builds the 09:15-09:30 IST opening range, and
  calls the entry on the first completed bar that CLOSES above the range high (or above a
  level you give), with an optional volume check. Says forming, waiting and how far,
  triggered with the time and the price, expired, unusable, circuit (touched its circuit
  limit) or closed (holiday, weekend, special session), and writes trader/entry.json for
  nse-exit. It never places an order. Use when the user says "tell me when to get in",
  "watch RELIANCE", "has INFY broken out", "did it break the opening range", "entry
  signal", "time my entry", "check my ORB list", or after /nse-scanner, before /nse-exit.
argument-hint: "[check | watch | watchlist] [SYMBOL] [--setup orb|level --level PRICE]"
---

# nse-entry

The second step of an NSE intraday day: one stock, one rule, bar by bar. It tells you the
moment a setup triggers. A signal is information, not an order and not a promise.

`${CLAUDE_SKILL_DIR}` is this skill's folder. The breakout rule itself lives in
`${CLAUDE_SKILL_DIR}/../shared/orb.py` (the scanner and the Time Machine use the same one),
and every market rule in `${CLAUDE_SKILL_DIR}/../shared/market.py`.

```bash
python "${CLAUDE_SKILL_DIR}/entry.py" check INFY                        # one look -> trader/entry.json on a trigger
python "${CLAUDE_SKILL_DIR}/entry.py" check SBIN --setup level --level 1010
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --min-volume-x 1.5      # trigger bar volume >= 1.5 x the day's average
python "${CLAUDE_SKILL_DIR}/entry.py" watch INFY --until 11:00          # re-checks after every 5-minute bar
python "${CLAUDE_SKILL_DIR}/entry.py" watchlist                         # every symbol in trader/orb_hits.json
```

Flags: `--now "2026-11-05 10:15"` (IST) and `--offline PATH` replay saved charts, `--no-write`,
`--json`. Exit code 0 when triggered, 1 otherwise (one plain line says why).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orb_hits.json` | from nse-scanner: the list `watchlist` checks |
| `trader/watchlist.json` | from nse-scanner daily: price band and previous close, for circuit limits |
| `trader/config.json` | `no_entries_after` (default 14:30 IST) |
| `data/nse_holidays.json` | refuses any day it cannot confirm is a normal session |
| `trader/entry.json` | written on a trigger: symbol, session, range high/low, level, trigger bar and time, entry price (the trigger close), tick, circuit limits, volume ratio. nse-exit reads it |

## The rule

- **Opening range**: the 09:15, 09:20 and 09:25 bars (09:15-09:30 IST). High = highest high,
  low = lowest low, rounded to the stock's tick. Before 09:30 it says **forming**.
- **Level**: the range high (`--setup orb`, default) or your price (`--setup level --level X`),
  like a 20-day high from the daily scan.
- **Trigger**: the first completed 5-minute bar from 09:30 whose close is above the level.
  Its time is the bar's end (the 09:35 bar triggers at 09:40). Only completed bars count; live,
  a bar counts 20 seconds after its end so Yahoo has sent its last trades.
- **Volume** (optional, `--min-volume-x K`): the trigger bar's volume is at least K times the
  average of the day's earlier bars, leaving out the 09:15 bar (Yahoo reports 0 there).
- **Time limit**: a close above at or after `no_entries_after` is not an entry: **expired**.
- **Circuit**: if the day so far touched the stock's circuit limit (from the band and the
  previous close in the daily watchlist), it says **circuit** and skip it.
- **Unusable**: a missing or empty bar before now. If only the newest bar is missing it says
  Yahoo is late and judges on the bars before it.
- After a trigger it also says where the price is now, and if the last close is back under
  the level ("the breakout has failed so far").

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after `/nse-scanner orb` and let
   the user pick one. Never pick for them.
2. `entry.py check SYMBOL`. Tell the user the status in one line (and the distance when
   waiting).
3. To wait for it, run `entry.py watch SYMBOL --until HH:MM` in the background; it checks
   after each 5-minute bar and stops on a trigger, an expiry, or the time.
4. When it triggers, say the time and the price, and that nothing was placed.
5. Hand off to `/nse-exit` to set the stop and the target BEFORE anything else.

## Example (real data, Thu 5 Nov 2026)

```
entry.py check INFY --offline wall-5m-5d-2026-11-05.json.gz --now "2026-11-05 09:38"
INFY WAITING: Waiting: last close 1,151.20 at 09:35 is 0.30% below the level 1,154.70.

entry.py check INFY ... --now "2026-11-05 10:15"
INFY TRIGGERED: Triggered at 09:40 IST: the 09:35 bar closed at Rs. 1,160.50, above 1,154.70 (+0.50%).
Now 1,164.20 at 10:15 (+0.32% since).
```

## Rules

- Long side only in v1. If the user asks for a short, say v1 does not plan shorts.
- One trigger per setup per day: the first close above. Later closes do not reset it.
- Never call an entry early because a bar "looks like" it will close above.
- Yahoo bars are free, unofficial and can be late. Say the time of the last bar.

## What it never does

It never places, sizes or checks an order, never says a stock is a buy, never calls the data
real time, never uses New York time, never trades a holiday, weekend or special session, and
never promises that a breakout will continue.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Most retail
intraday traders lose money; an entry signal does not change that.
