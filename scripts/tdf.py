"""Collector for T&D Financial Life (T&Dフィナンシャル生命).

Reads every data/products/tdf-*.json and, for each product:
  - snapshots its pages (the rate page goes to sources/tdf/rates/)
  - parses the CURRENT 積立利率設定期間 of the rate page into rate rows
    (積立利率 and 実質的な利回り) and merges them into data/rates/
  - with --docs, downloads the listed documents
  - with --wayback, also ingests every distinct Wayback Machine capture of
    the rate page and of its distributor copies (r_tumitate_riritu.html etc.;
    raw captures go to sources/tdf/wayback/)

The rate page shows only the current half-month period and is overwritten on
the 1st and 16th, so history only accumulates by running this regularly.
effective_date is the first day of the 積立利率設定期間.

Usage:
    python3 scripts/tdf.py
    python3 scripts/tdf.py --docs --wayback
    python3 scripts/tdf.py --only tdf-fiveten-world-4

Re-running is safe: unchanged pages are not saved twice and existing data
points are never rewritten. Captures that cannot be parsed reliably are
skipped and reported; gaps are never interpolated.
"""

import argparse
import csv
import json
import re
import sys
import unicodedata

import common
import wayback

SRC = common.SOURCES / "tdf"
CURRENCY_BY_JA = {"米ドル": "USD", "豪ドル": "AUD"}


def norm(text):
    """Full-width to ASCII (NFKC), whitespace collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def parse_value(text):
    """'２．５１％' -> ('2.51', 'published'); '取扱停止中' or '-' -> ('', 'not_offered')."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?", norm(text))
    if m:
        return m.group(1), "published"
    return "", "not_offered"


def parse_period(page):
    m = re.search(r"積立利率設定期間[：:]\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", norm(page))
    if not m:
        raise ValueError("積立利率設定期間 not found")
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def split_tables(page):
    """Yield (caption, table_html) for every <table>."""
    for m in re.finditer(r"<table[^>]*>([\s\S]*?)</table>", page):
        cap = re.search(r"<caption[^>]*>([\s\S]*?)</caption>", m.group(1))
        yield (norm(common.clean(cap.group(1))) if cap else ""), m.group(0)


def strip_comments(page):
    return re.sub(r"<!--[\s\S]*?-->", "", page)


def parse_page(page):
    """Return (effective_date, points) from a rate page.

    Each point is a dict: metric, currency, course (JPY course name or ''),
    rider (True/False/None), sex, term, text. Raises ValueError when the
    layout is not the one this parser knows.
    """
    page = strip_comments(page)
    date = parse_period(page)
    points = []
    for caption, table in split_tables(page):
        rows = [[(norm(t), c, r) for t, c, r in cells] for cells in common.table_rows(table)]
        flat = norm(" ".join(c[0] for row in rows for c in row))
        if "実質的な利回り" in flat:
            points += parse_net_yield_table(rows)
        elif "積立利率" in flat and "据置期間" in flat and ("運用通貨" in flat or "コース" in flat):
            points += parse_crediting_table(rows, caption)
    if not points:
        raise ValueError("no rate tables recognised")
    return date, points


def parse_crediting_table(rows, caption):
    """円貨 table: columns are courses. 外貨 table: columns are currencies."""
    head = rows[0]
    labels = [c[0] for c in head[1:]]  # skip the colspan=2 label cell
    jpy = head[0][0] == "コース"
    rider = None
    if not jpy:
        if "付加" in caption:
            rider = True
        elif caption == "":
            rider = False
        else:
            raise ValueError("unknown table caption %r" % caption)
    out = []
    for cells in rows[2:]:
        if len(cells) != 2 + len(labels) or not re.fullmatch(r"\d+年", cells[0][0]):
            raise ValueError("unexpected crediting row %r" % [c[0] for c in cells])
        term = cells[0][0][:-1]
        for label, cell in zip(labels, cells[2:]):
            out.append(
                {
                    "metric": "crediting_rate",
                    "currency": "JPY" if jpy else CURRENCY_BY_JA[label],
                    "course": label if jpy else "",
                    "rider": rider,
                    "sex": "",
                    "term": term,
                    "text": cell[0],
                }
            )
    return out


