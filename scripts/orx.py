"""Collector for ORIX Life (オリックス生命): 一時払終身保険 Moonshot.

Reads data/products/orx-*.json and, for each product:
  - snapshots its pages (the rate page goes to sources/orx/rates/)
  - parses the 積立利率 table of the rate page (指定通貨 x 積立利率保証期間) into
    rate rows and merges them into data/rates/
  - with --wayback, also ingests every distinct Wayback Machine capture of
    the rate page (raw captures go to sources/orx/wayback/)
  - with --docs, downloads the listed documents (not to be used by default)

The rate page shows only the rate for the current 契約日 window (about half a
month, recalculated on the 1st and 16th) and is overwritten, so history
accumulates only by re-running this script plus the Wayback captures.
effective_date is the first day of the 契約日 window. term_years holds the
積立利率保証期間 (10 years for every row seen).

Usage:
    python3 scripts/orx.py
    python3 scripts/orx.py --wayback
    python3 scripts/orx.py --file PATH      # ingest a saved copy of the rate page

Re-running is safe: unchanged pages are not saved twice and existing data
points are never rewritten. Captures that cannot be parsed reliably are
skipped and reported; gaps are never interpolated.
"""

import argparse
import csv
import gzip
import json
import re
import sys
import time
import unicodedata
import urllib.error

import common

SRC = common.SOURCES / "orx"
CDX = "https://web.archive.org/cdx/search/cdx?url=%s&output=json&collapse=digest&fl=timestamp,original,statuscode"
WAYBACK = "https://web.archive.org/web/%sid_/%s"
CURRENCY_BY_JA = {"米ドル": "USD", "円": "JPY", "豪ドル": "AUD"}


