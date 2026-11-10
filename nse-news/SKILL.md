---
name: nse-news
description: >-
  NSE News Monitor for Indian intraday trading. Shows what is new about the NSE stocks you
  watch: what each company told the exchange (NSE's announcement feed), its next board meeting
  (the day results are decided), ex-dates for dividends, bonuses and splits, Yahoo Finance
  headlines, and how far each stock is from its previous close. Each item shows once, newest
  first, in IST, with tags (results, dividend, capital, deal, people, legal, risk, halt) and a
  "!" on what a day trader must know before entering. It only reads and reports. Use when the
  user says "any news on INFY", "why is RBLBANK moving", "news on my watchlist", "morning
  briefing", "did anything break", "when are the results", "is there a board meeting", "any
  ex-dividend dates", "check the announcements", or after /nse-scanner finds setups.
argument-hint: "[SYMBOLS...] [--all] [--hours N] [--no-headlines]"
---

# nse-news

A chart can look perfect on the morning the company announces results, goes ex-dividend or loses
its CEO. Before a setup becomes a trade, this skill reads what the company told the exchange and
what the news wires say, for every stock on your list, and tells you only what is new.

`${CLAUDE_SKILL_DIR}` is this skill's folder. IST and symbols come from
`${CLAUDE_SKILL_DIR}/../shared/market.py`, the feeds from `${CLAUDE_SKILL_DIR}/../shared/nse_files.py`
and `yahoo.py`.

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                          # the plan, open positions, ORB hits, watchlist
python "${CLAUDE_SKILL_DIR}/news.py" INFY RBLBANK             # these stocks only
python "${CLAUDE_SKILL_DIR}/news.py" INFY --all --hours 96    # the last 4 days, seen before or not
python "${CLAUDE_SKILL_DIR}/news.py" --no-headlines           # NSE's feeds and the moves only
```

Flags: `--no-nse`, `--no-moves`, `--move-pct 3`, `--max-headlines 25`, `--json`, `--dry-run`
(remember nothing), `--offline DIR --now "YYYY-MM-DD HH:MM"` (replay a folder saved by
`tools/snapshot.py news`). Exit code 0 means something new, 1 means nothing new.

## How it works

- **NSE's feeds** are three public files for the whole market, so a 200-stock watchlist costs
  three requests: announcements (what companies filed), board meetings (with the meeting date) and
  corporate actions (with the ex-date). They carry the company's **name**, not its symbol, so an
  item is matched by name ("The Federal Bank Limited" = "Federal Bank Ltd."), by the symbol that
  starts the filed document's name, or by the symbol in the text. Names come from
  `trader/watchlist.json`, `universe.json` and Yahoo. A stock with no known name is named in a Note.
- **The feeds hold only the latest items** (on a busy day, a few hours). Run it before the open
  and again through the day; what you saw is remembered for 30 days and not repeated.
- **Dated events** (a board meeting, an ex-date) show from the day they are announced until the
  day itself, with "TODAY", "tomorrow" or "in N days". Other items show for `--hours` (48).
- **`!`** marks an NSE item tagged results, risk, halt or people, and any event today or tomorrow.
- **Yahoo headlines** are one request a stock (the first 25 by default: the plan, open positions
  and ORB hits come first). Yahoo carries little on NSE stocks and often nothing: the Note says so.
  A stock's feed also carries stories about its rivals; read the title.
- **Moves**: Yahoo's latest trade against the previous close. `MOVING` marks 3 % or more. On a
  holiday or before the open the price is the last session's, and it says "not today".

## What it reads and writes

| file / feed | what |
| --- | --- |
| `trader/plan.json`, `state/journal-*.json`, `orb_hits.json`, `watchlist.json` | your stocks, in that order, when you name none |
| `trader/universe.json`, `watchlist.json` | company names for matching |
| NSE RSS: `Online_announcements.xml`, `Board_Meetings.xml`, `Corporate_action.xml` | nsearchives.nseindia.com/content/RSS/ |
| Yahoo Finance headline RSS and spark | free, unofficial, can lag |
| `trader/news.json` | the brief just shown: stocks, moves, items, notes |
| `trader/state/news.json` | what has been shown (kept 30 days) |

## Steps

1. Run it: with no symbols after /nse-scanner, or with the one stock about to be traded.
2. Read what matters first: every `!` line, then anything tagged results, halt, legal or risk, then
   every MOVING stock. A move with no item is worth saying out loud too.
3. For each item that matters, open its link and read it (NSE's links are the filed PDF or XBRL
   file). Never tell the user something happened from the title alone; if a story could not be
   opened, say so.
4. Write a short brief, a stock at a time: what was filed or published, when (IST), the link, the
   move, and one line on what it means for an intraday plan, for example:
   - results or a board meeting **today**: the price can jump mid-session; a stop is a plan, not a
     guarantee, and can fill well past its price;
   - ex-date **today**: the stock opens lower by about the dividend, so yesterday's levels and the
     previous close are not comparable;
   - an event on a later day: an intraday trade is flat by `flat_by`, so it is not exposed to it.
5. If something changes the picture, say it before /nse-entry and /nse-exit. The user decides.

During the session: `/loop 15m /nse-news`. When a stock has no news, say so; do not pad the brief.

## Example (real data, Tue 10 Nov 2026 09:20 IST, a Diwali holiday morning)

```
$ news.py RBLBANK DSSL RITES INFY
Moves (Yahoo's latest trade against the previous close):
  INFY           Rs. 1,154.10   -1.49% on Mon 09 Nov 2026, not today
  10 Nov 08:48  RBLBANK  NSE announcement      ESOP/ESOS/ESPS: RBL Bank Limited has informed the Exchange
                                               regarding Allotment of 185172 Shares.
      tags: capital   https://nsearchives.nseindia.com/corporate/RBLBANK_10112026084759_...pdf
! 10 Nov 00:48  DSSL     NSE announcement      Board meeting on Sat 14 Nov 2026 (in 4 days): ... to consider and
                                               approve the Unaudited Financial results ... (Financial Results)
! 09 Nov 06:11  RITES    NSE corporate action  Ex-date on Wed 11 Nov 2026 (tomorrow): INTERIM DIVIDEND - RS 1.90
                                               PER SHARE (record date 11-Nov-2026)
Note: Tue 10 Nov 2026 is an NSE trading holiday (Diwali-Balipratipada). Prices are the last session's.
Note: NSE's feeds had 183 announcements (3 yours), 3 board meetings (2 yours), 59 corporate actions (1 yours).
Note: Yahoo has no headlines for RBLBANK, DSSL (it carries little on NSE stocks).
```

Of the 183 announcements that morning, 179 were mutual funds declaring an ETF's NAV. The brief:
"RITES goes ex-dividend tomorrow (Rs. 1.90): it will open about that much lower on Wednesday.
Dynacons decides half-year results on Saturday, outside any intraday trade. RBL Bank allotted
ESOP shares, routine. Nothing new on Infosys."

## What it never does

It never says buy or sell, never predicts what a stock will do, never invents or embellishes a
story, a date or a number, never places an order, and never disguises itself as a browser to get
past NSE's site: if NSE refuses, it says so and carries on with what it has.

## Safety

Paper and simulation only. Education and tooling, not financial advice. Headlines are leads, not
facts, and free feeds can be late or miss an item: no news here does not mean no news. Check the
exchange's site or your broker app before you act. Most retail intraday traders lose money.
