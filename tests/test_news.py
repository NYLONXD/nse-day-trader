"""Phase 8: nse-news on the real feeds of Tue 10 Nov 2026, 09:18 IST (a Diwali holiday morning):
NSE's announcement, board-meeting and corporate-action RSS, Yahoo's headline RSS and spark."""

import json
import os
import unittest

from _load import FIX, load_skill

import nse_files
import yahoo

from test_budget import CAL, Chain

news = load_skill("nse-news", "news")

DAY = os.path.join(FIX, "nse-news", "2026-11-10")
NOW = "2026-11-10 09:20"
ALL = ["RBLBANK", "DSSL", "RITES", "INFY", "RELIANCE", "M&M"]


class Base(Chain):
    def nw(self, *argv, now=NOW, offline=True):
        args = list(argv) + ["--now", now, "--holidays", CAL] + (["--offline", DAY] if offline else [])
        return self.run_mod(news, args)

    def put(self, name, doc):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def saved(self, *parts):
        with open(os.path.join(self.tmp, *parts), encoding="utf-8") as f:
            return json.load(f)


class Names(unittest.TestCase):
    def test_the_index_list_name_matches_nses_own_name(self):
        # Real pairs: ind_nifty500list.csv against EQUITY_L.csv (23 of 501 differ before this).
        for a, b in (("Federal Bank Ltd.", "The Federal Bank  Limited"),
                     ("Tata Power Co. Ltd.", "Tata Power Company Limited"),
                     ("J.K. Cement Ltd.", "JK Cement Limited"),
                     ("CCL Products (I) Ltd.", "CCL Products (India) Limited"),
                     ("Jammu & Kashmir Bank Ltd.", "The Jammu & Kashmir Bank Limited"),
                     ("NDL Ventures Limited", "NDL VENTURES LIMITED")):
            self.assertEqual(news.norm_name(a), news.norm_name(b))
        self.assertNotEqual(news.norm_name("Tata Power Company Limited"), news.norm_name("Tata Steel Limited"))

    def test_nse_and_rss_times_are_ist(self):
        self.assertEqual(news.nse_time("10-Nov-2026 08:56:59").isoformat(), "2026-11-10T08:56:59+05:30")
        self.assertEqual(news.nse_time("02-May-2026 16:46").isoformat(), "2026-05-02T16:46:00+05:30")
        self.assertIsNone(news.nse_time("soon"))
        self.assertEqual(news.rss_time("Fri, 06 Nov 2026 12:29:51 +0000").isoformat(), "2026-11-06T17:59:51+05:30")
        self.assertEqual(str(news.nse_date("Board Meeting Intimation |Meeting Date: 14-Nov-2026")), "2026-11-14")


