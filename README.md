# nse-day-trader

Eight Claude skills for **paper** intraday trading on India's **NSE**: find opening-range
breakouts, time the entry, plan the exit, replay the plan 10,000 times **net of costs**, size it
to your rupees, check it against your rules, ring alerts, and build a ticket **you place by hand**
in your own broker app, or skip.

- **Long only.** No shorting, options, futures or leverage in v1.
- **IST, INR, NSE rules.** Times are IST (UTC+05:30, no daylight saving), prices sit on each
  stock's NSE tick, money is in rupees with Indian digit grouping (Rs. 12,34,567.50), and
  symbols are Yahoo style (`RELIANCE.NS`).
- **Python 3.8+, standard library only.** Nothing to pip install. Free Yahoo data and NSE's
  public files.
- **Paper only.** Nothing here connects to a broker, asks for an API key or password, or places
  an order. Every stop is a plan, not a guarantee.

> **Most retail intraday traders lose money.** SEBI's own study of FY2022-23 found that about 7 in
> 10 individual intraday traders in the equity cash segment made a net loss. These tools help you
> follow rules and see the honest odds after costs; they do not change those odds.

## The eight skills

| skill | job | writes |
| --- | --- | --- |
| `nse-scanner` | The NSE universe (Nifty 500 or EQUITY_L.csv, or your CSV), a daily liquidity watchlist that skips stocks at their circuit limit, and the 09:15-09:30 opening-range-breakout scan | `trader/universe.json`, `watchlist.json`, `orb_hits.json` |
| `nse-entry` | Watches one stock bar by bar: forming, waiting, triggered (time and price), expired | `trader/entry.json` |
| `nse-exit` | Stop, target and flat-by time before the entry, on the stock's tick, circuit-aware | `trader/plan.json` |
| `nse-timemachine` | 10,000 replays from the stock's own last 60 days + the same rule on every past day, net of costs, fixed seed | `trader/timemachine.json` |
| `nse-budget` | Shares sized to your max loss **after costs**, capped by position, order size and your own cash (no leverage) | `trader/order.json` |
| `nse-risk` | 25 checks: calendar, entry window, ticks, circuit, max loss, net reward:risk, daily loss, trades a day, cash. PASS or BLOCK, with a fingerprint | `trader/state/risk.json` |
| `nse-alerts` | Quotes, price and time alerts, the plan's stop and target (cancel each other), the NSE bells | `trader/alerts.json`, `prices.json` |
| `nse-broker` | A ticket for an order that PASSED, to type into your broker app yourself; the paper journal of your fills | `trader/ticket.txt`, `state/journal-*.json` |

Each skill reads what the one before wrote, through JSON files in a `trader/` folder in the folder
where you run Claude. The order of a day: scanner, entry, exit, timemachine, budget, risk, broker,
alerts.

## Install