def norm(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def strip_comments(page):
    return re.sub(r"<!--[\s\S]*?-->", "", page)


def parse_value(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?", norm(text))
    if m:
        return m.group(1), "published"
    return "", "not_offered"


def parse_page(page):
    """Return (effective_date, [(currency, term, text)]) from a rate page."""
    page = strip_comments(page)
    flat = norm(common.clean(page))
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[～~〜-]\s*\d{4}年\d{1,2}月\d{1,2}日が契約日となる契約", flat)
    if not m:
        raise ValueError("契約日 window not found")
    date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    points = []
    for table in re.findall(r"<table[^>]*>[\s\S]*?</table>", page):
        rows = [[(norm(t), c, r) for t, c, r in cells] for cells in common.table_rows(table)]
        if not rows or [c[0] for c in rows[0]] != ["指定通貨", "積立利率保証期間", "積立利率"]:
            continue
        for cells in rows[1:]:
            texts = [c[0] for c in cells]
            term = re.fullmatch(r"(\d+)年", texts[1]) if len(texts) == 3 else None
            if not term or texts[0] not in CURRENCY_BY_JA:
                raise ValueError("unexpected rate row %r" % texts)
            points.append((CURRENCY_BY_JA[texts[0]], term.group(1), texts[2]))
    if not points:
        raise ValueError("no rate table recognised")
    return date, points


def rows_from_page(product, page, source_url, retrieved, snapshot, wayback):
    date, points = parse_page(page)
    series = product["rate_series"][0]
    rows = []
    for currency, term, text in points:
        if currency not in series["currencies"]:
            raise ValueError("currency %s not in rate_series" % currency)
        value, status = parse_value(text)
        rows.append(
            {
                "product_id": product["id"],
                "metric": series["metric"],
                "plan": series["plan"],
                "currency": currency,
                "term_years": term,
                "age_band": "",
                "sex": "",
                "effective_date": date,
                "value_pct": value,
                "status": status,
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if wayback else "false",
            }
        )
    return date, {series["csv"]: rows}


def merge(grouped, label):
    added_total, conflicts = 0, 0
    for name, rows in sorted(grouped.items()):
        added, bad = common.merge_rates(common.DATA / "rates" / name, rows)
        added_total += added
        conflicts += len(bad)
        for key, old, new in bad:
            print("    CONFLICT %s: on file %s, source shows %s" % (" ".join(k for k in key if k), old, new))
    print("  %-34s %3d points read, %3d new" % (label, sum(len(r) for r in grouped.values()), added_total))
    return conflicts


def snapshot_pages(product):
    rate_name = product["rate_page"]
    snapshot = None
    for page in product.get("pages", []):
        try:
            body = common.fetch(page["url"])
        except Exception as err:  # a page that refuses automated reads is reported, not fatal
            print("  page  %s UNREACHABLE: %s" % (page["name"], err))
            if page["name"] == rate_name:
                raise
            continue
        folder = "rates" if page["name"] == rate_name else "pages"
        path = common.save_snapshot(SRC / folder, page["name"], "html", body)
        print("  page  %s" % common.rel(path))
        if page["name"] == rate_name:
            snapshot = (page["url"], body, path)
    return snapshot


def collect_current(product, snapshot):
    url, body, path = snapshot
    date, grouped = rows_from_page(product, common.decode(body), url, common.today(), path, False)
    print("  current 契約日 window starting %s" % date)
    return merge(grouped, "current window")


def wayback_captures(url):
    rows = json.loads(common.fetch(CDX % url.replace("https://", "")))
    return [(r[0], r[1]) for r in rows[1:] if r[2] == "200"]


def fetch_wayback(url, tries=3):
    for attempt in range(tries):
        try:
            return common.fetch(url, pause=3.0)
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            time.sleep(60 * (attempt + 1))
        except RuntimeError:  # connection refused or timed out: wait, then give up on this capture
            time.sleep(30 * (attempt + 1))
    return None


def collect_wayback(product):
    rate_url = next(p["url"] for p in product["pages"] if p["name"] == product["rate_page"])
    conflicts = 0
    for ts, original in wayback_captures(rate_url):
        path = SRC / "wayback" / ("accumulation-interest-rate_%s.html" % ts)
        wb_url = WAYBACK % (ts, original)
        if path.exists():
            body = path.read_bytes()
        else:
            body = fetch_wayback(wb_url)
            if body is None:
                print("  wayback %s SKIPPED (Wayback Machine refused or unreachable; re-run to retry)" % ts)
                continue
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        try:
            eff, grouped = rows_from_page(product, common.decode(body), wb_url, common.today(), path, True)
        except (ValueError, KeyError) as err:
            print("  wayback %s SKIPPED (cannot parse reliably): %s" % (ts, err))
            continue
        print("  wayback %s window starting %s" % (ts, eff))
        conflicts += merge(grouped, "wayback " + ts)
    return conflicts


def download_documents(product):
    folder = SRC / "docs" / product["id"]
    for doc in product.get("documents", []):
        body = common.fetch(doc["url"], timeout=120)
        if not body.startswith(b"%PDF"):
            print("  SKIPPED (not a PDF): %s" % doc["url"])
            continue
        print("  doc   %s" % common.rel(common.save_snapshot(folder, doc["name"], "pdf", body)))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--docs", action="store_true", help="also download the product documents")
    parser.add_argument("--wayback", action="store_true", help="also ingest Wayback captures of the rate page")
    parser.add_argument("--file", help="ingest a saved copy of the rate page instead of fetching it")
    args = parser.parse_args()

    conflicts = 0
    for file in sorted((common.DATA / "products").glob("orx-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        print("%s  %s" % (product["id"], product["name_ja"]))
        if args.file:
            from pathlib import Path

            path = Path(args.file).resolve()
            url = next(p["url"] for p in product["pages"] if p["name"] == product["rate_page"])
            conflicts += collect_current(product, (url, path.read_bytes(), path))
        else:
            try:
                snapshot = snapshot_pages(product)
            except RuntimeError as err:
                print("  BLOCKED: %s\n  Save the page elsewhere and use --file." % err)
                sys.exit(2)
            conflicts += collect_current(product, snapshot)
        if args.wayback:
            conflicts += collect_wayback(product)
        if args.docs:
            download_documents(product)
    if conflicts:
        print("%d conflicts: the source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
