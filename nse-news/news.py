"""nse-news: what is new about your NSE stocks. Standard library only, Python 3.8+.
It only reads and reports. It never places an order and never says buy or sell.

  news.py                          your stocks: the plan, open positions, ORB hits, the watchlist
  news.py INFY RBLBANK             these stocks only
  news.py INFY --all --hours 96    everything in the last 4 days, seen before or not
  news.py --no-headlines           NSE's feeds and the moves only

Three kinds of source:
  1. NSE's own RSS feeds: what companies told the exchange (announcements), board meetings (the
     date results are decided) and corporate actions (ex-dates). Three files for the whole
     market, matched to your stocks by company name.
  2. Yahoo Finance's headline RSS, one request a stock. Often empty for NSE stocks.
  3. The move: Yahoo's latest trade against the previous close. MOVING marks a big one.

It shows an item once (trader/state/news.json remembers 30 days) and writes the brief to
trader/news.json. Times are IST. --offline DIR --now "YYYY-MM-DD HH:MM" replays a folder saved
by tools/snapshot.py news. Headlines are leads, not facts: open the link before you act.
"""

import argparse
import collections
import datetime as _dt
import email.utils
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as XML

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "shared"))

import config     # noqa: E402
import journal    # noqa: E402
import market     # noqa: E402
import nse_files  # noqa: E402
import yahoo      # noqa: E402

M = market
KEEP_DAYS = 30        # how long an item stays remembered as seen
MOVE_PCT = 3.0        # this far from the previous close is flagged MOVING
MAX_HEADLINES = 25    # Yahoo headline requests a run (one a stock)
NOTE = "Headlines are leads, not facts: open the link and read it before you act. Nothing was placed."

