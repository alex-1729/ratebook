"""Collector for Mitsui Sumitomo Primary Life (三井住友海上プライマリー生命).

Reads every data/products/msp-*.json and, for each product:
  - snapshots its pages (product page, rate index, agency list)
  - tries the live 積立利率 page (msp.ms-primary.com) and parses it; if the
    server does not answer, says so and carries on
  - with --wayback, ingests every Wayback Machine capture of that rate page
  - with --docs, downloads the listed documents
  - with --agents, refreshes the product's distributor list from the agency page

Usage:
    python3 scripts/msp.py                      # pages and live rates
    python3 scripts/msp.py --wayback --docs     # also Wayback captures and documents
    python3 scripts/msp.py --agents             # refresh distributors in the product JSON
    python3 scripts/msp.py --no-live            # skip the live rate server
    python3 scripts/msp.py --only msp-mare-3

Raw snapshots go to sources/msp/, rates to data/rates/. Re-running is safe:
unchanged files are not saved twice and existing data points are never
rewritten. Only the 積立利率 tables are collected; FX tables are ignored.
A page whose layout does not match what the parser expects is skipped and
reported, never guessed at.
"""

import argparse
import csv
import json
import re
import sys

from pathlib import Path

import common
import wayback

SRC = common.SOURCES / "msp"
MSP_CURRENCIES = {"米ドル": "USD", "豪ドル": "AUD", "ユーロ": "EUR", "ＮＺドル": "NZD", "円": "JPY"}
WINDOW = re.compile(r"(\d{4})年(\d{2})月(\d{2})日～(\d{4})年(\d{2})月(\d{2})日が契約日")


class LayoutError(Exception):
    pass


def parse_rate_page(page, courses):
    """Parse the 積立利率 section into (effective_date, window_end, [(currency, plan, term, text)]).

    courses maps the course label (e.g. 積立コース) to its plan key. Raises
    LayoutError on anything unexpected.
    """
    start = page.find('id="anc-01"')
    end = page.find('id="anc-02"')
    if start < 0:
        raise LayoutError("no 積立利率 section (anc-01)")
    section = page[start : end if end > start else len(page)]
    m = WINDOW.search(section)
    if not m:
        raise LayoutError("no contract-date window sentence")
    effective = "%s-%s-%s" % m.group(1, 2, 3)
    window_end = "%s-%s-%s" % m.group(4, 5, 6)
    points = []
    blocks = re.findall(r'table-v2__title">([\s\S]*?)</div>[\s\S]*?(<table[\s\S]*?</table>)', section)
    if not blocks:
        raise LayoutError("no rate tables")
    seen = set()
    for title, table in blocks:
        title = common.clean(title)
        if title not in MSP_CURRENCIES:
            raise LayoutError("unknown currency table %r" % title)
        if title in seen:
            raise LayoutError("duplicate table for %s" % title)
        seen.add(title)
        rows = common.table_rows(table)
        header = [c[0] for c in rows[1]] if len(rows) > 1 else []
        if len(rows) < 3 or not header or not all(re.fullmatch(r"\d+年", h) for h in header) or header != sorted(set(header), key=lambda h: int(h[:-1])):
            raise LayoutError("%s: unexpected header %r" % (title, rows[:2]))
        terms = [c[0][:-1] for c in rows[1]]
        for cells in rows[2:]:
            label = cells[0][0]
            if label not in courses:
                raise LayoutError("%s: unknown course %r" % (title, label))
            values = [c[0] for c in cells[1:]]
            if len(values) != len(terms):
                raise LayoutError("%s/%s: %d values for %d terms" % (title, label, len(values), len(terms)))
            for term, value in zip(terms, values):
                if value != "-" and not re.fullmatch(r"\d+\.\d+%", value):
                    raise LayoutError("%s/%s/%s: odd value %r" % (title, label, term, value))
                points.append((MSP_CURRENCIES[title], courses[label], term, value))
    if "USD" not in {p[0] for p in points}:
        raise LayoutError("no USD table")
    return effective, window_end, points


