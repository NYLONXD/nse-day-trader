"""Save real data as offline test fixtures. Standard library only.

  python tools/snapshot.py nse-files OUTDIR
      EQUITY_L.csv, ind_nifty500list.csv, ind_nifty50list.csv, sec_list.csv from NSE.
  python tools/snapshot.py charts --symbols A,B --range 5d --interval 5m --out FILE.json.gz
      one bundle {"SYM.NS": <raw v8 chart>} (what nse-scanner --offline reads)
  python tools/snapshot.py spark --symbols A,B --range 3mo --interval 1d --out FILE.json
      Yahoo's raw spark answers merged into one {"spark": {"result": [...]}}
  --symbols-file FILE: one symbol a line, or a CSV with a Symbol/SYMBOL column.

Honest User-Agent only. If NSE refuses, download the file in your browser instead.
"""

import argparse
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "shared"))

import market  # noqa: E402
import yahoo   # noqa: E402
import nse_files  # noqa: E402


def read_symbols(a):
    syms = [s for s in (a.symbols or "").split(",") if s.strip()]
    if a.symbols_file:
        with open(a.symbols_file, encoding="utf-8-sig") as f:
            text = f.read()
        first = text.splitlines()[0] if text else ""
        if "," in first:
            for row in csv.DictReader(text.splitlines()):
                row = {k.strip().lower(): v for k, v in row.items()}
                if row.get("symbol"):
                    syms.append(row["symbol"])
        else:
            syms.extend(l for l in text.splitlines() if l.strip())
    return [market.normalize_symbol(s) for s in syms]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("nse-files", "charts", "spark"))
    ap.add_argument("outdir", nargs="?")
    ap.add_argument("--symbols")
    ap.add_argument("--symbols-file")
    ap.add_argument("--range", default="5d")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    stamp = market.now_ist().strftime("%Y-%m-%d %H:%M IST")

    if a.what == "nse-files":
        os.makedirs(a.outdir, exist_ok=True)
        for kind, name in nse_files.FILE_NAMES.items():
            try:
                body = nse_files.download(kind)
            except yahoo.DataError as e:
                print(e)
                continue
            with open(os.path.join(a.outdir, name), "w", encoding="utf-8", newline="") as f:
                f.write(body)
            print("%s: %d bytes" % (name, len(body)))
            time.sleep(yahoo.PAUSE)
        return 0

    syms = read_symbols(a)
    if a.what == "charts":
        docs, failed = {"_fetched": stamp, "_request": "range=%s interval=%s" % (a.range, a.interval)}, []
        for i, s in enumerate(syms):
            try:
                docs[s] = json.loads(yahoo.yahoo_get(yahoo.chart_path(s, a.range, a.interval)))
            except (yahoo.DataError, ValueError) as e:
                failed.append("%s (%s)" % (s, e))
            time.sleep(yahoo.PAUSE)
            if (i + 1) % 20 == 0:
                print("  %d/%d" % (i + 1, len(syms)), file=sys.stderr)
        yahoo.save_bundle(a.out, docs)
        print("Saved %d charts to %s%s" % (len(docs) - 2, a.out, "; failed: " + ", ".join(failed) if failed else ""))
        return 0

    merged = {"_fetched": stamp, "spark": {"result": []}}
    for i in range(0, len(syms), yahoo.SPARK_MAX):
        doc = json.loads(yahoo.yahoo_get(yahoo.spark_path(syms[i:i + yahoo.SPARK_MAX], a.range, a.interval)))
        merged["spark"]["result"].extend((doc.get("spark") or {}).get("result") or [])
        time.sleep(yahoo.PAUSE)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, separators=(",", ":"))
    print("Saved spark for %d of %d symbols to %s" % (len(merged["spark"]["result"]), len(syms), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
