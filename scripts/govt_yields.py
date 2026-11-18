"""Collect government bond par yields for the three tracked currencies.

Usage:
    python3 scripts/govt_yields.py                       # all three curves
    python3 scripts/govt_yields.py --only USD,JPY        # some curves only
    python3 scripts/govt_yields.py --rba-file PATH.csv   # ingest a saved RBA F2 CSV
    python3 scripts/govt_yields.py --since 2015-01-01    # longer history

Curves (all public primary sources, daily, as published):
    USD  US Treasury par yield curve (home.treasury.gov, one CSV per year)
    JPY  MOF Japan JGB par yields (jgbcm_all.csv to last month end, jgbcm.csv this month)
    AUD  RBA statistical table F2, Capital Market Yields - Government Bonds - Daily

Raw downloads are saved under sources/markets/<source>/ (an unchanged download
is not saved twice). Long-format CSVs go to data/markets/govt_yield__<CCY>.csv,
one row per curve, tenor and business day, with source URL, retrieval date and
snapshot path. Existing points are never changed: a value that differs from
the one on file is reported as a conflict, as for data/rates/.

The RBA site refuses scripted downloads from some networks (HTTP 403). Nothing
is worked around: the curve is reported as blocked, and a copy saved from a
browser (https://www.rba.gov.au/statistics/tables/csv/f2-data.csv) can be
ingested with --rba-file.

Privacy rule: outbound requests carry the generic browser User-Agent from
common.py and nothing else. No cookies, names, emails or contact details.
"""

import argparse
import csv
import datetime as dt
import io
import re
import sys
import urllib.error
from pathlib import Path

import common

MARKETS_SRC = common.SOURCES / "markets"
MARKETS_DATA = common.DATA / "markets"

COLUMNS = [
    "curve",
    "currency",
    "tenor_years",
    "date",
    "value_pct",
    "source_url",
    "retrieved_date",
    "snapshot",
]
KEY = ["curve", "currency", "tenor_years", "date"]

