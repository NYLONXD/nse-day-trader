# Task list

One phase at a time. Each phase: build, run tests, show results, commit, wait for "next".

- [x] **Phase 0 — Feasibility.** Real 5-minute bars for 5 liquid .NS stocks from Yahoo; findings in `docs/phase0-feasibility.md`; fixtures in `tests/fixtures/phase0/`.
  - [ ] Open item: measure live delay during market hours (`python tools/probe_yahoo.py --no-save` between 09:30 and 15:00 IST).
- [x] **Phase 1 — shared/market.py + tests.** IST, session, holidays file, special sessions, per-stock tick (tick file > inferred > 0.05), symbols, Rs. formatting.
  - [ ] **You:** fill `data/nse_holidays.json` from NSE's circular (until then every date is refused).
  - [ ] **You (optional):** confirmed ticks in `data/nse_tick_sizes.json`.
- [x] **Phase 2 — nse-scanner.** universe (EQUITY_L.csv / Nifty 500 / local CSV, EQ only, GSM out, price bands), daily (min price, median traded value, circuit skip, next-session limits, setup tags), orb (09:15-09:30 range, first close above, circuit-touch skip). Shared: config.py, yahoo.py, nse_files.py; circuit rules in market.py.
- [x] **Phase 3 — nse-entry and nse-exit.** Shared ORB rule (shared/orb.py) used by scanner and entry; entry: check/watch/watchlist, forming/waiting/triggered/expired/unusable/circuit/closed, optional volume filter (skips 09:15), Yahoo-lag handling; exit: or-mid/or-low/pct/price/atr stops, r/pct/price targets, tick rounding, circuit-aware target, flat_by. Daily tick inference now uses recent bars only.
- [x] **Phase 4 — nse-timemachine.** 10,000 replays (seed 915) + history re-run, net of costs and slippage; shared/costs.py; stop/target rules moved to shared/orb.py; unbiasedness test on a mirrored real pool.
  - [ ] **You:** replace the placeholder cost rates in `trader/config.json` with your broker's (pricing page + a real contract note).
- [x] **Phase 5 — nse-budget and nse-risk.** Sizing net of costs and slippage (shared/costs.py, also used by the Time Machine), no leverage; 25-line risk gate with fingerprint; shared/journal.py (today's entries, fresh prices).
  - [ ] **You:** put your own capital and limits in `trader/config.json` (`python shared/config.py init`).
- [ ] **Phase 6 — nse-alerts and nse-broker** (ticket only, never calls a broker).
- [ ] **Phase 7 — README, plugin manifests, full test run.**
