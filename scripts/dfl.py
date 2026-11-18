"""Collector for Dai-ichi Frontier Life (第一フロンティア生命).

Reads every data/products/dfl-*.json and, for each product:
  - snapshots its pages (product page, current rate page, MVA page)
  - collects each rate series from the insurer's "rates since launch" table
  - with --docs, downloads the listed documents

Usage:
    python3 scripts/dfl.py            # pages and rates
    python3 scripts/dfl.py --docs     # pages, rates and documents
    python3 scripts/dfl.py --only dfl-premier-currency-3

Raw snapshots go to sources/dfl/, rates to data/rates/. Re-running is safe:
unchanged pages are not saved twice and existing data points are never
rewritten.
"""

import argparse
import csv
import json
import re
import sys

import common

BASE = "https://www.d-frontier-life.co.jp"
HISTORY_URL = BASE + "/customer/rate/tsumitateriritsu_history.html?param=%s&product=&agency="
SRC = common.SOURCES / "dfl"


def _number_band(label):
    """'15・20年' -> '15-20', '25・30・35・40年' -> '25-40', '1～4年' -> '1-4', '110%' -> '110'."""
    label = label.replace("年", "").replace("%", "").replace("歳", "").strip()
    parts = re.split(r"[・～]", label)
    return parts[0] if len(parts) == 1 else "%s-%s" % (parts[0], parts[-1])


def _count(label):
    match = re.fullmatch(r"(\d+)回", label)
    return match.group(1) if match else ""


def parse_layout(page, layout, table=0):
    """Parse the history tables whose headers carry more than currency and term.

    Every column becomes dims {currency, term_years, age_band, plan_suffix}. term_years
    is only ever the tenor (deferral or rate-guarantee years) and age_band only an
    issue-age band; any other header dimension goes into the plan key as plan_suffix,
    which the collector appends to the series' plan_base.

    annuity24_kakutei: currency / payout period / deferral period (both bands, e.g. 1～4年)
        -> plan_suffix "pay<payout band>_def<deferral band>", term_years blank (a band
           is not a tenor); 25・30・35・40年 is written 25-40
    annuity24_shushin: currency / total-payout guarantee ratio / issue age
        -> plan_suffix "g<percent>", age_band "<issue-age band>", term_years blank
    payouts: currency / (whole-life cover multiple, 終身保障あり tables only) / number of
        survival-benefit payouts / (積立利率保証期間, where printed)
        -> term_years = 積立利率保証期間; where the table does not print it (the
           終身保障なし tables) it is the number of payouts minus one, as the rate page
           states for every column; plan_suffix "x<multiple>_n<payouts>" where a cover
           multiple is printed, else none (the term then identifies the column)
    suppression: currency / guarantee term / death-benefit suppression period (保障抑制期間)
        -> term_years = guarantee years, plan_suffix "holdback_9m" or "holdback_5y"
    `table` picks one of several tables on the same page, in page order.
    """
    tables = re.findall(r"<table[\s\S]*?</table>", page)
    rows = common.table_rows(tables[table])
    is_date = lambda cells: re.fullmatch(r"\d{2}/\d{2}/\d{2}", cells[0][0]) is not None
    first = next(i for i, cells in enumerate(rows) if is_date(cells))
    columns = []
    for labels in common.expand_header(rows[:first])[1:]:
        dims = {"currency": common.CURRENCIES.get(labels[0], ""), "term_years": "", "age_band": "", "plan_suffix": ""}
        if layout == "annuity24_kakutei":
            dims["plan_suffix"] = "pay%s_def%s" % (_number_band(labels[1]), _number_band(labels[2]))
        elif layout == "annuity24_shushin":
            if not labels[2].endswith("歳"):
                raise ValueError("cannot read column header %r" % (labels,))
            dims["plan_suffix"] = "g%s" % _number_band(labels[1])
            dims["age_band"] = _number_band(labels[2])
        elif layout == "suppression":
            dims["term_years"] = _number_band(labels[1])
            period = re.fullmatch(r"(\d+)(ヵ月|年)", labels[2])
            if not (period and dims["term_years"].isdigit()):
                raise ValueError("cannot read column header %r" % (labels,))
            dims["plan_suffix"] = "holdback_%s%s" % (period.group(1), "m" if period.group(2) == "ヵ月" else "y")
        elif layout == "payouts":
            count, multiple, guarantee = "", "", ""
            for label in labels[1:]:
                if _count(label):
                    count = _count(label)
                elif re.fullmatch(r"\d+倍", label):
                    multiple = label[:-1]  # 終身保障倍率 (whole-life cover multiple)
                elif re.fullmatch(r"\d+年", label):
                    guarantee = label[:-1]  # 積立利率保証期間
            if not count:
                raise ValueError("cannot read column header %r" % (labels,))
            dims["term_years"] = guarantee or str(int(count) - 1)
            dims["plan_suffix"] = "x%s_n%s" % (multiple, count) if multiple else ""
        columns.append(dims)
    out = []
    for cells in rows[first:]:
        if not is_date(cells):
            continue
        values = [c[0] for c in cells[1:]]
        if len(values) != len(columns):
            raise ValueError("row %s has %d values for %d columns" % (cells[0][0], len(values), len(columns)))
        yy, mm, dd = cells[0][0].split("/")
        for dims, value in zip(columns, values):
            out.append(("20%s-%s-%s" % (yy, mm, dd), dims, value))
    return out