# Tenors kept per curve (years). The task's minimum set is UST 2/5/10/20,
# JGB 2/5/10/15/20 and ACGB 2/5/10; the neighbouring published tenors are kept
# too so that a product tenor can be matched to a close point on the curve.
UST_TENORS = {"1 Yr": 1, "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7, "10 Yr": 10, "20 Yr": 20, "30 Yr": 30}
JGB_TENORS = {"1年": 1, "2年": 2, "3年": 3, "5年": 5, "7年": 7, "10年": 10, "15年": 15, "20年": 20, "30年": 30}

UST_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
MOF_ALL_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
MOF_MONTH_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/f2-data.csv"

ERAS = {"S": 1925, "H": 1988, "R": 2018}


class Blocked(Exception):
    pass


def row(curve, currency, tenor, date, value, url, retrieved, snapshot):
    return {
        "curve": curve,
        "currency": currency,
        "tenor_years": str(tenor),
        "date": date,
        "value_pct": value,
        "source_url": url,
        "retrieved_date": retrieved,
        "snapshot": snapshot,
    }


def number(text):
    text = text.strip()
    if not text or text in {"-", "N/A", "NA"}:
        return None
    try:
        return "%g" % float(text)
    except ValueError:
        return None


# ---------- US Treasury ----------

def parse_ust(text, url, retrieved, snapshot, since):
    out = []
    for rec in csv.DictReader(io.StringIO(text)):
        month, day, year = rec["Date"].split("/")
        date = "%s-%s-%s" % (year, month, day)
        if date < since:
            continue
        for col, tenor in UST_TENORS.items():
            value = number(rec.get(col) or "")
            if value is not None:
                out.append(row("UST", "USD", tenor, date, value, url, retrieved, snapshot))
    return out


def collect_ust(since):
    rows = []
    retrieved = common.today()
    for year in range(int(since[:4]), dt.date.today().year + 1):
        url = UST_URL.format(year=year)
        body = common.fetch(url)
        snap = common.save_snapshot(MARKETS_SRC / "ust", "daily_treasury_par_yield_%d" % year, "csv", body)
        got = parse_ust(body.decode("utf-8-sig"), url, retrieved, common.rel(snap), since)
        print("  UST %d: %d points" % (year, len(got)))
        rows += got
    return rows


# ---------- MOF Japan ----------

def wareki(text):
    """'R8.11.16' -> '2026-11-16'."""
    m = re.fullmatch(r"([SHR])(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        return None
    era, y, mo, d = m.groups()
    return "%04d-%02d-%02d" % (ERAS[era] + int(y), int(mo), int(d))


def parse_mof(text, url, retrieved, snapshot, since):
    reader = csv.reader(io.StringIO(text))
    header = None
    out = []
    for rec in reader:
        if rec and rec[0].strip() == "基準日":
            header = [c.strip() for c in rec]
            continue
        if header is None or not rec:
            continue
        date = wareki(rec[0])
        if not date or date < since:
            continue
        for col, tenor in JGB_TENORS.items():
            i = header.index(col)
            value = number(rec[i]) if i < len(rec) else None
            if value is not None:
                out.append(row("JGB", "JPY", tenor, date, value, url, retrieved, snapshot))
    if header is None:
        raise ValueError("no 基準日 header row in %s" % url)
    return out


def collect_jgb(since):
    rows = []
    retrieved = common.today()
    for name, url in (("jgbcm_all", MOF_ALL_URL), ("jgbcm", MOF_MONTH_URL)):
        body = common.fetch(url)
        snap = common.save_snapshot(MARKETS_SRC / "mof", name, "csv", body)
        got = parse_mof(common.decode(body), url, retrieved, common.rel(snap), since)
        print("  JGB %s: %d points" % (name, len(got)))
        rows += got
    return rows


# ---------- RBA F2 ----------

def parse_rba(text, url, retrieved, snapshot, since):
    """F2 has metadata rows (Title, Description, ..., Series ID) then dd-Mon-yyyy rows."""
    reader = list(csv.reader(io.StringIO(text)))
    titles = next((r for r in reader if r and r[0].strip() == "Title"), None)
    if titles is None:
        raise ValueError("no Title row in RBA F2 file")
    tenors = {}
    for i, title in enumerate(titles):
        m = re.fullmatch(r"Australian Government (\d+) year bond", title.strip(), re.I)
        if m:
            tenors[i] = int(m.group(1))
    if not tenors:
        raise ValueError("no 'Australian Government N year bond' columns in RBA F2 file")
    out = []
    for rec in reader:
        if not rec:
            continue
        try:
            date = dt.datetime.strptime(rec[0].strip(), "%d-%b-%Y").date().isoformat()
        except ValueError:
            continue
        if date < since:
            continue
        for i, tenor in tenors.items():
            value = number(rec[i]) if i < len(rec) else None
            if value is not None:
                out.append(row("ACGB", "AUD", tenor, date, value, url, retrieved, snapshot))
    return out


def collect_acgb(since, rba_file=None, rba_retrieved=None):
    if rba_file:
        body = Path(rba_file).read_bytes()
        retrieved = rba_retrieved or common.today()
    else:
        try:
            body = common.fetch(RBA_URL)
        except urllib.error.HTTPError as err:
            raise Blocked("RBA answered HTTP %d; save f2-data.csv from a browser and pass --rba-file" % err.code)
        if body.lstrip()[:1] == b"<":
            raise Blocked("RBA returned an HTML page instead of the CSV; pass --rba-file")
        retrieved = common.today()
    snap = common.save_snapshot(MARKETS_SRC / "rba", "f2-data", "csv", body, date=retrieved)
    got = parse_rba(common.decode(body).lstrip("﻿"), RBA_URL, retrieved, common.rel(snap), since)
    print("  ACGB f2-data: %d points" % len(got))
    return got


# ---------- output ----------

def merge(path, new_rows):
    existing = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    index = {tuple(r[k] for k in KEY): r for r in existing}
    added, conflicts = 0, []
    for r in new_rows:
        key = tuple(r[k] for k in KEY)
        old = index.get(key)
        if old is None:
            index[key] = r
            existing.append(r)
            added += 1
        elif float(old["value_pct"]) != float(r["value_pct"]):
            conflicts.append((key, old["value_pct"], r["value_pct"]))
    existing.sort(key=lambda r: (r["date"], int(r["tenor_years"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(existing)
    return added, conflicts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="USD,JPY,AUD", help="comma-separated currencies")
    ap.add_argument("--since", default="2019-01-01", help="first date kept (ISO)")
    ap.add_argument("--rba-file", help="a saved copy of RBA f2-data.csv to ingest")
    ap.add_argument("--rba-retrieved", help="retrieval date of --rba-file (default today)")
    args = ap.parse_args()

    collectors = {
        "USD": lambda: collect_ust(args.since),
        "JPY": lambda: collect_jgb(args.since),
        "AUD": lambda: collect_acgb(args.since, args.rba_file, args.rba_retrieved),
    }
    failed = False
    for ccy in [c.strip().upper() for c in args.only.split(",") if c.strip()]:
        print("%s:" % ccy)
        try:
            rows = collectors[ccy]()
        except Blocked as err:
            print("  blocked: %s" % err)
            failed = True
            continue
        added, conflicts = merge(MARKETS_DATA / ("govt_yield__%s.csv" % ccy), rows)
        print("  %d new points, %d conflicts" % (added, len(conflicts)))
        for key, old, new in conflicts[:20]:
            print("  conflict %s: on file %s, source now %s" % ("/".join(key), old, new))
        failed = failed or bool(conflicts)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
