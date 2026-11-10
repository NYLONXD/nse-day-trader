# Your first live day (paper)

A checklist for running nse-day-trader on the real NSE market, with real live Yahoo data and
**paper** trades. Times are IST.

**Paper first.** For your first two weeks, do not place real orders. When a ticket comes out, write
down the price your broker app shows at that moment and record it as a paper fill. You learn the
whole flow, and the journal shows you, net of costs, whether the rules make or lose money for you.
Most retail intraday traders lose money; two weeks of paper costs nothing.

Run everything from the folder you cloned the repo into (open PowerShell there). Your `trader/`
folder is created inside it and git ignores it, so your trades never go to GitHub.

```
git clone https://github.com/NYLONXD/nse-day-trader.git
cd nse-day-trader
```

## The evening before (after 16:00)

1. **Your rules.** `python shared/config.py init`, then open `trader/config.json` in Notepad and set
   your own numbers. Start small: a `max_loss_per_trade_inr` you would not mind losing three times
   in a row. Check them: `python shared/config.py show`.
2. **The holiday list** (the tools refuse every day until this is done). Open
   `data/nse_holidays.json`. From NSE's circular "Trading Holidays for the calendar year 2026"
   (nseindia.com > Resources > Exchange Communication > Holidays, Equities segment), copy every
   holiday as `{"date": "2026-MM-DD", "name": "..."}`, add any special session (Muhurat trading) to
   `special_sessions`, put the circular's number and date in `"source": {"2026": "..."}`, and set
   `"years_covered": [2026]`. You can paste the circular's table to Claude and ask it to format the
   JSON; check every date against the circular yourself. Then:
   `python shared/config.py calendar` must say OPEN for tomorrow, or tell you why not.
3. **Your broker's costs** in `trader/config.json` under `"costs"` (from its pricing page or
   brokerage calculator; a contract note shows every charge). Until then the results say
   "PLACEHOLDERS".
4. **The universe**, once a week: `python nse-scanner/scan.py universe` (Nifty 500 from NSE; about
   5 seconds). If NSE refuses, download `ind_nifty500list.csv` and `sec_list.csv` in your browser
   and run `python nse-scanner/scan.py universe --csv ind_nifty500list.csv --bands sec_list.csv`.
5. **Tomorrow's watchlist**: `python nse-scanner/scan.py daily --setups-only` (about 6 minutes for
   the Nifty 500; it keeps liquid stocks with a daily setup tag, and skips circuit-locked ones).

## The day (IST)

| time | what | command |
| --- | --- | --- |
| before 09:15 | Check the day and set the bells | `python shared/config.py calendar` then `python nse-alerts/alerts.py bells` |
| before 09:15 | News on the watchlist: results today, ex-dates, what companies filed overnight | `python nse-news/news.py` |
| 09:15-09:30 | The opening range is forming. Do nothing. | (entry checks say FORMING) |
| about 09:40 | Measure how late Yahoo is, once. Note the "latest_bar_age_min" | `python tools/probe_yahoo.py --no-save` |
| 09:45-10:30 | Scan for breakouts (about a minute) | `python nse-scanner/scan.py orb --max-confirm 60` |
| right after | Pick **one** hit: early, small "% over", a name you know. Read its news first (a `!` line: open the link) | `python nse-news/news.py SYMBOL --all` |
| | Check it | `python nse-entry/entry.py check SYMBOL` |
| | Plan the exit before anything else | `python nse-exit/exit.py plan --write` |
| | Replay it (about a minute: it downloads 60 days) | `python nse-timemachine/timemachine.py run --write` |
| | A fresh price, then size it | `python nse-alerts/alerts.py quote SYMBOL --write` then `python nse-budget/size.py --write` |
| | The gate | `python nse-risk/check.py` |
| if PASS and you choose to | The ticket. **Paper**: do not place it; note your broker app's price now | `python nse-broker/ticket.py ticket` then `python nse-broker/ticket.py placed --price PRICE` |
| | Alerts for the stop, target, 14:30 and 15:10; leave this window running | `python nse-alerts/alerts.py from-plan` then `python nse-alerts/alerts.py watch --every 60` |
| when it rings | Record the exit at your broker app's price | `python nse-broker/ticket.py closed SYMBOL --price PRICE --reason stop` (or `target`, `flat_by`) |
| 14:30 | No new entries (the tools refuse) | |
| 15:10 | Flat by now: everything closed | `python nse-broker/ticket.py flatten`, then `closed` for each |
| after 15:30 | Your day, net of costs | `python nse-broker/ticket.py status` |
| after 16:00 | Next session's watchlist | `python nse-scanner/scan.py daily --setups-only` |

If the price ran on after the trigger, the risk check can BLOCK on reward:risk (the stop is now
far below you). Re-plan from the price now: `python nse-exit/exit.py plan --entry PRICE --write`,
then replay, quote, size and check again. Or skip it: chasing a move is a fine thing to refuse.

The entry, risk and ticket steps take a few minutes in all. If more than 15 minutes pass between the
risk check and the ticket, the ticket refuses: run `check.py` again (prices move).

If a step says no, that is the tool doing its job: read the one line, it says why and the fix. A
Time Machine verdict "toward the stop" or a risk WARN is information; skipping the trade is a fine
answer.

## More than one stock in a day

Yes, but **one stock at a time through the chain**. `entry.json`, `plan.json`, `order.json` and the
ticket hold one stock each, so the next stock overwrites them. Take stock A all the way to
`placed` and `from-plan` before you start stock B.

1. Stock A: entry → exit → Time Machine → quote → size → risk → ticket → `placed` → `from-plan`.
   Start `watch` once and leave it running: it watches every stock's alerts.
2. Stock B: re-run `scan.py orb` (hits change), then the same chain. `from-plan` adds B's stop and
   target, and the running `watch` picks them up ("Now also watching").
3. Each exit: `ticket.py closed SYMBOL --price PRICE --reason stop|target|flat_by`.
   At 15:10: `ticket.py flatten`, then `closed` for each one still open.

The limits cover all your positions together. Each open trade's max loss counts against
`daily_loss_limit_inr`, all of them share your cash (no leverage), and you get at most
`max_trades_per_day` trades and one per symbol a day. With ₹500 a trade and ₹1,500 a day, that is
at most 3 open at once. Pick different sectors: three PSU banks breaking out together are one
bet, not three.

## With Claude instead of typing commands

Install the plugin once from inside the repo folder in Claude Code:

```
/plugin marketplace add ./
/plugin install nse-day-trader@nse-day-trader
```

Then talk to it in the same order: "is the market open today", "scan NSE for opening range
breakouts", "check INFY", "plan my exit and write it", "replay it", "size it", "risk check",
"make the ticket", "I bought at 1160.60", "set alerts from my plan", "I sold at 1164.90",
"how did I do today".

## First-day notes

- Yahoo's delay was measured on 2026-11-06 at 09:36 IST: near real time (latest tick under 15
  seconds old). The tools count a bar 20 seconds after it ends; if the probe ever says bars arrive
  later, raise `SETTLE` in `shared/yahoo.py`.
- A full daily scan takes about 6 minutes, an ORB confirm of 60 charts about a minute: scan the
  daily watchlist the evening before, not at 09:30. The ORB scan refuses without a watchlist
  ("no watchlist"). Forgot it? Run `daily --setups-only` at 09:30: it ignores today's unfinished
  daily bar, so you get the same list as the evening before and it is ready by about 09:40.
- Every stop is a plan, not a guarantee.
