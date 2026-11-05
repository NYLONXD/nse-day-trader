---
name: nse-scanner
description: >-
  NSE Chart Scanner for Indian intraday trading. Builds the NSE universe (Nifty 500 or
  every EQ stock in EQUITY_L.csv, or a CSV you saved), runs a daily scan that keeps
  liquid stocks (minimum price, median traded value in rupees) and skips stocks locked
  at their circuit limit, then an intraday opening-range-breakout scan on 5-minute
  bars (range 09:15-09:30 IST). Writes trader/watchlist.json and trader/orb_hits.json
  for nse-entry. Free Yahoo .NS data. Use when the user says "scan NSE", "scan the
  Indian market", "find me setups", "opening range breakouts", "ORB scan", "what is
  breaking out on NSE", "build my watchlist", "which Nifty stocks broke out", "pre-market
  scan", or at the start of an Indian trading day.
argument-hint: "[universe | daily | orb] [--at HH:MM] [--symbols A,B]"
---

# nse-scanner

The first step of an NSE intraday day. It reads free Yahoo charts of NSE stocks and lists
the ones that match a rule. A scan is a list of charts that match a rule, not a list of
stocks to buy, and a setup is not a prediction.

`${CLAUDE_SKILL_DIR}` is this skill's folder. All market rules (IST, session, holidays,
ticks, circuit limits) come from `${CLAUDE_SKILL_DIR}/../shared/market.py`; settings come
from `trader/config.json` (`python "${CLAUDE_SKILL_DIR}/../shared/config.py" init` writes
one with the defaults).

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe                       # Nifty 500 -> trader/universe.json
python "${CLAUDE_SKILL_DIR}/scan.py" universe --source equity       # every EQ stock in EQUITY_L.csv
python "${CLAUDE_SKILL_DIR}/scan.py" universe --csv ~/Downloads/EQUITY_L.csv --bands ~/Downloads/sec_list.csv
python "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before 09:15 or after 16:00 -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M
python "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 09:35 IST -> trader/orb_hits.json
python "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 IST
```

Flags: `--now "2026-11-05 10:15"` (IST) pins the clock, `--offline PATH` reads saved Yahoo
answers (needs `--now`), `--limit N`, `--setups-only`, `--max-confirm 200`, `--no-write`,
`--json`, `--show N`. Exit code 0 when the scan found something, 1 when it found nothing or
refused (the reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.json` | written by `universe`: `symbols[]` with `symbol` (SYMBOL.NS), `name`, `band` |
| `trader/watchlist.json` | written by `daily`: `last_session`, filters, skipped counts, `hits[]` (close, median traded value, band, tick, setup tags, `next_session_limits`) |
| `trader/orb_hits.json` | written by `orb`: `session`, `cutoff`, counts, `hits[]` (hit time, range high/low, close, % over). nse-entry reads it |
| `trader/config.json` | `min_price_inr`, `min_traded_value_inr`, `no_entries_after`, `flat_by` |
| `data/nse_holidays.json` | you fill it from NSE's circular. `orb` refuses any day it cannot confirm |

## The universe

`universe` downloads NSE's list with an honest User-Agent. NSE's site may refuse scripts:
then download the file in your browser (the message gives the URL) and pass `--csv PATH`.
It accepts `EQUITY_L.csv`, an index list such as `ind_nifty500list.csv`, or a plain file
with one symbol a line. It keeps:

1. Series **EQ** only. BE and BZ are trade-for-trade (delivery only, no intraday).
2. Valid NSE symbols, written as Yahoo's `SYMBOL.NS`. NSE's own `-` and `&` are kept
   (`BAJAJ-AUTO.NS`, `M&M.NS`); a `.` is never turned into `-` (a US-only rule).
3. Not under GSM surveillance (the `Remarks` column of `sec_list.csv`).