class Brief(Base):
    def test_the_real_morning(self):
        code, out = self.nw(*ALL)
        self.assertEqual(code, 0, out)
        self.assertIn("News for 6 stocks at 09:20 IST on Tue 10 Nov 2026", out)
        # what RBL Bank told the exchange that morning: matched by Yahoo's name for the company
        self.assertIn("10 Nov 08:56  RBLBANK     NSE announcement      Alteration Of Capital and Fund Raising-XBRL: "
                      "RBL Bank Limited has informed the Exchange regarding Allotment of Securities", out)
        self.assertIn("ESOP/ESOS/ESPS: RBL Bank Limited has informed the Exchange regarding Allotment of 185172 Shares.", out)
        # one line for Dynacons's board meeting although three feed rows describe it
        self.assertEqual(out.count("Board meeting on Sat 14 Nov 2026 (in 4 days)"), 1)
        self.assertIn("! 10 Nov 00:48  DSSL", out)
        self.assertIn("Unaudited Financial results of the Company for the Half Yearly ended September 2026 "
                      "(Financial Results)", out)
        self.assertIn("tags: results, board", out)
        self.assertIn("! 09 Nov 06:11  RITES       NSE corporate action  Ex-date on Wed 11 Nov 2026 (tomorrow): "
                      "INTERIM DIVIDEND - RS 1.90 PER SHARE (record date 11-Nov-2026)", out)
        self.assertNotIn("Declaration of NAV", out)                 # 179 of the 183 announcements: not your stocks
        self.assertNotIn("NDL", out)
        self.assertIn("183 announcements (3 yours), 3 board meetings (2 yours), 59 corporate actions (1 yours)", out)
        self.assertIn("Yahoo has no headlines for RBLBANK, DSSL, RELIANCE", out)
        self.assertIn("Nothing was placed.", out)

    def test_moves_are_the_last_session_and_say_so(self):
        code, out = self.nw("INFY", "RBLBANK", "--no-nse", "--no-headlines")
        self.assertEqual(code, 1)                                    # prices alone are not news
        self.assertIn("INFY           Rs. 1,154.10   -1.49% on Mon 09 Nov 2026, not today", out)   # 1,171.60 before
        self.assertIn("RBLBANK          Rs. 379.00   +0.73%", out)
        self.assertNotIn("MOVING", out)
        code, out = self.nw("INFY", "--no-nse", "--no-headlines", "--move-pct", "1")
        self.assertIn("-1.49% on Mon 09 Nov 2026, not today   MOVING", out)

    def test_a_closed_day_is_said_not_refused(self):
        cal = os.path.join(self.tmp, "cal.json")
        with open(cal, "w") as f:
            json.dump({"years_covered": [2026], "source": {"2026": "test"},
                       "holidays": [{"date": "2026-11-10", "name": "test holiday"}], "special_sessions": []}, f)
        code, out = self.run_mod(news, ["RITES", "--offline", DAY, "--now", NOW, "--holidays", cal])
        self.assertEqual(code, 0)
        self.assertIn("Note: Tue 10 Nov 2026 is an NSE trading holiday (test holiday). Prices are the last session's.", out)

    def test_shown_once_then_all_brings_it_back(self):
        code, out = self.nw(*ALL)
        self.assertEqual(code, 0)
        code, out = self.nw(*ALL, now="2026-11-10 09:35")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        code, out = self.nw(*ALL, "--all", now="2026-11-10 09:35")
        self.assertEqual(code, 1)                                    # shown, but none of it is new
        self.assertIn("RITES       NSE corporate action", out)
        self.assertEqual(self.saved("state", "news.json")["last_check"], "2026-11-10 09:35")

    def test_dry_run_remembers_nothing(self):
        code, out = self.nw("RITES", "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "state", "news.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "news.json")))
        code, out = self.nw("RITES")
        self.assertEqual(code, 0)                                    # still new

    def test_the_window(self):
        # Real Yahoo headlines: M&M's Q2 profit story is from Fri 6 Nov 17:59 IST, 87 hours before.
        code, out = self.nw("M&M", "INFY", "--no-nse")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        code, out = self.nw("M&M", "INFY", "--no-nse", "--hours", "96")
        self.assertEqual(code, 0)
        self.assertIn("06 Nov 17:59  M&M         Yahoo Finance         Mahindra posts 25% rise in Q2 consolidated "
                      "profit after tax", out)
        self.assertIn("tags: results", out)
        self.assertNotIn("! 06 Nov", out)                            # "!" is for what the company filed
        self.assertNotIn("Cognizant", out)                           # INFY's feed, 29 Oct: outside 96 hours
        code, out = self.nw("INFY", "--no-nse", "--hours", "400")
        self.assertIn("29 Oct 19:31  INFY        Yahoo Finance         Cognizant Sinks as Bookings Drop 7%", out)

    def test_a_pinned_clock_shows_nothing_from_after_it(self):
        code, out = self.nw("RBLBANK", now="2026-11-10 08:40")
        self.assertIn("ESOP/ESOS/ESPS", out)                         # 08:48: inside the 10 minutes allowed for clocks that differ
        self.assertNotIn("Alteration Of Capital", out)               # 08:56: more than 10 minutes ahead

    def test_an_ex_date_that_has_passed_is_dropped(self):
        self.put("watchlist.json", {"hits": [{"symbol": "SAKSOFT.NS", "name": "Saksoft Ltd."},
                                             {"symbol": "REPCOHOME.NS", "name": "Repco Home Finance Ltd."}]})
        code, out = self.nw("--no-moves", "--no-headlines")
        self.assertEqual(code, 0)
        self.assertIn("News for 2 stocks", out)
        self.assertIn("REPCOHOME   NSE corporate action  Ex-date on Fri 13 Nov 2026 (in 3 days): "
                      "INTERIM DIVIDEND - RS 5 PER SHARE (record date 13-Nov-2026)", out)
        self.assertNotIn("SAKSOFT", out)                             # ex-date Mon 9 Nov: yesterday
        self.assertNotIn("! ", out)                                  # three days away: not marked


