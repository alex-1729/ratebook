"""Collector for PGF Life (プルデンシャル ジブラルタ ファイナンシャル生命).

Products (data/products/pgf-*.json):
  pgf-hosho-sentaku-neo-usd  保障選択型終身保険Neo＜米ドル建＞ (and its editions)
      積立利率 and 実質的な利回り, current period and the 2024-12-01 onward history
  pgf-sodateru-kajitsu       そだてる果実＜一時払＞ (and そだてる年金US)
      参照運用利回り and 上限率 per contract anniversary

The insurer's rate tables are not in the page HTML: they are loaded from
/hpcms/*.do fragments that sit behind bot protection (Imperva "Pardon Our
Interruption"), so scripts cannot read them. This collector does not try to
get around that. The tables were read in the built-in browser pane and the
rendered tables saved as sources/pgf/pages/<name>_<date>.html (see the
comment at the top of each file). The collector parses only those snapshots.
Unless --no-live is given it sends one probe request to report whether the
fragments answer scripts, but it never ingests a live response
(collect_all.py passes --no-live, so a run from the app makes no request).
Add newer snapshots in the same format to
extend the series. (pagetext_*.txt files hold page text that supports the
product JSON's descriptive fields and are not parsed.)

Usage:
    python3 scripts/pgf.py              # probe the live fragments once, then re-read the saved copies
    python3 scripts/pgf.py --no-live    # re-read the saved copies only, no request at all

Re-running is safe: existing data points are never rewritten.
"""

import argparse
import json
import re
import sys
import unicodedata
import urllib.error

import common

SRC = common.SOURCES / "pgf"
PAGES = SRC / "pages"
SITE = "https://www.pgf-life.co.jp"
JSWL_CURRENT = SITE + "/st/products/jswl/fxrate100.html"
JSWL_PAST = SITE + "/st/products/jswl/tumitate_past.html"
SDIA_CAP = SITE + "/st/products/sdia/01-1.html"
PROBE = SITE + "/hpcms/Fxrate1100.do?TABLE_TYPE=0"