It also stores each stock's price band from NSE's `sec_list.csv` (2, 5, 10, 20 % or "No
Band" for F&O stocks). Without the band file the circuit check falls back to a guess.
On the 2026-11-05 files: EQUITY_L 2,630 rows -> 2,365 kept (204 BE, 33 BZ, 28 GSM out);
Nifty 500 501 rows -> 496 kept.

## The daily scan

Run before 09:15 or after 16:00 IST (a session's daily bar counts only from 16:00, after
NSE's official close). Two stages: Yahoo's spark endpoint (closes, 20 symbols a request)
drops stocks under `min_price_inr`; then one daily chart each for the rest:

- **Traded value** = the median of close x volume over the last 20 sessions, at least
  `min_traded_value_inr` (default Rs. 10 cr). Median, so one block deal does not count as
  liquidity. Daily bars, because summed 5-minute volume misses 3-17 % (Phase 0).
- **Circuit**: a stock that closed within one tick of its band limit is skipped (you
  cannot buy at an upper circuit or sell at a lower one). "No Band" stocks have no fixed
  circuit. Band unknown: a close at the day's high (low) at a standard band's limit is
  treated as locked.
- **Next session's limits** (`next_session_limits`) for banded stocks, for the `orb` scan.
- **Setup tags** (information only): `breakout_20d`, `near_20d_high` (within 2 %),
  `reclaim_ma20`, `inside_day`, `oversold_rsi14`.

## The opening-range-breakout scan

The opening range is the 09:15, 09:20 and 09:25 bars (09:15-09:30 IST). Its high is the
highest high, its low the lowest low; opening-range **volume is never used** (Yahoo's
09:15 bar almost always says 0). A hit is the first completed 5-minute bar from 09:30 on
to **close** above the range high, at that bar's end time. Entries come only after the
range completes, so the earliest hit is 09:35.

1. Refuses on a weekend, an NSE holiday, a special session (Muhurat, budget-day weekend),
   or any year `data/nse_holidays.json` does not cover. Before 09:35 it says too early;
   after `no_entries_after` it says the setup has expired.
2. Stage 1, every symbol: spark closes. A chart goes on only if a bar after the range
   closed at or above the highest range close (necessary for a breakout, so none is lost).
3. Stage 2, each candidate (at most `--max-confirm`, most traded first; PARTIAL says how
   many were not checked): the real chart. A chart is usable only when every bar from
   09:15 to the cutoff is there with prices. Prices are rounded to the stock's tick.
4. Skips a stock whose high or low today touched its circuit limit.

## Steps

1. Weekly: `scan.py universe`. If NSE refuses, ask the user to download the CSV.
2. Before the open (or after 16:00 the day before): `scan.py daily`. Tell the user the
   counts (fetched, price, traded value, circuit skips) and the top names.
3. After 09:35 IST (10:00-10:15 is common): `scan.py orb`. Tell the user how many usable
   charts broke out and when, and that a later hit is closer to the price now.
4. The user picks one chart. Hand it on: `/nse-entry SYMBOL`. Never pick for them.

## Example (real data, Thu 5 Nov 2026)

```
scan.py daily --offline wall-1d-3mo-2026-11-05.json.gz --now "2026-11-05 17:40"
Fetched 61 of 61 daily charts. Last finished session: 2026-11-05.
1 below Rs. 50; 6 below Rs. 10.00 cr median daily traded value; 4 skipped at a circuit limit.
  skip BIRLACABLE   closed at its upper circuit (5% band) on 2026-11-05
Watchlist: 50 stocks, most traded first (40 with a daily setup tag).

scan.py orb --offline wall-5m-5d-2026-11-05.json.gz --now "2026-11-05 10:15"
22 of 50 usable charts closed above their opening-range high by 10:15 IST on 2026-11-05:
  09:40  INFY           Rs. 1,160.50  range 1,144.90 to 1,154.70  +0.50% over  (now 1,164.20 at 10:15)
```

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many were usable,
  and the time of the data (IST). Partial is said as partial.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- Yahoo data is free, unofficial and can be delayed. A hit is stale once the price moves on.

## What it never does

It never places, sizes or checks an order, never says which stock to buy, never calls the
data real time, never uses New York time, never guesses a holiday, never disguises its
User-Agent to get past NSE's site, and never promises that a setup works.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Most retail
intraday traders lose money; a scanner does not change that.