def parse_net_yield_table(rows):
    """Rows end with: [currency or course], 契約年齢, N年間, 積立利率, 実質的な利回り.

    The leading cells (性別, 保険金最低保証特約) use rowspan, so they are
    tracked as state while walking the rows.
    """
    header = " ".join(c[0] for c in rows[0])
    if "積立利率" not in header or "実質的な利回り" not in header or "年金支払開始日" not in header:
        return []
    fx = "運用通貨" in header
    out = []
    sex, rider = "", None
    sex_left = rider_left = 0
    for cells in rows[1:]:
        texts = [c[0] for c in cells]
        if len(texts) < 5:
            raise ValueError("unexpected net-yield row %r" % texts)
        lead = cells[: len(cells) - 4]  # label cells before the age column
        # lead = [sex?][rider?][currency|course]
        for text, _, rowspan in lead[:-1]:
            if text in ("付加なし", "付加あり"):
                rider, rider_left = text == "付加あり", rowspan
            elif "男性" in text or "女性" in text:
                has_m, has_f = "男性" in text, "女性" in text
                sex = "" if has_m and has_f else ("M" if has_m else "F")
                sex_left = rowspan
            else:
                raise ValueError("unexpected leading cell %r" % text)
        label = lead[-1][0]
        term = re.fullmatch(r"(\d+)年間", texts[-3])
        if not term:
            raise ValueError("unexpected period cell %r" % texts[-3])
        if fx and (label not in CURRENCY_BY_JA or rider is None):
            raise ValueError("unexpected FX net-yield row %r" % texts)
        out.append(
            {
                "metric": "net_yield",
                "currency": CURRENCY_BY_JA[label] if fx else "JPY",
                "course": "" if fx else label,
                "rider": rider if fx else None,
                "sex": sex,
                "term": term.group(1),
                "text": texts[-1],
                "crediting_text": texts[-2],
            }
        )
        sex_left -= 1
        rider_left -= 1
        if sex_left <= 0:
            sex = ""
        if rider_left <= 0:
            rider = None
    return out


def age_band(text):
    m = re.fullmatch(r"(\d+)\s*[～~-]\s*(\d+)\s*歳", norm(text))
    if not m:
        raise ValueError("unexpected age band %r" % text)
    return "%s-%s" % (m.group(1), m.group(2))


def parse_world6_yen(page):
    """生涯プレミアムワールド6 円貨 page: rows are age bands; columns are
    男性 / 男性 基本保険金額2,000万円～ / 女性 / 女性 基本保険金額2,000万円～."""
    page = strip_comments(page)
    date = parse_period(page)
    points = []
    for _, table in split_tables(page):
        rows = [[(norm(t), c, r) for t, c, r in cells] for cells in common.table_rows(table)]
        flat = norm(" ".join(c[0] for row in rows for c in row))
        if "2,000万円" not in flat or "男性" not in flat:
            continue
        for cells in rows:
            if not re.fullmatch(r"\d+[～~]\d+歳", cells[0][0]):
                continue
            if len(cells) != 5:
                raise ValueError("unexpected yen row %r" % [c[0] for c in cells])
            for (sex, plan), cell in zip((("M", "jpy_std"), ("M", "jpy_20m"), ("F", "jpy_std"), ("F", "jpy_20m")), cells[1:]):
                points.append({"metric": "crediting_rate", "plan": plan, "currency": "JPY", "sex": sex,
                               "age_band": age_band(cells[0][0]), "term": "", "text": cell[0]})
    if not points:
        raise ValueError("no yen rate table recognised")
    return date, points


