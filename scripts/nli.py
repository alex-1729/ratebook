"""Collector for Nippon Life (日本生命保険相互会社), window-sales product
ロングドリームＧＯＬＤ３ (ニッセイ指定通貨建積立利率変動型一時払終身保険).

For each data/products/nli-*.json:
  - snapshots the listed pages (the rate page goes to sources/nli/rates/)
  - parses the CURRENT 契約日 period of the rate page (ふやすタイプ and
    うけとるタイプ tables) into rate rows and merges them into data/rates/
  - with --wayback, also ingests distinct Wayback Machine captures of the rate
    page (raw captures go to sources/nli/wayback/); captures that are not the
    GOLD3 layout, or that the archive refuses to serve, are skipped and reported
  - with --docs, downloads the listed documents (not to be run by agents here)

The rate page shows only the current half-month period (the 1st-15th or the
16th-end) and is overwritten on the 1st and 16th, so history accumulates only
by running this regularly. effective_date is the first day of the 契約日 range.
Rates are guaranteed 10 years (term_years = 10) and are reset at each 10th
policy anniversary; 契約日 age band is the age at the contract date.

The live page is served as old upper-case HTML; the parser lower-cases tags,
so saved snapshots and live pages are handled identically.

Usage:
    python3 scripts/nli.py
    python3 scripts/nli.py --wayback
    python3 scripts/nli.py --only nli-longdream-gold3
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

SRC = common.SOURCES / "nli"
CDX = "https://web.archive.org/cdx/search/cdx?url=%s&output=json&collapse=digest"
WAYBACK = "https://web.archive.org/web/%sid_/%s"
CURRENCY_BY_JA = {"米ドル": "USD", "豪ドル": "AUD"}
RIDER = {"付加する": "rider", "付加しない": "no_rider"}
PAYOUT = {"指定通貨で10年一定": "fx_const", "円で10年一定": "yen_const"}
AGE = {"15歳~75歳": "15-75", "76歳~90歳": "76-90"}  # keys are NFKC-normalised (～ -> ~)


def norm(text):
    """Full-width to ASCII (NFKC), whitespace collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def parse_value(text):
    """'４．４３％' -> ('4.43', 'published'); '－' -> ('', 'not_offered'); '＊' -> None."""
    t = norm(text)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return m.group(1), "published"
    if t in ("-", "−", "ー", "－", "―"):
        return "", "not_offered"
    if t == "*":  # 未判明 (not yet determined by the system): no data point
        return None
    raise ValueError("unrecognised rate cell %r" % text)


def prepare(page):
    """Drop comments and lower-case tag and attribute names so common.table_rows works."""
    page = re.sub(r"<!--[\s\S]*?-->", "", page)
    page = re.sub(r"(</?)([A-Za-z][A-Za-z0-9]*)", lambda m: m.group(1) + m.group(2).lower(), page)
    return re.sub(r"\b(ROWSPAN|COLSPAN)=", lambda m: m.group(1).lower() + "=", page)


def parse_period(text):
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日[～~]\d{4}年\d{1,2}月\d{1,2}日をご契約日とする", norm(text))
    if not m:
        raise ValueError("契約日 period not found")
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def gold3_section(page):
    """The part of the page for ロングドリームＧＯＬＤ３ (anchor name GOLD3)."""
    page = prepare(page)
    i = page.find('<a name="GOLD3"')
    if i < 0:
        raise ValueError("GOLD3 section not found")
    return page[i:]


def parse_page(page):
    """Return (effective_date, points). A point: metric, currency, age_band, plan, text."""
    section = prepare(gold3_section(page))
    tables = re.findall(r'<table class="bordergray00"[\s\S]*?</table>', section)
    if len(tables) < 2:
        raise ValueError("expected the ふやす and うけとる tables, found %d" % len(tables))
    # one period line precedes each table; both must agree
    periods = {parse_period(common.clean(s)) for s in re.findall(r"[^<>]*をご契約日とするご契約について[^<>]*", section)}
    if len(periods) != 1:
        raise ValueError("period lines disagree: %r" % sorted(periods))
    date = periods.pop()
    points = []
    for kind, table in zip(("fuyasu", "uketoru"), tables[:2]):
        rows = [[(norm(t), c, r) for t, c, r in cells] for cells in common.table_rows(table)]
        header = [c[0] for c in rows[0]]
        if kind == "fuyasu":
            want = ["指定通貨", "被保険者の契約日における満年齢", "円建死亡保険金特約", "積立利率", "【ご参考】実質的な利回り"]
            metrics = ["crediting_rate", "net_yield"]
            labels = RIDER
        else:
            want = ["指定通貨", "被保険者の契約日における満年齢", "定期支払金の受取方法", "積立利率", "定期支払率", "【ご参考】実質的な利回り"]
            metrics = ["crediting_rate", "payment_rate", "net_yield"]
            labels = PAYOUT
        if header != [norm(w) for w in want]:
            raise ValueError("unexpected %s header %r" % (kind, header))
        currency = age = None
        cur_left = age_left = 0
        for cells in rows[1:]:
            cells = list(cells)
            if cur_left <= 0:
                text, _, span = cells.pop(0)
                currency, cur_left = CURRENCY_BY_JA[text], span
            if age_left <= 0:
                text, _, span = cells.pop(0)
                age, age_left = AGE[text], span
            label = cells.pop(0)[0]
            if len(cells) != len(metrics):
                raise ValueError("row has %d values, expected %d" % (len(cells), len(metrics)))
            for metric, cell in zip(metrics, cells):
                points.append(
                    {
                        "metric": metric,
                        "currency": currency,
                        "age_band": age,
                        "plan": "%s_%s" % (kind, labels[label]),
                        "text": cell[0],
                    }
                )
            cur_left -= 1
            age_left -= 1
    return date, points