def norm(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def parse_value(text):
    """'年5.25%' -> ('5.25', 'published'); '取扱停止中' / '-' -> ('', 'not_offered'); '' -> None."""
    t = norm(text)
    if t == "":
        return None
    m = re.fullmatch(r"(?:年)?(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return m.group(1), "published"
    return "", "not_offered"


def span(page, span_id):
    m = re.search(r'<span id="%s">([\s\S]*?)</span>\s*(?=<span id=|$)' % span_id, page)
    if not m:
        raise ValueError("block %s not found" % span_id)
    return m.group(1)


def ymd(text):
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", norm(text))
    if not m:
        raise ValueError("no date in %r" % text)
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def age_term(label_age, label_term):
    a = re.search(r"(\d+)[～~〜](\d+)歳", norm(label_age))
    t = re.search(r"(\d+)年", norm(label_term))
    if not a or not t:
        raise ValueError("unexpected age/term labels %r %r" % (label_age, label_term))
    return "%s-%s" % (a.group(1), a.group(2)), t.group(1)


def parse_current(block, date):
    """Rows: 契約年齢 | 積立利率適用期間 | 積立利率 | 実質的な利回り."""
    points = []
    rows = common.table_rows(block)
    if [c[0] for c in rows[0]][2:4] != ["積立利率", "実質的な利回り（年複利）"] and "積立利率" not in rows[0][2][0]:
        raise ValueError("unexpected current-rate header")
    for cells in rows[1:]:
        if len(cells) != 4:
            raise ValueError("unexpected current-rate row %r" % [c[0] for c in cells])
        age, term = age_term(cells[0][0], cells[1][0])
        for metric, cell in (("crediting_rate", cells[2][0]), ("net_yield", cells[3][0])):
            pv = parse_value(cell)
            if pv is not None:
                points.append((metric, date, age, term, pv))
    return points


def parse_past(block):
    """Three header rows (age, term, metric) then one row per 契約日 period."""
    rows = common.table_rows(block)
    ages = [c[0] for c in rows[0][1:]]
    terms = [c[0] for c in rows[1]]
    if len(ages) != 2 or len(terms) != 2 or [c[0] for c in rows[2]][0] != "積立利率":
        raise ValueError("unexpected past-rate header")
    cols = [age_term(ages[0], terms[0]), age_term(ages[1], terms[1])]
    points = []
    for cells in rows[3:]:
        if len(cells) != 5:
            raise ValueError("unexpected past-rate row %r" % [c[0] for c in cells])
        date = ymd(cells[0][0])
        for i, (age, term) in enumerate(cols):
            for metric, cell in (("crediting_rate", cells[1 + 2 * i][0]), ("net_yield", cells[2 + 2 * i][0])):
                pv = parse_value(cell)
                if pv is not None:
                    points.append((metric, date, age, term, pv))
    return points


def parse_sdia(page):
    block = re.search(r'id="disp_limit_rate_list">([\s\S]*?)</table>', page)
    if not block:
        raise ValueError("limit-rate table not found")
    rows = common.table_rows(block.group(1) + "</table>")
    head = [norm(c[0]) for c in rows[0]]
    if head[:3] != ["契約応当日", "参照運用利回り", "上限率"]:
        raise ValueError("unexpected cap-rate header %r" % head)
    points = []
    for cells in rows[1:]:
        date = ymd(cells[0][0])
        for metric, cell in (("reference_yield", cells[1][0]), ("cap_rate", cells[2][0])):
            pv = parse_value(cell)
            if pv is not None:
                points.append((metric, date, "", "", pv))
    return points


def to_rows(product, plan, points, source_url, retrieved, snapshot):
    """points: (metric, date, age, term, (value, status)) -> {csv name: [rows]}."""
    grouped = {}
    for metric, date, age, term, (value, status) in points:
        series = next(s for s in product["rate_series"] if s["metric"] == metric and s["plan"] == plan)
        grouped.setdefault(series["csv"], []).append(
            {
                "product_id": product["id"],
                "metric": metric,
                "plan": plan,
                "currency": "USD",
                "term_years": term,
                "age_band": age,
                "sex": "",
                "effective_date": date,
                "value_pct": value,
                "status": status,
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "false",
            }
        )
    return grouped


def merge(grouped, label):
    added_total, conflicts = 0, 0
    for name, rows in sorted(grouped.items()):
        added, bad = common.merge_rates(common.DATA / "rates" / name, rows)
        added_total += added
        conflicts += len(bad)
        for key, old, new in bad:
            print("    CONFLICT %s: on file %s, source shows %s" % (" ".join(k for k in key if k), old, new))
    print("  %-44s %4d points read, %4d new" % (label, sum(len(r) for r in grouped.values()), added_total))
    return conflicts


def snapshot_date(path):
    return re.search(r"_(\d{4}-\d{2}-\d{2})\.html$", path.name).group(1)


def live_reachable():
    """One plain request to a rate fragment. True only if it answers with a table."""
    try:
        body = common.decode(common.fetch(PROBE, retries=1))
    except (urllib.error.URLError, RuntimeError, OSError) as err:
        print("  live fragments: not readable (%s)" % err)
        return False
    if "Pardon Our Interruption" in body or "<table" not in body:
        print("  live fragments: answer with a bot-protection page (Pardon Our Interruption), not read by script;")
        print("                  using the browser-pane snapshots under sources/pgf/pages/")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-live", action="store_true", help="make no request to the insurer's site")
    args = parser.parse_args()
    products = {p["id"]: p for p in (json.loads(f.read_text(encoding="utf-8")) for f in sorted((common.DATA / "products").glob("pgf-*.json")))}
    neo, sdia = products["pgf-hosho-sentaku-neo-usd"], products["pgf-sodateru-kajitsu"]
    conflicts = 0
    if args.no_live:
        print("  no request made to the insurer's site (--no-live); re-reading the saved copies under sources/pgf/pages/")
    elif live_reachable():
        print("  live fragments answered this time; the collector still reads only the saved snapshots (the protection is intermittent).")
    print("%s  %s" % (neo["id"], neo["name_ja"]))
    for path in sorted(PAGES.glob("jswl_tumitate_past_*.html")):
        page = common.decode(path.read_bytes())
        retrieved = snapshot_date(path)
        for span_id, plan in (("disp_rate2", "base"), ("disp_rate4", "withdrawal")):
            grouped = to_rows(neo, plan, parse_past(span(page, span_id)), JSWL_PAST, retrieved, path)
            conflicts += merge(grouped, "%s %s" % (path.name, plan))
    for path in sorted(PAGES.glob("jswl_fxrate100_*.html")):
        page = common.decode(path.read_bytes())
        retrieved = snapshot_date(path)
        date = ymd(span(page, "disp_date1"))
        for span_id, plan in (("disp_rate7", "base"), ("disp_rate9", "withdrawal")):
            grouped = to_rows(neo, plan, parse_current(span(page, span_id), date), JSWL_CURRENT, retrieved, path)
            conflicts += merge(grouped, "%s %s" % (path.name, plan))
    print("%s  %s" % (sdia["id"], sdia["name_ja"]))
    for path in sorted(PAGES.glob("sdia_limit_rate_*.html")):
        page = common.decode(path.read_bytes())
        grouped = to_rows(sdia, "single", parse_sdia(page), SDIA_CAP, snapshot_date(path), path)
        conflicts += merge(grouped, path.name)
    if conflicts:
        print("%d conflicts: the source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
