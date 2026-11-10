# Task list

One phase at a time. Each phase: build, run tests, show results, commit, wait for "next".

- [x] **Phase 0 — Feasibility.** Real 5-minute bars for 5 liquid .NS stocks from Yahoo; findings in `docs/phase0-feasibility.md`; fixtures in `tests/fixtures/phase0/`.
  - [x] Open item: live delay measured 2026-11-06 09:36 IST: near real time (latest tick < 15 s old); `SETTLE` = 20 s kept.
- [x] **Phase 1 — shared/market.py + tests.** IST, session, holidays file, special sessions, per-stock tick (tick file > inferred > 0.05), symbols, Rs. formatting.
  - [ ] **You:** fill `data/nse_holidays.json` from NSE's circular (until then every date is refused).
  - [ ] **You (optional):** confirmed ticks in `data/nse_tick_sizes.json`.
- [x] **Phase 2 — nse-scanner.** universe (EQUITY_L.csv / Nifty 500 / local CSV, EQ only, GSM out, price bands), daily (min price, median traded value, circuit skip, next-session limits, setup tags), orb (09:15-09:30 range, first close above, circuit-touch skip). Shared: config.py, yahoo.py, nse_files.py; circuit rules in market.py.
- [x] **Phase 3 — nse-entry and nse-exit.** Shared ORB rule (shared/orb.py) used by scanner and entry; entry: check/watch/watchlist, forming/waiting/triggered/expired/unusable/circuit/closed, optional volume filter (skips 09:15), Yahoo-lag handling; exit: or-mid/or-low/pct/price/atr stops, r/pct/price targets, tick rounding, circuit-aware target, flat_by. Daily tick inference now uses recent bars only.
- [x] **Phase 4 — nse-timemachine.** 10,000 replays (seed 915) + history re-run, net of costs and slippage; shared/costs.py; stop/target rules moved to shared/orb.py; unbiasedness test on a mirrored real pool.
  - [ ] **You:** replace the placeholder cost rates in `trader/config.json` with your broker's (pricing page + a real contract note).
- [x] **Phase 5 — nse-budget and nse-risk.** Sizing net of costs and slippage (shared/costs.py, also used by the Time Machine), no leverage; 25-line risk gate with fingerprint; shared/journal.py (today's entries, fresh prices).
  - [ ] **You:** put your own capital and limits in `trader/config.json` (`python shared/config.py init`).
- [x] **Phase 6 — nse-alerts and nse-broker.** Alerts: quote -> prices.json, price/time alerts on completed bars, plan stop/target pair (OCO-style), bells. Broker: ticket only behind the risk fingerprint (today, <=15 min, in window), SL-M/SL and OCO-or-alert target with the no-two-resting-sells warning, capped limit, paper journal (placed/closed/flatten/status). Code-level test: no network, no secrets.
- [x] **Phase 7 — README, plugin manifests, full test run.** README (install, first-time setup, real example day, data lessons, how to extend, credits), MIT LICENSE, .claude-plugin/plugin.json + marketplace.json (`claude plugin validate .` passes), tools/example_day.py (all eight skills end to end), trader/ copies of the holiday and tick lists win over the plugin's, 196 tests pass.
- [x] **Phase 8 — nse-news.** What is new about your stocks: NSE's announcement, board-meeting and corporate-action RSS (matched by company name, the filed document's name or the symbol in the text), Yahoo headline RSS, the move against the previous close; shown once (30 days), tags, `!` for results/risk/halt/people and events today or tomorrow; `tools/snapshot.py news`; real fixtures of 10 Nov 2026 09:18 IST. Plugin 0.2.0.
- [ ] **Phase 9 — nse-analyst.** A company's quarterly results, quarter against the same quarter a year earlier. Data source to be proven first (as in Phase 0).
- [ ] **Phase 10 — India's version of the Pelosi tracker.** Bulk and block deals / insider-trading disclosures. Data source to be proven first.

## Your to-do list (none of it is code)
- [ ] Fill the NSE holiday list for 2026 (and 2027 when NSE publishes it): `data/nse_holidays.json`, or `trader/nse_holidays.json` for a plugin install (`python shared/config.py lists`).
- [ ] `python shared/config.py init`, then your own capital and limits in `trader/config.json`.
- [ ] Your broker's real cost rates in `trader/config.json` -> `costs` (check them against a contract note).
- [x] Once during market hours: `python tools/probe_yahoo.py --no-save` to measure Yahoo's delay (done 2026-11-06: near real time).
- [ ] After you create the GitHub repo and push: add `homepage` and `repository` to `.claude-plugin/plugin.json` (README, Install section).