def plan_of(series, dims):
    """A layout series names its plan as plan_base plus the column's plan_suffix."""
    suffix = dims.get("plan_suffix", "")
    if "plan_base" not in series:
        return series["plan"]
    return series["plan_base"] + ("_" + suffix if suffix else "")


def parse_series(series, page):
    """Parse one history page as the series reads it: [(date, dims, text, plan)]."""
    if series.get("layout"):
        parsed = parse_layout(page, series["layout"], series.get("table", 0))
    else:
        parsed = common.parse_history_table(page)
    return [(date, dims, text, plan_of(series, dims)) for date, dims, text in parsed]


def rows_for_series(product, series, page, url, retrieved, snapshot):
    """The rate rows of one series from a history page (offline: no fetching)."""
    rows = []
    for date, dims, text, plan in parse_series(series, page):
        if plan != series["plan"]:
            continue  # another column group of the same table: its own series
        offered = text not in ("", "-", "－")
        rows.append(
            {
                "product_id": product["id"],
                "metric": series["metric"],
                "plan": plan,
                "currency": dims["currency"],
                # A series can fix the term when the table has no term header.
                "term_years": dims["term_years"] or series.get("term_years", ""),
                "age_band": dims["age_band"],
                "sex": series.get("sex", ""),
                "effective_date": date,
                "value_pct": text if offered else "",
                "status": "published" if offered else "not_offered",
                "source_url": url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "false",
            }
        )
    return rows


def fetch_history(param, cache):
    """Fetch and snapshot one history page once per run (several series share a page)."""
    if param not in cache:
        url = HISTORY_URL % param
        body = common.fetch(url)
        snapshot = common.save_snapshot(SRC / "rates", "history_" + param, "html", body)
        cache[param] = (url, common.decode(body), snapshot)
    return cache[param]


def unlisted_columns(product, cache):
    """Column groups a history page shows that no series of the product collects."""
    out = []
    listed = {(s["metric"], s["plan"]) for s in product.get("rate_series", [])}
    for series in product.get("rate_series", []):
        if "plan_base" not in series or series["param"] not in cache:
            continue
        for _, _, _, plan in parse_series(series, cache[series["param"]][1]):
            if (series["metric"], plan) not in listed and (series["metric"], plan) not in out:
                out.append((series["metric"], plan))
    return out


def collect_series(product, series, cache):
    url, page, snapshot = fetch_history(series["param"], cache)
    retrieved = common.today()
    rows = rows_for_series(product, series, page, url, retrieved, snapshot)
    if not rows:
        print("  %-34s NO POINTS: the page has no column for plan %s" % (series["csv"], series["plan"]))
        return 1
    csv_path = common.DATA / "rates" / series["csv"]
    added, conflicts = common.merge_rates(csv_path, rows)
    dates = sorted({r["effective_date"] for r in rows})
    print("  %-34s %4d points read, %4d new, %s to %s" % (series["csv"], len(rows), added, dates[0], dates[-1]))
    for key, old, new in conflicts:
        print("    CONFLICT %s: on file %s, site now shows %s" % (" ".join(k for k in key if k), old, new))
    return len(conflicts)


def snapshot_pages(product):
    for page in product.get("pages", []):
        body = common.fetch(page["url"])
        path = common.save_snapshot(SRC / "pages", page["name"], "html", body)
        print("  page  %s" % common.rel(path))


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
    parser.add_argument("--only", help="collect a single product id")
    args = parser.parse_args()

    files = sorted((common.DATA / "products").glob("dfl-*.json"))
    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    conflicts = 0
    for file in files:
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        snapshot_pages(product)
        cache = {}
        for series in product.get("rate_series", []):
            conflicts += collect_series(product, series, cache)
        for metric, plan in unlisted_columns(product, cache):
            print("  NEW COLUMNS not collected: %s plan %s has no rate_series entry (add one)" % (metric, plan))
        if args.docs:
            download_documents(product, manifest)
    if args.docs:
        write_manifest(manifest_path, manifest)
    if conflicts:
        print("%d conflicts: the site shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