AGE = re.compile(r"\d+歳(～\d+歳)?")
DEFER = re.compile(r"\d+年(～\d+年)?")
VALUE = re.compile(r"\d+\.\d+%|-")


def _band(text, unit):
    """'0年～4年' -> '0-4', '10年' -> '10', '50歳～69歳' -> '50-69'."""
    return text.replace(unit, "").replace("～", "-")


def _tenor(band):
    """A deferral is the tenor only when it is one number of years ('10'), not a band ('0-4')."""
    return band if band.isdigit() else ""


def _cells(table):
    return [common.clean(c) for c in re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", table)]


def _need(cond, msg):
    if not cond:
        raise LayoutError(msg)


def parse_rate_page_ashita(page, product):
    """Parse the あしたの、よろこび2 rate page (four courses, five tables).

    The page's tables are malformed HTML (rows lack their opening tag), so
    cells are read as one ordered stream per table. term_years is only the
    deferral (据置期間) when the page gives a single number of years, age_band
    only the issue-age band; every other dimension is part of the plan key:
      ukedori_shushin_def<band>        受取重視 終身年金, by deferral band (0-4, 5-9, 10);
                                       age_band = issue age; term_years = deferral if a single number
      ukedori_kakutei_def<band>_pay<n> 受取重視 確定年金, by deferral band and payout period;
                                       term_years = deferral if a single number
      sakidori_anshin                  先取・安心, age_band = issue age, term_years blank
      mankiki_jujitsu_g<pct>           満期充実, by 保障率 (100/70/50 %); term_years = deferral
      tsumitate                        積立, term_years = deferral
    Returns (effective, window_end, [(currency, plan, term, text, age_band)]).
    """
    start = page.find('id="anc-01"')
    end = page.find('id="anc-02"')
    _need(start >= 0, "no 積立利率 section (anc-01)")
    section = page[start : end if end > start else len(page)]
    m = WINDOW.search(section)
    _need(m, "no contract-date window sentence")
    effective = "%s-%s-%s" % m.group(1, 2, 3)
    window_end = "%s-%s-%s" % m.group(4, 5, 6)
    plans = {s["plan"] for s in product["rate_series"]}
    bases = {s.get("plan_base", s["plan"]) for s in product["rate_series"]}
    points = []
    seen_plans = set()
    pieces = re.split(r'<h3 class="heading3__title">', section)[1:]
    _need(pieces, "no course headings")
    for piece in pieces:
        course = common.clean(piece.split("</h3>")[0])
        for table in re.findall(r"<table[\s\S]*?</table>", piece):
            cells = _cells(table)
            _need("契約通貨" in cells, "%s: no 契約通貨 header" % course)
            title = common.clean((re.findall(r'table-v2__title">([\s\S]*?)</div>', piece[: piece.find(table)]) or [""])[-1])
            if course == "受取重視コース" and title.startswith("死亡時保証"):
                plan, rows = "ukedori_shushin", _parse_ukedori_shushin(cells)
            elif course == "受取重視コース" and title == "確定年金":
                plan, rows = "ukedori_kakutei", _parse_ukedori_kakutei(cells)
            elif course == "先取・安心コース":
                plan, rows = "sakidori_anshin", _parse_sakidori(cells)
            elif course == "満期充実コース":
                plan, rows = "mankiki_jujitsu", _parse_mankiki(cells)
            elif course == "積立コース":
                plan, rows = "tsumitate", _parse_tsumitate(cells)
            else:
                raise LayoutError("unknown course/table %r / %r" % (course, title))
            _need(plan in bases, "plan %s missing from rate_series" % plan)
            seen_plans.add(plan)
            for c, suffix, t, v, a in rows:
                full = plan + ("_" + suffix if suffix else "")
                _need(full in plans, "column %s (%s) has no rate_series entry: add one" % (full, c))
                points.append((c, full, t, v, a))
    _need(len(seen_plans) == 5, "expected 5 tables, got %s" % sorted(seen_plans))
    return effective, window_end, points


CUR = {"米ドル": "USD", "豪ドル": "AUD", "円": "JPY"}


def _split_currencies(cells, header_len):
    """Yield (currency, [cells]) for each currency block after the header."""
    body = cells[header_len:]
    blocks, cur = [], None
    for c in body:
        if c in CUR:
            cur = [CUR[c], []]
            blocks.append(cur)
        else:
            _need(cur is not None, "cell %r before any currency" % c)
            cur[1].append(c)
    _need(blocks, "no currency rows")
    return blocks


def _parse_ukedori_shushin(cells):
    _need(cells[:4] == ["契約通貨", "据置期間", "上段：契約年齢", "下段：積立利率"], "ukedori_shushin header %r" % cells[:4])
    out = []
    for cur, rest in _split_currencies(cells, 4):
        i = 0
        while i < len(rest):
            _need(DEFER.fullmatch(rest[i]), "%s: expected deferral, got %r" % (cur, rest[i]))
            defer = _band(rest[i], "年")
            ages = rest[i + 1 : i + 4]
            vals = rest[i + 4 : i + 7]
            _need(len(ages) == 3 and all(AGE.fullmatch(a) for a in ages), "%s: bad age cells %r" % (cur, ages))
            _need(len(vals) == 3 and all(VALUE.fullmatch(v) for v in vals), "%s: bad values %r" % (cur, vals))
            for a, v in zip(ages, vals):
                out.append((cur, "def" + defer, _tenor(defer), v, _band(a, "歳")))
            i += 7
        _need(i == len(rest), "%s: trailing cells" % cur)
    return out


def _parse_ukedori_kakutei(cells):
    _need(cells[:2] == ["契約通貨", "据置期間"], "ukedori_kakutei header %r" % cells[:2])
    _need(cells[2] == "年金支払期間別 積立利率", "ukedori_kakutei header %r" % cells[2])
    periods = []
    k = 3
    while k < len(cells) and re.fullmatch(r"\d+年", cells[k]):
        periods.append(cells[k][:-1])
        k += 1
    _need(len(periods) == 5, "ukedori_kakutei periods %r" % periods)
    out = []
    for cur, rest in _split_currencies(cells, k):
        _need(len(rest) % 6 == 0, "%s: %d cells" % (cur, len(rest)))
        for i in range(0, len(rest), 6):
            _need(DEFER.fullmatch(rest[i]), "%s: expected deferral, got %r" % (cur, rest[i]))
            vals = rest[i + 1 : i + 6]
            _need(all(VALUE.fullmatch(v) for v in vals), "%s: bad values %r" % (cur, vals))
            defer = _band(rest[i], "年")
            for per, v in zip(periods, vals):
                out.append((cur, "def%s_pay%s" % (defer, per), _tenor(defer), v, ""))
    return out


def _parse_sakidori(cells):
    _need(cells[:2] == ["契約通貨", "契約年齢別 積立利率"], "sakidori header %r" % cells[:2])
    ages = cells[2:5]
    _need(len(ages) == 3 and all(AGE.fullmatch(a) for a in ages), "sakidori ages %r" % ages)
    out = []
    for cur, rest in _split_currencies(cells, 5):
        _need(len(rest) == 3 and all(VALUE.fullmatch(v) for v in rest), "%s: %r" % (cur, rest))
        for a, v in zip(ages, rest):
            out.append((cur, "", "", v, _band(a, "歳")))
    return out


def _parse_mankiki(cells):
    _need(cells[:3] == ["契約通貨", "据置期間", "保障率別 積立利率"], "mankiki header %r" % cells[:3])
    guar = cells[3:6]
    _need(len(guar) == 3 and all(re.fullmatch(r"\d+％保障型", g) for g in guar), "mankiki guarantees %r" % guar)
    out = []
    for cur, rest in _split_currencies(cells, 6):
        _need(len(rest) % 4 == 0, "%s: %d cells" % (cur, len(rest)))
        for i in range(0, len(rest), 4):
            _need(re.fullmatch(r"\d+年", rest[i]), "%s: expected term, got %r" % (cur, rest[i]))
            vals = rest[i + 1 : i + 4]
            _need(all(VALUE.fullmatch(v) for v in vals), "%s: bad values %r" % (cur, vals))
            for g, v in zip(guar, vals):
                out.append((cur, "g" + g.split("％")[0], rest[i][:-1], v, ""))
    return out


def _parse_tsumitate(cells):
    _need(cells[:3] == ["契約通貨", "据置期間", "積立利率"], "tsumitate header %r" % cells[:3])
    out = []
    for cur, rest in _split_currencies(cells, 3):
        _need(len(rest) % 2 == 0, "%s: %d cells" % (cur, len(rest)))
        for i in range(0, len(rest), 2):
            _need(re.fullmatch(r"\d+年", rest[i]) and VALUE.fullmatch(rest[i + 1]), "%s: %r" % (cur, rest[i : i + 2]))
            out.append((cur, "", rest[i][:-1], rest[i + 1], ""))
    return out


def parse_page(product, page):
    if product.get("rate_parser") == "ashitano_yorokobi2":
        return parse_rate_page_ashita(page, product)
    return parse_rate_page(page, course_map(product))


def course_map(product):
    return {s["course_ja"]: s["plan"] for s in product["rate_series"]}


def build_rows(product, effective, points, source_url, snapshot, via_wayback, retrieved=None):
    """Turn parsed points into rate rows grouped by plan (offline: nothing is written)."""
    retrieved = retrieved or common.today()
    by_plan = {}
    for point in points:
        currency, plan, term, text = point[:4]
        age_band = point[4] if len(point) > 4 else ""
        offered = text != "-"
        by_plan.setdefault(plan, []).append(
            {
                "product_id": product["id"],
                "metric": "crediting_rate",
                "plan": plan,
                "currency": currency,
                "term_years": term,
                "age_band": age_band,
                "sex": "",
                "effective_date": effective,
                "value_pct": text.rstrip("%") if offered else "",
                "status": "published" if offered else "not_offered",
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if via_wayback else "false",
            }
        )
    return by_plan


def write_points(product, effective, points, source_url, snapshot, via_wayback, retrieved=None):
    """Merge parsed points into the per-plan CSVs. Returns (added, conflicts)."""
    csv_of = {s["plan"]: s["csv"] for s in product["rate_series"]}
    by_plan = build_rows(product, effective, points, source_url, snapshot, via_wayback, retrieved)
    added, conflicts = 0, []
    for plan, rows in sorted(by_plan.items()):
        a, c = common.merge_rates(common.DATA / "rates" / csv_of[plan], rows)
        added += a
        conflicts += c
    return added, conflicts


def report(label, effective, window_end, points, added, conflicts):
    print("  %-26s window %s to %s, %3d points read, %3d new" % (label, effective, window_end, len(points), added))
    for key, old, new in conflicts:
        print("    CONFLICT %s: on file %s, source now shows %s" % (" ".join(k for k in key if k), old, new))
    return len(conflicts)


def snapshot_pages(product):
    for page in product.get("pages", []):
        try:
            body = common.fetch(page["url"], retries=2, timeout=40)
        except RuntimeError as err:
            print("  page  NOT REACHABLE %s (%s)" % (page["url"], err))
            continue
        path = common.save_snapshot(SRC / "pages", page["name"], "html", body)
        print("  page  %s" % common.rel(path))


def collect_live(product):
    src = product["rate_source"]
    try:
        body = common.fetch(src["live_url"], retries=2, timeout=75)
    except RuntimeError as err:
        print("  live rate page NOT REACHABLE (%s); nothing collected from it. Not worked around." % err)
        return 0
    path = common.save_snapshot(SRC / "rates", src["name"], "html", body)
    try:
        effective, window_end, points = parse_page(product, common.decode(body))
    except LayoutError as err:
        print("  live rate page saved to %s but SKIPPED, layout not parsed: %s" % (common.rel(path), err))
        return 0
    added, conflicts = write_points(product, effective, points, src["live_url"], path, False)
    return report("live", effective, window_end, points, added, conflicts)


def collect_file(product, file, retrieved):
    """Ingest a copy of the live rate page saved by hand (e.g. from another network)."""
    src = product["rate_source"]
    body = Path(file).read_bytes()
    path = common.save_snapshot(SRC / "rates", src["name"], "html", body, date=retrieved)
    try:
        effective, window_end, points = parse_page(product, common.decode(body))
    except LayoutError as err:
        print("  saved copy filed as %s but SKIPPED, layout not parsed: %s" % (common.rel(path), err))
        return 0
    added, conflicts = write_points(product, effective, points, src["live_url"], path, False, retrieved)
    return report("saved live copy", effective, window_end, points, added, conflicts)


def wayback_records(src):
    """Captures of the rate page, including URL variants (http, extra query parameters)."""
    page, _, query = src["cdx_url"].partition("?")
    wanted = dict(kv.split("=", 1) for kv in query.split("&") if kv)
    out = []
    for rec in wayback.captures(page, match="prefix", statuses=None):
        q = rec["original"].partition("?")[2]
        params = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        if all(params.get(k) == v for k, v in wanted.items()):
            out.append(rec)
    return sorted(out, key=lambda r: r["timestamp"])


def saved_capture(src, ts):
    pattern = "%s_%s_*.html" % (src["name"], ts)
    found = sorted((SRC / "wayback").glob(pattern))
    return found[-1] if found else None


def collect_wayback(product):
    src = product["rate_source"]
    conflicts = 0
    for rec in wayback_records(src):
        ts = rec["timestamp"]
        if rec["statuscode"] != "200":
            print("  wayback %s skipped: archived HTTP %s" % (ts, rec["statuscode"]))
            continue
        url = wayback.RAW % (ts, rec["original"])
        path = saved_capture(src, ts)
        if path is None:
            got = wayback.fetch(ts, rec["original"])
            if got is None:
                print("  wayback %s NOT FETCHED (Wayback Machine refused: rate limit)" % ts)
                continue
            body, url, _form = got
            path = common.save_snapshot(SRC / "wayback", "%s_%s" % (src["name"], ts), "html", body)
        try:
            effective, window_end, points = parse_page(product, common.decode(path.read_bytes()))
        except LayoutError as err:
            print("  wayback %s SKIPPED, layout not parsed: %s" % (ts, err))
            continue
        added, found = write_points(product, effective, points, url, path, True)
        conflicts += report("wayback " + ts, effective, window_end, points, added, found)
    return conflicts


def refresh_distributors(product, file):
    url = product["agent_page"]
    body = common.fetch(url, timeout=90)
    common.save_snapshot(SRC / "pages", "product_agent", "html", body)
    page = common.decode(body)
    link = product["pages"][0]["url"].replace("https://www.ms-primary.com", "")
    out = []
    for item in page.split("js-agency-search-item")[1:]:
        m = re.search(r'js-accordion__trigger">([^<]+)<span[^>]*data-category="([^"]*)"', item)
        if m and link in item:
            out.append({"name_ja": m.group(1).strip(), "category": m.group(2)})
    product["distributors"] = out
    file.write_text(json.dumps(product, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  distributors: %d agencies list this product" % len(out))


def download_documents(product, manifest):
    folder = SRC / "docs" / product["id"]
    for doc in product.get("documents", []):
        try:
            body = common.fetch(doc["url"], timeout=120)
        except RuntimeError as err:
            print("  doc FAILED %s (%s)" % (doc["url"], err))
            continue
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
    parser.add_argument("--agents", action="store_true", help="refresh the distributor list in the product JSON")
    parser.add_argument("--no-live", action="store_true", help="do not contact the live rate server")
    parser.add_argument("--only", help="collect a single product id")
    parser.add_argument("--file", help="ingest a saved copy of the live rate page instead of fetching (needs --only)")
    parser.add_argument("--retrieved", help="date (YYYY-MM-DD) the --file copy was saved")
    args = parser.parse_args()
    if args.file and not (args.only and args.retrieved):
        parser.error("--file needs --only and --retrieved")

    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    conflicts = 0
    for file in sorted((common.DATA / "products").glob("msp-*.json")):
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
        if args.agents:
            refresh_distributors(product, file)
        if args.docs:
            download_documents(product, manifest)
    if args.docs:
        write_manifest(manifest_path, manifest)
    if conflicts:
        print("%d conflicts: a source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