You need [Claude Code](https://claude.com/claude-code) and Python 3.8 or newer.

**As a plugin, from GitHub** (after you push this repo; replace `YOUR-GITHUB-USER`):

```
/plugin marketplace add YOUR-GITHUB-USER/nse-day-trader
/plugin install nse-day-trader@nse-day-trader
```

**As a plugin, from this folder** (works now):

```
/plugin marketplace add D:/Professional_life/skills/nse-day-trader
/plugin install nse-day-trader@nse-day-trader
```

Claude Code namespaces plugin skills: they show up as `/nse-day-trader:nse-scanner` and so on, and
Claude also picks them by their descriptions when you say things like "scan NSE" or "size my
position".

**As plain skills** (short names like `/nse-scanner`): copy the eight `nse-*` folders **and** the
`shared/` and `data/` folders side by side into `~/.claude/skills/` (or a project's
`.claude/skills/`). Every skill imports `../shared`, so they must stay siblings.

When you push to GitHub, add your repo to `.claude-plugin/plugin.json` (and to the plugin entry in
`marketplace.json`):

```json
"homepage": "https://github.com/YOUR-GITHUB-USER/nse-day-trader",
"repository": "https://github.com/YOUR-GITHUB-USER/nse-day-trader",
```

## First-time setup (once)

Ask Claude "set my budget" (nse-budget walks you through it), or run these in your project folder
(`<plugin>` is this folder, or the plugin's folder Claude Code installed):

1. **Your rules**: `python <plugin>/shared/config.py init` writes `trader/config.json` with the
   defaults. Change them to your own: `capital_inr` (only money you can afford to lose),
   `max_loss_per_trade_inr`, `daily_loss_limit_inr`, `max_trades_per_day`, `min_reward_risk`,
   `flat_by` (15:10), `no_entries_after` (14:30), `min_price_inr`, `min_traded_value_inr`.
   `python <plugin>/shared/config.py show` checks them.
2. **NSE holidays**: every date is refused until you fill the list from NSE's official circular
   ("Trading Holidays for the calendar year YYYY", nseindia.com > Resources > Exchange
   Communication > Holidays). Working from the cloned repo, fill `data/nse_holidays.json`. Installed
   as a plugin, run `python <plugin>/shared/config.py lists` and fill `trader/nse_holidays.json`
   instead (it wins over the plugin's copy and survives plugin updates). Add special sessions
   (Muhurat, a budget-day weekend session) too: they are refused. The steps are inside the file.
3. **Your broker's costs**: the `costs` in `trader/config.json` are PLACEHOLDERS (0.03 % or Rs. 20
   brokerage an order, STT 0.025 % on the sell, exchange 0.00297 %, SEBI 0.0001 %, stamp 0.003 % on
   the buy, GST 18 %, 1 tick slippage). Put in your broker's numbers from its pricing page or
   brokerage calculator, and check them against a real contract note, which lists every charge.
   Every result says how many rates are still placeholders.
4. **Optional ticks**: each stock's tick is inferred from its recent prices (0.01 to 5.00).
   `trader/nse_tick_sizes.json` (or `data/`) overrides it with values you confirmed.
5. **Once, during market hours**: `python tools/probe_yahoo.py --no-save` measures how late
   Yahoo's latest 5-minute bar is (Phase 0 could not, the market was closed).

## An example day (real data, Thu 5 Nov 2026)

`python tools/example_day.py` runs all eight skills offline on real saved Yahoo data of that day,
with a TEST holiday calendar and simulated fills. The highlights:

```
$ scan.py daily --offline tests/fixtures/nse-scanner/wall-1d-3mo-2026-11-05.json.gz --now "2026-11-05 09:00"
Fetched 61 of 61 daily charts. Last finished session: 2026-11-04.
Watchlist: 54 stocks, most traded first (41 with a daily setup tag).

$ scan.py orb ... --now "2026-11-05 10:15"
  skip AUGMONT      touched its lower circuit (5% band) today
22 of 54 usable charts closed above their opening-range high by 10:15 IST on 2026-11-05:
  09:40  INFY           Rs. 1,160.50  range 1,144.90 to 1,154.70  +0.50% over  (now 1,164.20 at 10:15)

$ entry.py check INFY ...
INFY TRIGGERED: Triggered at 09:40 IST: the 09:35 bar closed at Rs. 1,160.50, above 1,154.70 (+0.50%).

$ exit.py plan --write
  stop      Rs. 1,149.80  or-mid: opening-range midpoint 1,149.80, rounded down to the 0.05 tick
  target    Rs. 1,181.90  r:2: 2 x the risk a share
  Note: No fixed price band (an F&O stock): no circuit limit applies to the target.

$ timemachine.py run --write
Net of costs and 1 tick(s) slippage: a target hit is +1.88R, a stop -1.12R.
10000 replays (seed 915, +-2 bars):
  target first   5.2%   stop first  52.3%   closed at flat_by  42.5%   net positive  29.9%
Verdict: Strongly toward the stop: of the replays that hit a side, 9% hit the target first;
break-even after costs needs 37%.
History, the same rule on 58 past sessions: triggered on 19. Target 1, stop 11, flat_by 7.

$ size.py --write
  max loss       41 shares   <- decides it      (position 43, max order 43, cash 77)
  At the stop 1,149.80: lose Rs. 492.98, of which Rs. 50.18 is charges and Rs. 4.10 slippage.
  At the target 1,181.90: make Rs. 824.33 after Rs. 51.02 of costs. Net reward:risk 1.67.

$ check.py
  PASS  max loss          Rs. 492.98 at the stop with costs and slippage, limit Rs. 500.00
  WARN  time machine      the replays lean against this plan: Strongly toward the stop (average -0.41R net)
PASS: every rule holds (fingerprint 91afb06471e6a085).

$ ticket.py ticket
  1. BUY 41 INFY at MARKET, or a LIMIT buy at Rs. 1,160.65 so you never pay more than your max loss allows.
  2. Right after it fills, the stop-loss: SELL 41 INFY, order type SL-M, trigger Rs. 1,149.80.
  3. The target, Rs. 1,181.90, ONE of: (a) an OCO / GTT-OCO order ... or (b) no order: /nse-alerts rings ...
    Never leave a stop-loss SELL and a target SELL resting for the same shares: ... which is a short.
  4. By 15:10 IST, if still open: cancel the stop-loss, then SELL 41 INFY at MARKET.

$ alerts.py watch
RING [15:13] 15:10 IST (now 15:13): FLAT BY: sell anything still open, at market

$ ticket.py closed INFY --price 1164.90 --reason flat_by
Recorded: SELL 41 INFY at 1,164.90 (flat_by). Net Rs. 125.73 after Rs. 50.57 of costs.
```

A PASS from nse-risk means the rules hold, not that the trade is good: the Time Machine leaned
against this plan, and on its 19 past triggers the same rule lost 10.8R after costs. This day
happened to end up Rs. 125.73.

## What the data taught us (Phase 0 onwards)

- Yahoo's `.NS` 5-minute bars run 09:15 to 15:25 IST, 75 a day, about **60 trading days** back
  (`range=60d`; explicit dates must be within 60 calendar days).
- The **09:15 bar's volume is almost always 0**, so opening-range volume is never used; liquidity
  comes from daily bars (summed 5-minute volume misses 3-17 %).
- Bars from **15:15** are often empty (NSE's closing-price window): `flat_by` must be 15:15 or
  earlier. A day with an empty bar before flat_by is unusable (15 Oct 2026 had a market-wide gap).
- **Ticks differ by stock**: RELIANCE and TCS trade in 0.10 steps, most stocks in 0.05, low-priced
  ones in 0.01, and a stock's tick changes when its price moves into another band.
- **Circuit limits** computed from the band and the previous close matched, to the paisa, the
  prices where six real stocks locked on 5 Nov 2026.
- **Costs** took about 0.12R of a Rs. 500-risk INFY trade: sizing on price alone would have bought
  46 shares instead of 41 and lost about Rs. 553 at the stop.

## Project layout

```
nse-day-trader/
  .claude-plugin/        plugin.json, marketplace.json
  nse-scanner/ ...       eight skills: SKILL.md + one script each
  shared/                market.py (ALL market rules), orb.py (breakout, stop and target rules),
                         costs.py, config.py, journal.py, yahoo.py, nse_files.py
  data/                  nse_holidays.json and nse_tick_sizes.json, for you to fill
  tests/                 offline tests on real saved Yahoo and NSE data (tests/fixtures/)
  tools/                 probe_yahoo.py, snapshot.py (save new fixtures), example_day.py
  docs/                  phase0-feasibility.md
```

## Tests

```
python -m unittest discover -s tests
```

All offline, on real data saved in `tests/fixtures/`. Besides each skill, they check that no skill
script hardcodes a market rule (09:15, 0.05, `.NS`, the IST offset, New York time), that every
SKILL.md has its frontmatter and its "What it never does", that nothing uses Python 3.9+ features,
that the replay engine is unbiased (a mirrored real pool comes out even), and that nse-broker and
everything it imports has no network code and reads no secret.

## How to extend

- **A new market rule** (a new session time, a tick band, a holiday kind) goes in
  `shared/market.py` only, with a test. The repo test fails if a skill script hardcodes one.
- **A new setup** (say, a VWAP reclaim) goes in `shared/orb.py`, so the scanner, nse-entry and the
  Time Machine all call it the same way; then add a `--setup` choice where it is used.
- **A new skill**: a folder `nse-<name>/` with a `SKILL.md` (frontmatter `name`, a `description`
  with "Use when ..." trigger phrases, `argument-hint`; sections Steps, What it never does, Safety;
  it mentions `${CLAUDE_SKILL_DIR}/../shared`) and **one** script that starts with
  `sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))` and `import market`. Save real data
  for its tests with `python tools/snapshot.py charts --symbols A,B --range 5d --interval 5m --out
  tests/fixtures/<name>/....json.gz`, and add `tests/test_<name>.py`. `tests/test_repo.py` checks the
  shape.
- **Every year**: the new holiday list from NSE's circular, and your broker's current costs.
- **Not in v1, on purpose**: shorts, options, futures, leverage, and any broker API. Adding any of
  them changes the safety story; the risk gate and the ticket would need new rules first.

## Credits

The design (one folder per skill, a SKILL.md plus a Python script, JSON hand-offs in `trader/`,
offline tests on saved real data, the risk and broker gates) follows Jake Schincariol's US-market
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT). The code here was
written fresh for NSE; none of it is copied.

## License

MIT, (c) 2026 Himanshu Jha. See `LICENSE`. Education and tooling, not financial advice.
