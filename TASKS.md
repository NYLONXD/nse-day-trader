# Task list

One phase at a time. Each phase: build, run tests, show results, commit, wait for "next".

- [x] **Phase 0 — Feasibility.** Real 5-minute bars for 5 liquid .NS stocks from Yahoo; findings in `docs/phase0-feasibility.md`; fixtures in `tests/fixtures/phase0/`.
  - [ ] Open item: measure live delay during market hours (`python tools/probe_yahoo.py --no-save` between 09:30 and 15:00 IST).
- [ ] **Phase 1 — shared/market.py + tests.** IST, session, holidays file (`data/nse_holidays.json`, you fill it), special sessions, tick rounding, symbols, Rs. formatting.
- [ ] **Phase 2 — nse-scanner.** universe (EQUITY_L.csv / Nifty 500 / local CSV), daily, orb.
- [ ] **Phase 3 — nse-entry and nse-exit.**
- [ ] **Phase 4 — nse-timemachine.** 10,000 replays + history re-run, net of costs, fixed seed.
- [ ] **Phase 5 — nse-budget and nse-risk.**
- [ ] **Phase 6 — nse-alerts and nse-broker** (ticket only, never calls a broker).
- [ ] **Phase 7 — README, plugin manifests, full test run.**
