# Phase 0 — Is Yahoo usable for NSE intraday?

Fetched 2026-11-05 at 17:16 IST (after the close) with `tools/probe_yahoo.py`:
`/v8/finance/chart/<SYMBOL>.NS?range=60d&interval=5m` for RELIANCE, TCS, INFY, HDFCBANK, SBIN.
All five answered HTTP 200. Raw answers are saved in `tests/fixtures/phase0/`.

**Verdict: usable for history and the offline tests. Live use depends on one thing I could not
measure yet: how delayed the newest bar is during the session.**

| question | answer |
| --- | --- |
| Bars start at 09:15? | Yes. Every one of 60 days: 75 bars, 09:15 to 15:25 IST, all on the 5-minute grid. |
| Time zone | Yahoo says `Asia/Kolkata`, gmtoffset 19800 (+05:30). Exchange code `NSI`, currency INR. |
| How far back? | `range=60d` gives **60 trading days** (2026-08-11 to 2026-11-05). Explicit `period1/period2` windows must sit within the last **60 calendar days** ("The requested range must be within the last 60 days"). Use `range=60d`. |
| Delay of latest bar vs now | **Measured live on 2026-11-06 at 09:36 IST: near real time.** `regularMarketTime` was 09:36:13-09:36:20 when fetched at about 09:36:25 (`latest_bar_age_min` -0.1 to -0.2); the 5-minute bars to 09:30 were complete. During the session Yahoo also sends one off-grid "live tick" bar at the latest trade time; the tools count only bars older than `SETTLE` (20 s) past their end, so that is enough. |
| Missing bars | No missing slots. Null bars (prices = null): the closing minutes, 15:20 every day and often 15:15/15:25, plus 11:45 and 11:50 on 2026-10-15 in all five stocks (a market-wide gap). |
| Volume present? | Yes, except the **09:15 bar has volume 0 on 295 of 300 stock-days** (prices are real). The 15:15/15:20 bars often have 0 too. Summed 5-minute volume is 83-97 % of Yahoo's daily volume. |
| Closed weekdays | 2026-09-14, 2026-10-02, 2026-10-20 had no bars in any stock. Check them against NSE's holiday circular when you fill `data/nse_holidays.json`. |
| Tick | Every price is on the 0.05 grid once float noise (1312.300048828125) is rounded. **But (found in Phase 1) the tick is per stock:** RELIANCE and TCS only ever trade in 0.10 steps; INFY, SBIN and HDFCBANK in 0.05 (INFY and SBIN even above Rs. 1,000). So `market.py` uses a tick file you fill, else the tick inferred from recent prices, else 0.05. |

## What this changes in the design

1. **Opening range (09:15-09:30) = the 09:15, 09:20, 09:25 bars.** Use their highs and lows. Do not
   use opening-range *volume*: the 09:15 bar's volume is almost always 0.
2. **Traded-value filter uses daily (1d) bars**, not summed 5-minute volume.
3. **Ignore bars at 15:15 and later.** They are null or distorted around NSE's closing-price
   calculation. Default `flat_by` 15:10 means the last bar we need is the 15:05 bar. Good.
4. **A null bar before `flat_by` makes the day unusable** for the scanner and the Time Machine
   (Oct 15 here), and the tools say so instead of guessing a price.
5. **Round every Yahoo price to the 0.05 tick** before using it.
6. Time Machine history is at most ~60 trading days of 5-minute bars.