def parse_world6_fx(page):
    """生涯プレミアムワールド6 外貨 page: per-currency rate tables (columns 男性
    no rider / 男性 rider / 女性 no rider / 女性 rider), the reset-period table
    and the 実質的な利回り example tables."""
    page = strip_comments(page)
    date = parse_period(page)
    currency = None
    rate_tables, yield_tables, terms = [], [], {}
    for m in re.finditer(r"運用通貨[：:]\s*(米ドル|豪ドル)|<table[^>]*>[\s\S]*?</table>", page):
        if m.group(1):
            currency = CURRENCY_BY_JA[m.group(1)]
            continue
        table = m.group(0)
        rows = [[(norm(t), c, r) for t, c, r in cells] for cells in common.table_rows(table)]
        flat = norm(" ".join(c[0] for row in rows for c in row))
        if "積立利率更改期間" in flat and "積立利率更改日" in flat:
            for cells in rows[1:]:
                if cells[0][0] in CURRENCY_BY_JA:
                    mm = re.fullmatch(r"(\d+)年", cells[1][0])
                    if not mm:
                        raise ValueError("unexpected reset period %r" % cells[1][0])
                    terms[CURRENCY_BY_JA[cells[0][0]]] = mm.group(1)
        elif "実質的な利回り" in flat:
            raw = norm(common.clean(table))
            if "付加しない" in raw:
                rider = False
            elif "付加した" in raw:
                rider = True
            else:
                raise ValueError("cannot tell rider from yield table")
            yield_tables.append((rider, rows))
        elif "保険金最低保証特約を" in flat and "男性" in flat:
            if currency is None:
                raise ValueError("rate table without a currency heading")
            rate_tables.append((currency, rows))
    if not rate_tables:
        raise ValueError("no foreign-currency rate table recognised")
    points = []
    for cur, rows in rate_tables:
        if cur not in terms:
            raise ValueError("reset period for %s not found" % cur)
        for cells in rows:
            if not re.fullmatch(r"\d+[～~]\d+歳", cells[0][0]):
                continue
            if len(cells) != 5:
                raise ValueError("unexpected FX row %r" % [c[0] for c in cells])
            for (sex, plan), cell in zip((("M", "fx_no_rider"), ("M", "fx_rider"), ("F", "fx_no_rider"), ("F", "fx_rider")), cells[1:]):
                points.append({"metric": "crediting_rate", "plan": plan, "currency": cur, "sex": sex,
                               "age_band": age_band(cells[0][0]), "term": terms[cur], "text": cell[0]})
    for rider, rows in yield_tables:
        sex = ""
        # Course column order differs between page versions: the 2024 page
        # heads them 積立コース / 定期支払コース, later pages 定期受取コース /
        # 任意受取コース. Map by header text, never by position.
        courses = [c[0] for cells in rows for c in cells if c[0].rstrip("*＊").endswith("コース")]
        course_keys = []
        for name in courses:
            if "定期" in name:
                course_keys.append("periodic")
            elif "任意" in name or "積立コース" in name:
                course_keys.append("optional")
            else:
                raise ValueError("unknown yield course %r" % name)
        if sorted(course_keys) != ["optional", "periodic"]:
            raise ValueError("unexpected yield courses %r" % courses)
        for cells in rows:
            texts = [c[0] for c in cells]
            if texts and texts[0] in ("男性", "女性"):
                sex = "M" if texts[0] == "男性" else "F"
                texts = texts[1:]
            if len(texts) != 6 or texts[0] not in CURRENCY_BY_JA:
                continue
            cur = CURRENCY_BY_JA[texts[0]]
            mm = re.fullmatch(r"(\d+)年間", texts[2])
            if not mm or not sex:
                raise ValueError("unexpected yield row %r" % texts)
            if mm.group(1) != terms.get(cur):
                raise ValueError("yield period differs from reset period for %s" % cur)
            for course, text in zip(course_keys, texts[4:6]):
                points.append({"metric": "net_yield", "plan": "fx_%s_%s" % ("rider" if rider else "no_rider", course),
                               "currency": cur, "sex": sex, "age_band": age_band(texts[1]), "term": mm.group(1), "text": text})
    return date, points


PARSERS = {"world6_yen": parse_world6_yen, "world6_fx": parse_world6_fx}


def rows_from_points(product, date, points, source_url, retrieved, snapshot, wayback):
    plans = {(s["metric"], s["plan"]): s["csv"] for s in product["rate_series"]}
    grouped = {}
    for p in points:
        name = plans.get((p["metric"], p["plan"]))
        if name is None:
            raise ValueError("no rate_series for %s/%s" % (p["metric"], p["plan"]))
        value, status = parse_value(p["text"])
        grouped.setdefault(name, []).append(
            {
                "product_id": product["id"],
                "metric": p["metric"],
                "plan": p["plan"],
                "currency": p["currency"],
                "term_years": p["term"],
                "age_band": p["age_band"],
                "sex": p["sex"],
                "effective_date": date,
                "value_pct": value,
                "status": status,
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if wayback else "false",
            }
        )
    return date, grouped


def series_for(product, point):
    for series in product["rate_series"]:
        if series["metric"] != point["metric"] or point["currency"] not in series["currencies"]:
            continue
        if "course_ja" in series and series["course_ja"] != point["course"]:
            continue
        if "rider" in series and series["rider"] != point["rider"]:
            continue
        return series
    raise ValueError("no rate_series matches %r" % point)