class Stocks(Base):
    def test_without_a_name_only_the_file_name_can_match(self):
        code, out = self.nw("RBLBANK", "DSSL", "--no-moves", "--no-headlines")
        self.assertEqual(code, 0)
        self.assertIn("ESOP/ESOS/ESPS", out)                         # RBLBANK_10112026084759_...pdf
        self.assertIn("DSSL        NSE board meeting     Board meeting on Sat 14 Nov 2026 (in 4 days): Financial Results", out)
        self.assertNotIn("Alteration Of Capital", out)               # an XBRL file: the name only
        self.assertIn("no company name known for RBLBANK, DSSL", out)

    def test_names_come_from_the_scanners_files(self):
        self.put("universe.json", {"symbols": [{"symbol": "RBLBANK.NS", "name": "RBL Bank Ltd.", "band": "No Band"}]})
        code, out = self.nw("RBLBANK", "--no-moves", "--no-headlines")
        self.assertIn("Alteration Of Capital", out)
        self.assertNotIn("no company name known", out)

    def test_default_stocks_plan_open_positions_hits_watchlist(self):
        self.put("plan.json", {"symbol": "RITES", "name": "", "session": "2026-11-10"})
        self.put("state/journal-2026-11-10.json", {"date": "2026-11-10", "entries": [
            {"symbol": "INFY.NS", "closed": False}, {"symbol": "RELIANCE.NS", "closed": True}]})
        self.put("orb_hits.json", {"hits": [{"symbol": "DSSL.NS", "name": "Dynacons Systems & Solutions Ltd."}]})
        self.put("watchlist.json", {"hits": [{"symbol": "RBLBANK.NS", "name": "RBL Bank Ltd."},
                                             {"symbol": "RITES.NS", "name": "RITES Ltd."}]})
        code, out = self.nw("--max-headlines", "2", "--json", "--dry-run")
        doc = json.loads(out)
        self.assertEqual(doc["stocks"], ["RITES.NS", "INFY.NS", "DSSL.NS", "RBLBANK.NS"])   # the closed trade is out
        self.assertIn("Yahoo headlines were read for the first 2 of 4 stocks", " ".join(doc["notes"]))

    def test_no_stocks(self):
        code, out = self.nw()
        self.assertEqual(code, 1)
        self.assertIn("/nse-scanner daily", out)
        code, out = self.nw("BRK.B")
        self.assertIn("only NSE symbols", out)
        code, out = self.run_mod(news, ["INFY", "--offline", DAY])
        self.assertIn("--offline needs --now", out)


class Written(Base):
    def test_news_json(self):
        self.nw("RITES", "INFY")
        doc = self.saved("news.json")
        self.assertEqual(doc["as_of"], "2026-11-10 09:20 IST")
        self.assertEqual(doc["moves"][1], {"symbol": "INFY.NS", "price": 1154.1, "prev_close": 1171.6, "pct": -1.49,
                                           "time": "2026-11-09 15:28 IST", "stale": True, "moving": False})
        item = [i for i in doc["items"] if i["symbol"] == "RITES.NS"][0]
        self.assertEqual((item["event"], item["event_date"], item["important"], item["new"], item["tags"]),
                         ("ex-date", "2026-11-11", True, True, ["dividend"]))


class Failures(Base):
    def test_a_source_that_fails_is_a_note_not_a_crash(self):
        text = {k: nse_files.read_text(os.path.join(DAY, v)) for k, v in nse_files.FEED_NAMES.items()}
        sparks = yahoo.load_offline(os.path.join(DAY, "moves.json"))
        asked = []

        def feed(kind):
            if kind == "announcements":
                raise yahoo.DataError("NSE did not send Online_announcements.xml (HTTP Error 403: Forbidden)")
            return "<rss><channel><item>" if kind == "board_meetings" else text[kind]

        def headlines(sym):
            asked.append(sym)
            raise yahoo.DataError("Yahoo sent no headlines for RITES ([Errno 11001] getaddrinfo failed)")
        old = nse_files.download_feed, yahoo.fetch_headlines, yahoo.fetch_spark
        nse_files.download_feed, yahoo.fetch_headlines = feed, headlines
        yahoo.fetch_spark = lambda syms, rng, interval: ({s: sparks[s] for s in syms}, {})
        try:
            code, out = self.nw("RITES", offline=False)
        finally:
            nse_files.download_feed, yahoo.fetch_headlines, yahoo.fetch_spark = old
        self.assertEqual(code, 0)
        self.assertEqual(asked, ["RITES.NS"])
        self.assertIn("Note: skipped NSE's announcements: NSE did not send Online_announcements.xml (HTTP Error 403", out)
        self.assertIn("Note: skipped NSE's board meetings: Board_Meetings.xml is not valid XML", out)
        self.assertIn("Note: Yahoo sent no headlines for RITES", out)
        self.assertIn("Ex-date on Wed 11 Nov 2026 (tomorrow)", out)  # the feed that did arrive still reports

    def test_the_feed_addresses_are_nses_and_the_agent_is_honest(self):
        for url in nse_files.FEEDS.values():
            self.assertTrue(url.startswith("https://nsearchives.nseindia.com/content/RSS/"), url)
        self.assertEqual(set(nse_files.FEEDS), set(nse_files.FEED_NAMES))
        self.assertNotRegex(yahoo.UA, r"Mozilla|Chrome|Safari")
        self.assertIn("region=IN", yahoo.HEADLINES)


if __name__ == "__main__":
    unittest.main()
