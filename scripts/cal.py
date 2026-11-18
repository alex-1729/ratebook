"""Collector for Credit Agricole Life Japan (クレディ・アグリコル生命).

Reads every data/products/cal-*.json and, for each product:
  - snapshots its pages (product page, rate page, edition pages)
  - parses the rate page into rate rows and merges them into data/rates/:
      crediting_rate      積立利率 for the contract month
      base_index_rate     基準指標金利 for the same contract month and period
        term_years is blank: the deferral is fixed at 2 years, but the rate
        set at contract applies through deferral AND payout (22 or 32 years
        in all), so no tenor is comparable with other products and the rates
        stay off tenor boards (changed 2026-11-17; earlier rows had 2);
        plan = 年金支払期間 (payout period), e.g. payout_20y
      mva_reference_rate  調整用基準指標金利 used for the market value adjustment
                          on surrender / lump-sum payment of the basic annuity in a
                          given month. It has no tenor (term_years blank); the
                          plan carries the payout period and the 残存年数
                          (remaining years) column, e.g. payout_20y_remaining_12y
    The page shows the current contract month plus the previous six months of
    積立利率 and 基準指標金利 (three in older captures). effective_date is the
    first day of the contract (or surrender) month.
  - checks every product "edition" (same policy form, other marketed name) against
    the primary product: its own rate page must show identical values, otherwise
    the run reports a conflict. Editions get their own snapshots.
  - with --wayback, also ingests every distinct Wayback Machine capture of the
    rate pages (raw captures go to sources/cal/wayback/)
  - with --docs, downloads the listed documents (not to be used in this project's
    current phase)

Usage:
    python3 scripts/cal.py
    python3 scripts/cal.py --wayback
    python3 scripts/cal.py --only cal-jibun-nenkin-kaigo-plus

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

SRC = common.SOURCES / "cal"
CDX = "https://web.archive.org/cdx/search/cdx?url=%s&output=json&collapse=digest"
WAYBACK = "https://web.archive.org/web/%sid_/%s"


class LayoutError(Exception):
    pass


def norm(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def parse_value(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", norm(text))
    if m:
        return m.group(1), "published"
    if norm(text) in ("-", "－", "ー", ""):
        return "", "not_offered"
    raise LayoutError("unreadable rate cell %r" % text)


def years(text):
    m = re.fullmatch(r"(\d+)年", norm(text))
    if not m:
        raise LayoutError("expected N年, got %r" % text)
    return m.group(1)


def sections(page):
    """Split the page body into {h2 title: html} (comments removed)."""
    page = re.sub(r"<!--[\s\S]*?-->", "", page)
    parts = re.split(r"<h2[^>]*>", page)[1:]
    out = {}
    for part in parts:
        title, _, body = part.partition("</h2>")
        out[norm(re.sub(r"<[^>]+>", "", title))] = body
    return out


DEFERRAL = "2"  # 据置期間: the only one the product offers; parse_current checks it
# term_years written for crediting_rate / base_index_rate. Blank on purpose: the rate applies for
# 2 + 20 or 2 + 30 years (deferral plus payout), which matches no other product's tenor.
BOARD_TERM = ""


def payout_plan(pay):
    return "payout_%sy" % pay


def mva_plan(pay, remaining):
    return "payout_%sy_remaining_%sy" % (pay, remaining)


def parse_current(body):
    m = re.search(r"契約日が(\d{4})年(\d{1,2})月(\d{1,2})日[～~](\d{4})年(\d{1,2})月", norm(common.clean(body)))
    if not m:
        raise LayoutError("contract-month sentence not found")
    date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    if date[8:] != "01":
        raise LayoutError("window does not start on the 1st: %s" % date)
    points = []
    for cells in common.table_rows(body):
        texts = [c[0] for c in cells]
        if len(texts) == 4 and re.fullmatch(r"\d+年", norm(texts[0])):
            if years(texts[0]) != DEFERRAL:
                raise LayoutError("unexpected deferral period %r" % texts[0])
            pay = years(texts[1])
            points.append((date, "crediting_rate", payout_plan(pay), BOARD_TERM, texts[2]))
            points.append((date, "base_index_rate", payout_plan(pay), BOARD_TERM, texts[3]))
    if not points:
        raise LayoutError("no current-rate rows")
    return date, points


def parse_past(body):
    points = []
    pay = None
    for cells in common.table_rows(body):
        texts = [c[0] for c in cells]
        if len(texts) == 4 and re.fullmatch(r"\d+年", norm(texts[0])):
            pay = years(texts[0])
            texts = texts[1:]
        if len(texts) != 3 or pay is None:
            continue
        m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2}) ?[～~] ?(\d{4})/(\d{1,2})/(\d{1,2})", norm(texts[0]))
        if not m:
            continue
        if m.group(3) != "1" or (m.group(1), m.group(2)) != (m.group(4), m.group(5)):
            raise LayoutError("past window is not one calendar month: %r" % texts[0])
        date = "%s-%02d-01" % (m.group(1), int(m.group(2)))
        # the past table has no deferral column; the product has one deferral only
        points.append((date, "crediting_rate", payout_plan(pay), BOARD_TERM, texts[1]))
        points.append((date, "base_index_rate", payout_plan(pay), BOARD_TERM, texts[2]))
    return points


def parse_adjustment(body):
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日[～~].*?が解約日", norm(common.clean(body)))
    if not m or m.group(3) != "1":
        raise LayoutError("surrender-month sentence not found")
    date = "%s-%02d-01" % (m.group(1), int(m.group(2)))
    blocks = re.split(r"<h3[^>]*>", body)[1:]
    if not blocks:
        raise LayoutError("no 年金支払期間 blocks")
    seen = {}
    for block in blocks:
        title, _, rest = block.partition("</h3>")
        t = re.search(r"年金支払期間(\d+)年の場合", norm(re.sub(r"<[^>]+>", "", title)))
        if not t:
            raise LayoutError("unexpected block title %r" % title)
        pay = t.group(1)
        for tr in common.table_rows(rest):
            label = norm(tr[0][0])
            if label == "残存年数":
                remaining = [years(c[0]) for c in tr[1:]]
            elif label == "調整用基準指標金利":
                values = [c[0] for c in tr[1:]]
                if len(values) != len(remaining):
                    raise LayoutError("adjustment table width mismatch")
                for rem, value in zip(remaining, values):
                    key = (pay, rem)
                    if key in seen and parse_value(seen[key]) != parse_value(value):
                        raise LayoutError("remaining term %s listed twice with different rates" % rem)
                    seen[key] = value
    if not seen:
        raise LayoutError("no adjustment rates")
    return date, [(date, "mva_reference_rate", mva_plan(pay, rem), "", value) for (pay, rem), value in seen.items()]


def parse_page(page):
    secs = sections(page)
    cur = next((b for t, b in secs.items() if t == "積立利率と基準指標金利"), None)
    past = next((b for t, b in secs.items() if t.startswith("過去の積立利率")), None)
    adj = next((b for t, b in secs.items() if "調整用基準指標金利" in t), None)
    if cur is None or adj is None:
        raise LayoutError("rate sections not found")
    date, points = parse_current(cur)
    if past is not None:
        points += parse_past(past)
    adate, apoints = parse_adjustment(adj)
    if adate != date:
        raise LayoutError("contract month %s and surrender month %s differ" % (date, adate))
    return date, points + apoints


def series_for(product, metric, plan):
    for s in product["rate_series"]:
        if s["metric"] == metric and s["plan"] == plan:
            return s
    raise LayoutError("no rate_series for %s/%s" % (metric, plan))


def rows_from_page(product, page, source_url, retrieved, snapshot, wayback):
    date, points = parse_page(page)
    grouped = {}
    for eff, metric, plan, term, text in points:
        series = series_for(product, metric, plan)
        value, status = parse_value(text)
        grouped.setdefault(series["csv"], []).append(
            {
                "product_id": product["id"],
                "metric": metric,
                "plan": plan,
                "currency": "USD",
                "term_years": term,
                "age_band": "",
                "sex": "",
                "effective_date": eff,
                "value_pct": value,
                "status": status,
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if wayback else "false",
            }
        )
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


def compare_edition(grouped_primary, grouped_edition, label):
    """Every point of the edition's page must equal the primary page's value."""
    index = {}
    for rows in grouped_primary.values():
        for r in rows:
            index[tuple(r[k] for k in common.RATE_KEY[1:])] = (r["value_pct"], r["status"])
    same, bad = 0, 0
    for rows in grouped_edition.values():
        for r in rows:
            key = tuple(r[k] for k in common.RATE_KEY[1:])
            other = index.get(key)
            if other is None:
                continue
            if other == (r["value_pct"], r["status"]):
                same += 1
            else:
                bad += 1
                print("    EDITION DIFFERS %s: product %s, edition %s" % (" ".join(k for k in key if k), other[0], r["value_pct"]))
    print("  %-34s %3d points identical, %d different" % (label, same, bad))
    return bad


def snapshot_pages(product):
    """Snapshot every page; return {page name: (url, body, path)} for rate pages."""
    rate_names = {product["rate_page"]} | {e["rate_page"] for e in product.get("editions", [])}
    found = {}
    for page in product.get("pages", []):
        body = common.fetch(page["url"])
        folder = "rates" if page["name"] in rate_names else "pages"
        path = common.save_snapshot(SRC / folder, page["name"], "html", body)
        print("  page  %s" % common.rel(path))
        if page["name"] in rate_names:
            found[page["name"]] = (page["url"], body, path)
    return found


def collect_current(product, found):
    conflicts = 0
    url, body, path = found[product["rate_page"]]
    date, grouped = rows_from_page(product, common.decode(body), url, common.today(), path, False)
    print("  current contract month %s" % date)
    conflicts += merge(grouped, "current page")
    for edition in product.get("editions", []):
        eurl, ebody, epath = found[edition["rate_page"]]
        edate, egrouped = rows_from_page(product, common.decode(ebody), eurl, common.today(), epath, False)
        if edate != date:
            print("  edition %s shows month %s, product %s: not compared" % (edition["site_code"], edate, date))
            continue
        conflicts += compare_edition(grouped, egrouped, "edition " + edition["site_code"])
    return conflicts


def wayback_captures(url):
    raw = common.fetch(CDX % url.replace("https://", ""), pause=2.0)
    return [(r[1], r[2]) for r in json.loads(raw)[1:] if r[4] == "200"]


def fetch_wayback(url, tries=4):
    for attempt in range(tries):
        try:
            return common.fetch(url, pause=3.0)
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            time.sleep(60 * (attempt + 1))
        except RuntimeError:
            time.sleep(10 * (attempt + 1))
    return None


def load_capture(code, ts, original):
    folder = SRC / "wayback"
    path = folder / ("%s_rate_%s.html" % (code, ts))
    wb_url = WAYBACK % (ts, original)
    if path.exists():
        return path, wb_url, path.read_bytes()
    body = fetch_wayback(wb_url)
    if body is None:
        return path, wb_url, None
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path, wb_url, body


def collect_wayback(product):
    conflicts = 0
    pages = {p["name"]: p["url"] for p in product["pages"]}
    sources = [(product["site_code"], pages[product["rate_page"]], None)]
    for e in product.get("editions", []):
        sources.append((e["site_code"], pages[e["rate_page"]], e))
    primary = {}
    for code, url, edition in sources:
        for ts, original in wayback_captures(url):
            path, wb_url, body = load_capture(code, ts, original)
            if body is None:
                print("  wayback %s %s SKIPPED (Wayback Machine refused: rate limit)" % (code, ts))
                continue
            try:
                eff, grouped = rows_from_page(product, common.decode(body), wb_url, common.today(), path, True)
            except (LayoutError, ValueError) as err:
                print("  wayback %s %s SKIPPED (cannot parse reliably): %s" % (code, ts, err))
                continue
            if edition is None:
                print("  wayback %s %s contract month %s" % (code, ts, eff))
                conflicts += merge(grouped, "wayback %s" % ts)
                primary[ts] = grouped
            else:
                # compare against what is on file for the same data points
                bad = compare_with_file(grouped, "wayback %s %s" % (code, ts))
                conflicts += bad
                if not bad:
                    # identical wherever both pages overlap, so months only the
                    # edition's capture shows are added, with the edition's URL
                    conflicts += merge(grouped, "  + months only in edition capture")
    return conflicts


def compare_with_file(grouped, label):
    same, bad, new = 0, 0, 0
    for name, rows in grouped.items():
        path = common.DATA / "rates" / name
        existing = {}
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                existing = {tuple(r[k] for k in common.RATE_KEY): (r["value_pct"], r["status"]) for r in csv.DictReader(fh)}
        for r in rows:
            key = tuple(r[k] for k in common.RATE_KEY)
            if key not in existing:
                new += 1
            elif existing[key] == (r["value_pct"], r["status"]):
                same += 1
            else:
                bad += 1
                print("    EDITION DIFFERS %s: on file %s, edition capture %s" % (" ".join(k for k in key if k), existing[key][0], r["value_pct"]))
    print("  %-34s %3d identical, %d different, %d not on file for the product" % (label, same, bad, new))
    return bad


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
    cols = ["product_id", "name", "title_ja", "url", "file", "bytes", "sha256", "retrieved_date"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(manifest.values(), key=lambda r: (r["product_id"], r["name"], r["file"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--docs", action="store_true", help="also download the product documents")
    parser.add_argument("--wayback", action="store_true", help="also ingest Wayback captures of the rate pages")
    parser.add_argument("--only", help="collect a single product id")
    args = parser.parse_args()

    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    conflicts = 0
    for file in sorted((common.DATA / "products").glob("cal-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        found = snapshot_pages(product)
        conflicts += collect_current(product, found)
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