def rows_from_page(product, page, source_url, retrieved, snapshot, wayback):
    """Parse a page into CSV rows grouped by csv filename."""
    date, points = parse_page(page)
    by_key = {}
    for p in points:
        if p["metric"] == "crediting_rate":
            by_key[(p["currency"], p["course"], p["rider"], p["term"])] = p["text"]
    grouped = {}
    for p in points:
        if p["metric"] == "net_yield":  # cross-check against the crediting-rate table
            other = by_key.get((p["currency"], p["course"], p["rider"], p["term"]))
            if other is not None and parse_value(other) != parse_value(p["crediting_text"]):
                raise ValueError("crediting rate differs between tables for %r" % p)
        series = series_for(product, p)
        value, status = parse_value(p["text"])
        grouped.setdefault(series["csv"], []).append(
            {
                "product_id": product["id"],
                "metric": p["metric"],
                "plan": series["plan"],
                "currency": p["currency"],
                "term_years": p["term"],
                "age_band": "",
                "sex": p["sex"],
                "effective_date": date,
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


def snapshot_pages(product):
    """Snapshot every page; return {rate page name: (url, body, path)}."""
    rate_names = set(product["rate_pages"]) if "rate_pages" in product else {product["rate_page"]}
    snaps = {}
    for page in product.get("pages", []):
        body = common.fetch(page["url"])
        folder = "rates" if page["name"] in rate_names else "pages"
        path = common.save_snapshot(SRC / folder, page["name"], "html", body)
        print("  page  %s" % common.rel(path))
        if page["name"] in rate_names:
            snaps[page["name"]] = (page["url"], body, path)
    return snaps


def collect_current(product, snaps):
    conflicts = 0
    if "rate_pages" not in product:
        url, body, path = snaps[product["rate_page"]]
        date, grouped = rows_from_page(product, common.decode(body), url, common.today(), path, False)
        print("  current period starting %s" % date)
        return merge(grouped, "current period")
    for name, spec in product["rate_pages"].items():
        url, body, path = snaps[name]
        date, points = PARSERS[spec["parser"]](common.decode(body))
        date, grouped = rows_from_points(product, date, points, url, common.today(), path, False)
        print("  %s: period starting %s" % (name, date))
        conflicts += merge(grouped, "current " + name)
    return conflicts


def wayback_rate_pages(rate_url):
    """Distinct-content captures of the rate page and of its distributor copies.

    Distributor copies sit beside the rate page with a prefix (r_tumitate_riritu.html
    and so on) and show the same table. Returns [(page_name, timestamp, original)].
    """
    folder = rate_url.rsplit("/", 1)[0] + "/"
    stem = rate_url.rsplit("/", 1)[1]
    out = []
    for rec in wayback.captures(folder, match="prefix"):
        name = rec["original"].split("?")[0].rsplit("/", 1)[1]
        if name == stem or (name.endswith("_" + stem) and re.fullmatch(r"[a-z_]+", name[: -len(stem)])):
            out.append((name[:-5], rec["timestamp"], rec["original"]))
    return sorted(out, key=lambda r: (r[1], r[0]))


def collect_wayback(product):
    if "rate_pages" in product:
        targets = [(n, spec["wayback_prefix"], spec["parser"]) for n, spec in product["rate_pages"].items()]
    else:
        targets = [(product["rate_page"], product.get("wayback_prefix", "tumitate_riritu"), None)]
    conflicts = 0
    for page_name, prefix, parser in targets:
        rate_url = next(p["url"] for p in product["pages"] if p["name"] == page_name)
        stem = rate_url.rsplit("/", 1)[1][:-5]
        for copy_name, ts, original in wayback_rate_pages(rate_url):
            # main page keeps <prefix>_<ts>.html; a distributor copy gets its own prefix (r_<prefix>_<ts>.html)
            file_prefix = copy_name[: -len(stem)] + prefix
            label = "wayback %s %s" % (file_prefix, ts)
            path = SRC / "wayback" / ("%s_%s.html" % (file_prefix, ts))
            wb_url = wayback.RAW % (ts, original)
            if not path.exists():
                got = wayback.fetch(ts, original)
                if got is None:
                    print("  %s SKIPPED (Wayback Machine refused: rate limit)" % label)
                    continue
                body, wb_url, form = got
                if form != "raw":
                    print("  %s note: only the rewritten form was served" % label)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            body = path.read_bytes()
            try:
                if parser:
                    date, points = PARSERS[parser](common.decode(body))
                    eff, grouped = rows_from_points(product, date, points, wb_url, common.today(), path, True)
                else:
                    eff, grouped = rows_from_page(product, common.decode(body), wb_url, common.today(), path, True)
            except (ValueError, KeyError) as err:
                print("  %s SKIPPED (cannot parse reliably): %s" % (label, err))
                continue
            print("  %s period starting %s" % (label, eff))
            conflicts += merge(grouped, label)
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
    for file in sorted((common.DATA / "products").glob("tdf-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        snaps = snapshot_pages(product)
        conflicts += collect_current(product, snaps)
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
