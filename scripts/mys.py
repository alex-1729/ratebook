"""Collector for Meiji Yasuda Life (明治安田生命), bank-channel product
外貨建・エブリバディプラス.

Reads data/products/mys-*.json and, for each product:
  - snapshots its pages (the window list, edition pages and the rate page)
  - fetches the live 予定利率 page (one page shared by every bank edition) and parses it
  - with --wayback, ingests the Wayback Machine captures of that page
  - with --file, ingests a saved copy of the rate page (for example one read
    in a browser when the site does not answer scripts)

The rate page shows only the current half-month window (契約日 1st-15th or
16th-end of month) and is overwritten on the 1st and 16th, so history grows
only by re-running this script. The page lists two types (運用重視 and
バランス), each with USD and AUD by contract-age band, plus the 適用する期間
(the 10-year rate-reset period), stored here as term_years.

Usage:
    python3 scripts/mys.py
    python3 scripts/mys.py --wayback
    python3 scripts/mys.py --file saved.html --retrieved 2026-11-17

Re-running is safe: unchanged snapshots are not saved twice and existing data
points are never rewritten. A page whose layout does not match is skipped and
reported, never guessed at. Documents are never downloaded (URLs are listed in
the product JSON only).
"""

import argparse
import json
import re
import sys
import time
import urllib.error
from pathlib import Path

import common

SRC = common.SOURCES / "mys"
WAYBACK = "https://web.archive.org/web/%sid_/%s"
CDX = "https://web.archive.org/cdx/search/cdx?url=%s&output=json&collapse=digest"
CURRENCY = {"米ドル建": "USD", "豪ドル建": "AUD"}
TYPES = {"■運用重視タイプ": "unyou", "■バランスタイプ": "balance"}
WINDOW = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日～(\d{4})年(\d{1,2})月(\d{1,2})日が契約日")
AGE = re.compile(r"(\d+)歳～(\d+)歳")
PCT = re.compile(r"(\d+\.\d+)[％%]")


class LayoutError(Exception):
    pass


def iso(y, m, d):
    return "%s-%02d-%02d" % (y, int(m), int(d))


def parse_rate_page(page):
    """Return (effective_date, window_end, [(plan, currency, term, age_band, text)])."""
    m = WINDOW.search(page)
    if not m:
        raise LayoutError("no contract-date window sentence")
    effective = iso(*m.group(1, 2, 3))
    window_end = iso(*m.group(4, 5, 6))
    marks = sorted((page.find(label), label) for label in TYPES if page.find(label) >= 0)
    if len(marks) != len(TYPES):
        raise LayoutError("type headings missing (found %s)" % [l for _, l in marks])
    points = []
    for n, (pos, label) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(page)
        section = page[pos:end]
        tables = re.findall(r"<table[\s\S]*?</table>", section)
        if not tables:
            raise LayoutError("%s: no table" % label)
        rows = common.table_rows(tables[0])
        currency, term, seen = None, None, 0
        for cells in rows:
            texts = [c[0] for c in cells]
            if texts[0] in CURRENCY:
                currency = CURRENCY[texts[0]]
                texts = texts[1:]
                cells = cells[1:]
            if not texts or not AGE.fullmatch(texts[0]):
                continue
            if currency is None:
                raise LayoutError("%s: age row before any currency" % label)
            lo, hi = AGE.fullmatch(texts[0]).groups()
            value = texts[1] if len(texts) > 1 else ""
            if len(texts) > 2:
                pm = re.fullmatch(r"(\d+)年", texts[2])
                if not pm:
                    raise LayoutError("%s: odd period %r" % (label, texts[2]))
                term = pm.group(1)
            if term is None:
                raise LayoutError("%s: no 適用する期間" % label)
            if not PCT.fullmatch(value) and value not in ("-", "－", "ー"):
                raise LayoutError("%s/%s: odd value %r" % (label, currency, value))
            points.append((TYPES[label], currency, term, "%s-%s" % (lo, hi), value))
            seen += 1
        if seen == 0:
            raise LayoutError("%s: no rate rows" % label)
    if {p[1] for p in points} != {"USD", "AUD"}:
        raise LayoutError("expected USD and AUD")
    return effective, window_end, points


def write_points(product, effective, points, source_url, snapshot, via_wayback, retrieved=None):
    retrieved = retrieved or common.today()
    csv_of = {s["plan"]: s["csv"] for s in product["rate_series"]}
    by_plan = {}
    for plan, currency, term, age, text in points:
        pm = PCT.fullmatch(text)
        by_plan.setdefault(plan, []).append(
            {
                "product_id": product["id"],
                "metric": "planned_rate",
                "plan": plan,
                "currency": currency,
                "term_years": term,
                "age_band": age,
                "sex": "",
                "effective_date": effective,
                "value_pct": pm.group(1) if pm else "",
                "status": "published" if pm else "not_offered",
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if via_wayback else "false",
            }
        )
    added, conflicts = 0, []
    for plan, rows in sorted(by_plan.items()):
        a, c = common.merge_rates(common.DATA / "rates" / csv_of[plan], rows)
        added += a
        conflicts += c
    return added, conflicts