def series_for(product, point):
    for series in product["rate_series"]:
        if series["metric"] == point["metric"] and series["plan"] == point["plan"]:
            return series
    raise ValueError("no rate_series matches %r" % point)


def rows_from_page(product, page, source_url, retrieved, snapshot, wayback):
    date, points = parse_page(page)
    grouped, unknown = {}, 0
    for p in points:
        parsed = parse_value(p["text"])
        if parsed is None:
            unknown += 1
            continue
        value, status = parsed
        series = series_for(product, p)
        grouped.setdefault(series["csv"], []).append(
            {
                "product_id": product["id"],
                "metric": p["metric"],
                "plan": series["plan"],
                "currency": p["currency"],
                "term_years": "10",
                "age_band": p["age_band"],
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
    if unknown:
        print("  %d cells shown as ＊ (not yet determined) were not recorded" % unknown)
    return date, grouped


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
        body = common.fetch(page["url"])
        folder = "rates" if page["name"] == rate_name else "pages"
        path = common.save_snapshot(SRC / folder, page["name"], "html", body)
        print("  page  %s" % common.rel(path))
        if page["name"] == rate_name:
            snapshot = (page["url"], body, path)
    return snapshot


def collect_current(product, snapshot):
    url, body, path = snapshot
    date, grouped = rows_from_page(product, common.decode(body), url, common.today(), path, False)
    print("  current period starting %s" % date)
    return merge(grouped, "current period")


def wayback_captures(url):
    raw = common.fetch(CDX % url.replace("https://", ""))
    return [(r[1], r[2]) for r in json.loads(raw)[1:] if r[4] == "200"]


def fetch_wayback(url, tries=2):
    """Fetch a capture; None if the archive keeps refusing (429) or is unreachable."""
    for attempt in range(tries):
        try:
            return common.fetch(url, pause=3.0, retries=1)
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            time.sleep(60 * (attempt + 1))
        except RuntimeError:
            return None
    return None


def collect_wayback(product):
    rate_url = next(p["url"] for p in product["pages"] if p["name"] == product["rate_page"])
    conflicts = 0
    try:
        captures = wayback_captures(rate_url)
    except (RuntimeError, urllib.error.HTTPError) as err:
        print("  wayback index unavailable: %s" % err)
        return 0
    for ts, original in captures:
        folder = SRC / "wayback"
        path = folder / ("rate_page_%s.html" % ts)
        wb_url = WAYBACK % (ts, original)
        if path.exists():
            body = path.read_bytes()
        else:
            body = fetch_wayback(wb_url)
            if body is None:
                print("  wayback %s SKIPPED (Wayback Machine refused or unreachable)" % ts)
                continue
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        try:
            eff, grouped = rows_from_page(product, common.decode(body), wb_url, common.today(), path, True)
        except (ValueError, KeyError) as err:
            print("  wayback %s SKIPPED (cannot parse reliably): %s" % (ts, err))
            continue
        print("  wayback %s period starting %s" % (ts, eff))
        conflicts += merge(grouped, "wayback " + ts)
    return conflicts


def download_documents(product, manifest):
    folder = SRC / "docs" / product["id"]
    for doc in product.get("documents", []):
        body = common.fetch(doc["url"], timeout=120)
        if not body.startswith(b"%PDF"):
            print("  SKIPPED (not a PDF): %s" % doc["url"])
            continue
        path = common.save_snapshot(folder, doc["name"], "pdf", body)
        manifest[(product["id"], doc["name"], common.rel(path))] = {
            "product_id": product["id"],
            "name": doc["name"],
            "title_ja": doc["title_ja"],
            "url": doc["url"],
            "file": common.rel(path),
            "bytes": str(len(body)),
            "sha256": common.sha256(body),
            "retrieved_date": path.stem.rsplit("_", 1)[1],
        }
        print("  doc   %s (%.1f MB)" % (common.rel(path), len(body) / 1e6))


def load_manifest(path):
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {(r["product_id"], r["name"], r["file"]): r for r in csv.DictReader(fh)}


def write_manifest(path, manifest):
    fields = ["product_id", "name", "title_ja", "url", "file", "bytes", "sha256", "retrieved_date"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for key in sorted(manifest):
            writer.writerow(manifest[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--docs", action="store_true", help="also download the product documents")
    parser.add_argument("--wayback", action="store_true", help="also ingest Wayback captures of the rate page")
    parser.add_argument("--only", help="collect a single product id")
    args = parser.parse_args()

    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    conflicts = 0
    for file in sorted((common.DATA / "products").glob("nli-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        try:
            snapshot = snapshot_pages(product)
        except (RuntimeError, urllib.error.HTTPError) as err:
            print("  FAILED to read the pages: %s" % err)
            print("  The Nippon Life rate page could not be fetched; no rates collected.")
            sys.exit(2)
        conflicts += collect_current(product, snapshot)
        if args.wayback:
            conflicts += collect_wayback(product)
        if args.docs:
            download_documents(product, manifest)
    if args.docs:
        write_manifest(manifest_path, manifest)
    if conflicts:
        print("%d conflicts: the source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