# Why an item matters. Matched on the words of the item, never on a guess about the company.
KEYWORDS = [
    ("results", r"\bresults?\b|\bearnings\b|\bEPS\b|\bguidance\b|\bQ[1-4]\b|\bprofit\b|quarter(?:ly)? ended"),
    ("board", r"board meeting"),
    ("dividend", r"\bdividend\b|\bbonus\b|stock split|sub-?division|\bbuy-?back\b"),
    ("capital", r"\ballotment\b|\bpreferential\b|\bQIP\b|rights issue|fund rais|\bESOP|\bdebentures?\b|\bNCDs?\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\boutperform\b|\bunderperform\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bamalgamation\b|\bdemerger\b|\btakeover\b|open offer|"
             r"joint venture|\bstake\b"),
    ("people", r"\bresign|\bappoint|\bcessation\b|\bCEO\b|\bCFO\b|managing director|\bauditors?\b"),
    ("legal", r"\blawsuit\b|\blitigation\b|\bcourt\b|\bNCLT\b|\bpenalt|show cause|\bfraud\b|\binvestigation\b|"
              r"\bprobe\b|search and seizure"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bUSFDA\b|\bFDA\b|\bGST\b|income tax|\bCCI\b"),
    ("risk", r"\bdefault|\binsolven|\bbankrupt|\bpledge|credit rating|\bdelist|\bcyber|\bbreach\b|\bstrike\b|\bfire\b"),
    ("halt", r"\bsuspen(?:ded|sion)\b|\bhalt(?:s|ed)?\b"),
    ("macro", r"repo rate|\binflation\b|\bCPI\b|\bFII\b|\bFPI\b|union budget|\bcrude\b|\brupee\b|\bNifty\b|\bSensex\b"),
]
IMPORTANT_TAGS = {"results", "risk", "halt", "people"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
NSE_DATE = r"(\d{1,2})-([A-Za-z]{3})-(\d{4})"
NAME_WORDS = {"co": "company", "corp": "corporation", "inds": "industries", "i": "india"}


class NewsError(Exception):
    pass


def trader_path(*parts):
    return os.path.join(config.trader_dir(), *parts)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError:
        raise NewsError("%s is not valid JSON; fix or remove it" % path)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ names and dates

def norm_name(name):
    """A company name as a matching key: 'The Federal Bank  Limited' and 'Federal Bank Ltd.'
    both become 'federal bank'. NSE's feeds carry the company's name, not its symbol."""
    s = (name or "").lower().replace("&", " and ").replace(".", "")
    words = [NAME_WORDS.get(w, w) for w in re.sub(r"[^a-z0-9 ]+", " ", s).split()]
    words = [w for w in words if w not in ("limited", "ltd")]
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def nse_date(text):
    """'14-Nov-2026' anywhere in the text -> date, or None."""
    m = re.search(NSE_DATE, text or "")
    if not m or m.group(2).lower() not in MONTHS:
        return None
    try:
        return _dt.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


def nse_time(text):
    """NSE's '10-Nov-2026 08:56:59' (IST wall clock) -> aware IST datetime, or None."""
    m = re.match(r"\s*" + NSE_DATE + r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text or "")
    d = nse_date(text) if m else None
    if d is None:
        return None
    return M.ist(d.year, d.month, d.day, int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))


def rss_time(text):
    """An RSS pubDate ('Thu, 29 Oct 2026 14:01:10 +0000') -> aware IST datetime, or None."""
    try:
        d = email.utils.parsedate_to_datetime((text or "").strip())
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    return M.to_ist(d if d.tzinfo is not None else d.replace(tzinfo=M.UTC))


def fmt_day(d):
    return d.strftime("%a %d %b %Y")


def day_words(d, today):
    n = (d - today).days
    return "TODAY" if n == 0 else "tomorrow" if n == 1 else "in %d days" % n


# ------------------------------------------------------------------ your stocks

class Stocks(object):
    """The stocks to report on, in priority order, with every name each is known by."""

    def __init__(self):
        self.why = collections.OrderedDict()      # SYM.NS -> why it is on the list
        self.names = {}                           # SYM.NS -> [names]
        self.by_name = {}                         # normalised name -> SYM.NS

    def add(self, sym, why):
        self.why.setdefault(sym, why)

    def name(self, sym, name):
        key = norm_name(name)
        if sym in self.why and key:
            if name not in self.names.setdefault(sym, []):
                self.names[sym].append(name)
            self.by_name.setdefault(key, sym)

    def nameless(self):
        return [s for s in self.why if s not in self.names]


def _rows(doc, key):
    return [r for r in ((doc or {}).get(key) or []) if isinstance(r, dict) and r.get("symbol")]


def load_stocks(symbols, day):
    """Given symbols, or else: the plan, open positions, today's ORB hits, the daily watchlist.
    Names come from every trader/ file that has them."""
    st = Stocks()
    plan = read_json(trader_path("plan.json")) or {}
    hits = _rows(read_json(trader_path("orb_hits.json")), "hits")
    watch = _rows(read_json(trader_path("watchlist.json")), "hits")
    universe = _rows(read_json(trader_path("universe.json")), "symbols")
    if symbols:
        for s in symbols:
            st.add(M.normalize_symbol(s), "asked for")
    else:
        if plan.get("symbol"):
            st.add(M.normalize_symbol(plan["symbol"]), "the plan")
        for e in journal.load(day)["entries"]:
            if not e.get("closed"):
                st.add(M.normalize_symbol(e["symbol"]), "open position")
        for r in hits:
            st.add(M.normalize_symbol(r["symbol"]), "ORB hit")
        for r in watch:
            st.add(M.normalize_symbol(r["symbol"]), "watchlist")
    for r in [plan] + hits + watch + universe:
        if r.get("symbol") and r.get("name"):
            try:
                st.name(M.normalize_symbol(r["symbol"]), r["name"])
            except M.MarketError:
                continue
    return st


# ------------------------------------------------------------------ sources

class Source(object):
    """The network, or a folder saved by tools/snapshot.py news."""

    def __init__(self, offline=None):
        self.offline = offline

    def _file(self, name):
        path = os.path.join(self.offline, name)
        if not os.path.exists(path):
            raise yahoo.DataError("no %s in %s" % (name, self.offline))
        return nse_files.read_text(path)

    def feed(self, kind):
        return self._file(nse_files.FEED_NAMES[kind]) if self.offline else nse_files.download_feed(kind)

    def headlines(self, sym):
        if self.offline:
            return self._file("headlines-%s.xml" % M.bare_symbol(sym))
        text = yahoo.fetch_headlines(sym)
        yahoo.SLEEP(yahoo.PAUSE)
        return text

    def sparks(self, syms):
        """{sym: Series} with Yahoo's meta (latest trade, previous close, the company's name)."""
        if self.offline:
            path = os.path.join(self.offline, "moves.json")
            return yahoo.load_offline(path) if os.path.exists(path) else {}
        return yahoo.fetch_spark(syms, "1d", "5m")[0]


def rss_items(text, what):
    """RSS text -> [{tag: text}] for each <item>."""
    try:
        root = XML.fromstring(text)
    except XML.ParseError as e:
        raise NewsError("%s is not valid XML (%s)" % (what, e))
    return [{c.tag: " ".join((c.text or "").split()) for c in it} for it in root.iter("item")]


def fields(desc):
    """NSE's 'KEY:value |KEY:value' description -> {key: value} (keys lower case)."""
    out = {}
    for part in desc.split("|"):
        k, sep, v = part.partition(":")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def whose(st, company, link="", desc=""):
    """The stock on your list an NSE item is about, or None. By the company's name, by the
    symbol that starts the file's name (INFY_10112026...pdf), or by 'SYMBOL : ' in the text."""
    sym = st.by_name.get(norm_name(company))
    if sym:
        return sym
    for text, pat in ((link.rsplit("/", 1)[-1], r"^([A-Z0-9&-]+)_\d"), (desc, r"^([A-Z0-9&-]+)\s+:\s")):
        m = re.match(pat, text)
        if m:
            try:
                sym = M.normalize_symbol(m.group(1))
            except M.MarketError:
                continue
            if sym in st.why:
                return sym
    return None


def nse_items(st, kind, rows):
    """One NSE feed's rows -> items for the stocks on your list."""
    out = []
    for r in rows:
        title, desc, link = r.get("title", ""), r.get("description", ""), r.get("link", "")
        company = title.split(" - Ex-Date:")[0].strip()
        sym = whose(st, company, link, desc)
        if not sym:
            continue
        it = {"symbol": sym, "company": company, "link": link, "time": nse_time(r.get("pubDate")),
              "event": None, "event_date": None}
        if kind == "corporate_actions":
            f = fields(desc)
            it.update(source="NSE corporate action", event="ex-date", event_date=nse_date(title.partition("Ex-Date:")[2]),
                      title=f.get("purpose") or desc)
            if f.get("record date", "-") != "-":
                it["title"] += " (record date %s)" % f["record date"]
            it["id"] = "nse-ca:%s|%s|%s" % (sym, it["event_date"], f.get("purpose"))
        elif kind == "board_meetings":
            what, _, rest = desc.partition("|")
            it.update(source="NSE board meeting", event="board meeting", event_date=nse_date(rest),
                      title=what.strip() or "Board meeting")
            it["id"] = "nse-bm:" + (link or "%s|%s" % (company, desc))
        else:
            text, _, subject = desc.partition("|SUBJECT:")
            it.update(source="NSE announcement",
                      title=re.sub(r"\s+([.,;])", r"\1", "%s: %s" % (subject.strip(), text.strip())).strip(": "))
            held = re.search(r"Board Meeting to be held on\s+" + NSE_DATE, text, re.I)
            if held:
                it.update(event="board meeting", event_date=nse_date(held.group(0)))
            it["id"] = "nse:" + (link or "%s|%s|%s" % (company, desc, r.get("pubDate")))
        if it["event"] == "board meeting" and it["event_date"]:      # one item a meeting, whichever feed
            it["id"] = "nse-bm:%s|%s" % (sym, it["event_date"])
        out.append(it)
    return out


def yahoo_items(sym, rows):
    out = []
    for r in rows:
        link = r.get("link", "")
        out.append({"id": "yahoo:" + (r.get("guid") or link or r.get("title", "")), "source": "Yahoo Finance",
                    "symbol": sym, "title": r.get("title", ""), "summary": r.get("description", ""),
                    "link": link, "time": rss_time(r.get("pubDate")), "event": None, "event_date": None})
    return out


def tag(items):
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item a story: the same id, or the same link. The first wins; a later copy with more
    words (the announcement behind a board-meeting line) lends them."""
    seen, out = {}, []
    generic = re.compile(r"nseindia\.com/companies-listing/")
    for it in items:
        keys = [it["id"]] + ([it["link"]] if it["link"] and not generic.search(it["link"]) else [])
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is None:
            for k in keys:
                seen[k] = it
            out.append(it)
            continue
        if it["event_date"] and not hit["event_date"]:
            hit["event"], hit["event_date"], hit["id"] = it["event"], it["event_date"], it["id"]
        if it["title"] not in hit["title"] and hit["title"] not in it["title"]:
            hit["title"] = "%s (%s)" % (hit["title"].rstrip("."), it["title"])
        elif len(it["title"]) > len(hit["title"]):
            hit["title"] = it["title"]
        hit["tags"] = hit["tags"] + [t for t in it["tags"] if t not in hit["tags"]]
        seen[hit["id"]] = hit
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


def select(items, seen, now, hours, show_all):
    """What to show: dated events from today on, other items inside the last `hours`; only what
    was not shown before unless show_all. Marks each item new or not, and important or not."""
    today, cutoff, ahead = now.date(), now - _dt.timedelta(hours=hours), now + _dt.timedelta(minutes=10)
    out = []
    for it in items:
        if it["time"] is not None and it["time"] > ahead:
            continue                                   # a pinned --now: nothing from after it
        if it["event_date"]:
            if it["event_date"] < today:
                continue
        elif it["time"] is not None and it["time"] < cutoff:
            continue
        it["new"] = fingerprint(it) not in seen
        soon = it["event_date"] is not None and (it["event_date"] - today).days <= 1
        it["important"] = it["source"].startswith("NSE") and (soon or bool(IMPORTANT_TAGS & set(it["tags"])))
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now, i["symbol"]), reverse=True)
    return out


def moves_from(st, sparks, today):
    out = []
    for sym in st.why:
        s = sparks.get(sym)
        meta = s.meta if s is not None else {}
        price, prev = meta.get("regularMarketPrice"), meta.get("chartPreviousClose") or meta.get("previousClose")
        if not price or not prev:
            continue
        when = M.to_ist(meta["regularMarketTime"]) if meta.get("regularMarketTime") else None
        out.append({"symbol": sym, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
                    "pct": round((float(price) / float(prev) - 1) * 100, 2), "time": when,
                    "stale": when is not None and when.date() != today})
    return out


# ------------------------------------------------------------------ the brief

def render(st, shown, moves, notes, now, a):
    out = ["News for %d stock%s at %s IST on %s (%s)." % (
        len(st.why), "" if len(st.why) == 1 else "s", M.fmt_time(now), fmt_day(now.date()),
        "everything in the window" if a.all else "only what is new since the last check")]
    with_news = {i["symbol"] for i in shown}
    rows = [m for m in moves if len(moves) <= 12 or abs(m["pct"]) >= a.move_pct or m["symbol"] in with_news]
    if rows:
        out.append("")
        out.append("Moves (Yahoo's latest trade against the previous close):")
        for m in rows:
            when = "" if m["time"] is None else (" at %s" % M.fmt_time(m["time"]) if not m["stale"] else
                                                 " on %s, not today" % fmt_day(m["time"].date()))
            out.append("  %-12s %14s  %+6.2f%%%s%s" % (M.bare_symbol(m["symbol"]), M.fmt_inr(m["price"]), m["pct"], when,
                                                    "   MOVING" if abs(m["pct"]) >= a.move_pct else ""))
        if len(rows) < len(moves):
            out.append("  (%d more stocks moved less than %g%% and have no news.)" % (len(moves) - len(rows), a.move_pct))
    out.append("")
    if not shown:
        out.append("Nothing new.")
    for it in shown:
        when = it["time"].strftime("%d %b %H:%M") if it["time"] else "time unknown"
        title = it["title"]
        if it["event_date"]:
            title = "%s on %s (%s): %s" % (it["event"].capitalize(), fmt_day(it["event_date"]),
                                           day_words(it["event_date"], now.date()), title)
        out.append("%s %s  %-11s %-21s %s" % ("!" if it["important"] else " ", when, M.bare_symbol(it["symbol"]),
                                              it["source"], title))
        extra = (["tags: " + ", ".join(it["tags"])] if it["tags"] else []) + ([it["link"]] if it["link"] else [])
        if extra:
            out.append("      " + "   ".join(extra))
    out.append("")
    out.extend("Note: " + n for n in notes)
    out.append(NOTE)
    return "\n".join(out) + "\n"


def as_json(it):
    d = {k: it.get(k) for k in ("symbol", "source", "title", "link", "tags", "event", "important", "new")}
    d["time"] = it["time"].strftime("%Y-%m-%d %H:%M IST") if it["time"] else None
    d["event_date"] = str(it["event_date"]) if it["event_date"] else None
    return d


def run(a):
    if a.offline and not a.now:
        raise NewsError("--offline needs --now \"YYYY-MM-DD HH:MM\"")
    if a.hours <= 0 or a.move_pct <= 0 or a.max_headlines < 0:
        raise NewsError("--hours and --move-pct must be above 0, --max-headlines 0 or more")
    now = M.parse_ist(a.now) if a.now else M.now_ist()
    st = load_stocks(a.symbols, now.date())
    if not st.why:
        raise NewsError("No stocks to check. Name some (news.py INFY RBLBANK), or run /nse-scanner daily "
                        "first: it writes trader/watchlist.json.")
    src, notes, items = Source(a.offline), [], []
    try:                                  # news is worth reading on a closed day too; just say so
        day = M.load_calendar(a.holidays).status(now.date())
        if not day.tradable and day.kind != "unknown_year":
            notes.append("%s Prices are the last session's." % day.reason)
    except M.MarketError:
        pass

    moves = []
    if not a.no_moves:
        try:
            sparks = src.sparks(list(st.why))
        except yahoo.DataError as e:
            sparks = {}
            notes.append("no prices: %s" % e)
        for sym, s in sparks.items():
            for key in ("longName", "shortName"):          # Yahoo's name is NSE's own name for the company
                st.name(sym, s.meta.get(key))
        moves = moves_from(st, sparks, now.date())

    if not a.no_nse:
        read = []
        for kind in ("announcements", "board_meetings", "corporate_actions"):
            try:
                rows = rss_items(src.feed(kind), nse_files.FEED_NAMES[kind])
            except (yahoo.DataError, NewsError) as e:
                notes.append("skipped NSE's %s: %s" % (kind.replace("_", " "), e))
                continue
            got = nse_items(st, kind, rows)
            items += got
            read.append("%d %s (%d yours)" % (len(rows), kind.replace("_", " "), len(got)))
        if read:
            notes.append("NSE's feeds had %s. They hold the latest items only, so check often." % ", ".join(read))
        if st.nameless():
            notes.append("no company name known for %s, so NSE items that carry only the name were not matched. "
                         "/nse-scanner universe and daily save the names." % ", ".join(
                             M.bare_symbol(s) for s in st.nameless()))

    if not a.no_headlines:
        asked, empty = list(st.why)[:a.max_headlines], []
        for sym in asked:
            try:
                rows = rss_items(src.headlines(sym), "Yahoo's headlines for %s" % M.bare_symbol(sym))
            except (yahoo.DataError, NewsError) as e:
                notes.append(str(e))
                continue
            if not rows:
                empty.append(M.bare_symbol(sym))
            items += yahoo_items(sym, rows)
        if empty:
            notes.append("Yahoo has no headlines for %s (it carries little on NSE stocks)." % ", ".join(empty))
        if len(asked) < len(st.why):
            notes.append("Yahoo headlines were read for the first %d of %d stocks (the plan, open positions and "
                         "ORB hits come first); --max-headlines changes that." % (len(asked), len(st.why)))

    state_path = trader_path("state", "news.json")
    state = read_json(state_path, {}) or {}
    seen = state.get("seen") or {}
    items = dedupe(tag(items))
    shown = select(items, seen, now, a.hours, a.all)

    doc = {"as_of": now.strftime("%Y-%m-%d %H:%M IST"), "stocks": list(st.why),
           "moves": [dict(m, time=m["time"].strftime("%Y-%m-%d %H:%M IST") if m["time"] else None,
                          moving=abs(m["pct"]) >= a.move_pct) for m in moves],
           "items": [as_json(i) for i in shown], "notes": notes,
           "note": "Leads, not facts. Read the link before acting. Not financial advice."}
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        sys.stdout.write(render(st, shown, moves, notes, now, a))
    if not a.dry_run:
        stamp = now.strftime("%Y-%m-%d %H:%M")
        for it in items:
            if "new" in it:                         # only what select() let through the window
                seen.setdefault(fingerprint(it), stamp)
        cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M")
        write_json(state_path, {"last_check": stamp, "seen": {k: v for k, v in seen.items() if v >= cutoff}})
        write_json(trader_path("news.json"), doc)
    return 0 if any(i["new"] for i in shown) else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="news.py", description="What is new about your NSE stocks (read only).")
    ap.add_argument("symbols", nargs="*", help="default: the plan, open positions, ORB hits, the watchlist")
    ap.add_argument("--hours", type=float, default=48, help="ignore undated items older than this (default 48)")
    ap.add_argument("--all", action="store_true", help="show items already seen too")
    ap.add_argument("--no-nse", action="store_true", help="skip NSE's feeds")
    ap.add_argument("--no-headlines", action="store_true", help="skip Yahoo's headlines")
    ap.add_argument("--no-moves", action="store_true", help="skip the prices")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big, percent (default 3)")
    ap.add_argument("--max-headlines", type=int, default=MAX_HEADLINES, help="Yahoo headline requests a run")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="do not remember what was shown, write nothing")
    ap.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    ap.add_argument("--offline", metavar="DIR", help="a folder saved by tools/snapshot.py news")
    ap.add_argument("--holidays")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return run(a)
    except (NewsError, M.MarketError, yahoo.DataError) as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