def report(label, effective, window_end, points, added, conflicts):
    print("  %-22s window %s to %s, %3d points read, %3d new" % (label, effective, window_end, len(points), added))
    for key, old, new in conflicts:
        print("    CONFLICT %s: on file %s, source now shows %s" % (" ".join(k for k in key if k), old, new))
    return len(conflicts)


def snapshot_pages(product):
    for page in product.get("pages", []):
        try:
            body = common.fetch(page["url"], retries=2, timeout=40)
        except (RuntimeError, urllib.error.HTTPError) as err:
            print("  page  NOT REACHABLE %s (%s)" % (page["url"], err))
            continue
        path = common.save_snapshot(SRC / "pages", page["name"], "html", body)
        print("  page  %s" % common.rel(path))


def collect_live(product):
    src = product["rate_source"]
    try:
        body = common.fetch(src["live_url"], retries=3, timeout=60)
    except (RuntimeError, urllib.error.HTTPError) as err:
        print("  live rate page NOT REACHABLE (%s); nothing collected from it. Not worked around." % err)
        return 0
    path = common.save_snapshot(SRC / "rates", src["name"], "html", body)
    try:
        effective, window_end, points = parse_rate_page(common.decode(body))
    except LayoutError as err:
        print("  live rate page saved to %s but SKIPPED, layout not parsed: %s" % (common.rel(path), err))
        return 0
    added, conflicts = write_points(product, effective, points, src["live_url"], path, False)
    return report("live", effective, window_end, points, added, conflicts)


def collect_file(product, file, retrieved):
    src = product["rate_source"]
    body = Path(file).read_bytes()
    path = common.save_snapshot(SRC / "rates", src["name"], "html", body, date=retrieved)
    try:
        effective, window_end, points = parse_rate_page(common.decode(body))
    except LayoutError as err:
        print("  saved copy filed as %s but SKIPPED, layout not parsed: %s" % (common.rel(path), err))
        return 0
    added, conflicts = write_points(product, effective, points, src["live_url"], path, False, retrieved)
    return report("saved copy", effective, window_end, points, added, conflicts)


def wayback_fetch(url):
    for attempt in range(3):
        try:
            return common.fetch(url, timeout=90, pause=5.0)
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == 2:
                raise
            print("  web.archive.org says too many requests; waiting 90 s")
            time.sleep(90)


def collect_wayback(product):
    src = product["rate_source"]
    try:
        listing = json.loads(wayback_fetch(CDX % src["cdx_url"]).decode("utf-8"))
    except (RuntimeError, urllib.error.URLError, ValueError) as err:
        print("  wayback index NOT REACHABLE (%s)" % err)
        return 0
    head, rows = listing[0], listing[1:]
    conflicts = 0
    for row in rows:
        rec = dict(zip(head, row))
        if rec["statuscode"] != "200":
            print("  wayback %s skipped: HTTP %s" % (rec["timestamp"], rec["statuscode"]))
            continue
        url = WAYBACK % (rec["timestamp"], rec["original"])
        try:
            body = wayback_fetch(url)
        except (urllib.error.URLError, RuntimeError) as err:
            print("  wayback %s NOT FETCHED (%s)" % (rec["timestamp"], err))
            continue
        path = common.save_snapshot(SRC / "wayback", "%s_%s" % (src["name"], rec["timestamp"]), "html", body)
        try:
            effective, window_end, points = parse_rate_page(common.decode(body))
        except LayoutError as err:
            print("  wayback %s SKIPPED, layout not parsed: %s" % (rec["timestamp"], err))
            continue
        added, found = write_points(product, effective, points, url, path, True)
        conflicts += report("wayback " + rec["timestamp"], effective, window_end, points, added, found)
    return conflicts


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--docs", action="store_true", help="ignored: documents are not downloaded for this insurer")
    parser.add_argument("--wayback", action="store_true", help="also ingest Wayback captures of the rate page")
    parser.add_argument("--no-live", action="store_true", help="do not contact the live rate page")
    parser.add_argument("--only", help="collect a single product id")
    parser.add_argument("--file", help="ingest a saved copy of the rate page (needs --only and --retrieved)")
    parser.add_argument("--retrieved", help="date (YYYY-MM-DD) the --file copy was saved")
    args = parser.parse_args()
    if args.file and not (args.only and args.retrieved):
        parser.error("--file needs --only and --retrieved")
    conflicts = 0
    for file in sorted((common.DATA / "products").glob("mys-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        if args.file:
            conflicts += collect_file(product, args.file, args.retrieved)
            continue
        snapshot_pages(product)
        if not args.no_live:
            conflicts += collect_live(product)
        if args.wayback:
            conflicts += collect_wayback(product)
    if conflicts:
        print("%d conflicts: a source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
