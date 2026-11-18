"""Collector for Manulife Japan (マニュライフ生命保険).

Product: 未来につなげる終身保険v2 (通貨選択型一時払終身保険), JPY / USD / AUD.

How the rates are published
  The rate pages (.../rates/miraisyushinv2-1/ln-baseint.html for the latest
  period, .../ln-baseint-past.html for the previous twelve half-month periods)
  are shells: the numbers are loaded by script from the site's own JSON feed
  (POST /bin/rates/data). manulife.co.jp answers HTTP 403 to non-browser
  clients, so this collector cannot fetch the pages itself and does not try to
  get around that. The pages were read in the built-in browser and condensed
  into text snapshots under sources/mnl/rates/ (format "mnl-condensed-v1",
  described in the first lines of each snapshot). This script:
    1. unless --no-live is given, requests the two live pages once and says how
       they answered (nothing from them is ingested; collect_all.py passes
       --no-live, so a run from the app makes no request);
    2. parses every snapshot in sources/mnl/rates/ and merges it into
       data/rates/mnl-mirai-shushin-v2__crediting_rate__<plan>.csv.
  To add newer data, save a new snapshot of the same format (named
  ln-baseint_<date>.txt or ln-baseint-past_<date>.txt) and re-run.

Plans (one CSV each; all are 積立利率, fixed for life from the contract date):
  ari_kihon      告知ありタイプ 基本コース／特定疾病保障コース
  ari_uketori    告知ありタイプ 受取コース
  nashi_kihon    告知なしタイプ 基本コース (by 第1保険期間 2/3/5/7/10 years)
  nashi_uketori  告知なしタイプ 受取コース (by 第1保険期間 2/5/10 years)
The page miraisyushinv2 (without -1) shows the 基本コース figures only and was
checked equal to the -1 page; it is not collected separately.

The rate PDFs under /documents/rates/riritsu/ are the insurer's generic 主な諸利率
(not this product's grid), so they are not used. No PDF library is needed.

Usage:
    python3 scripts/mnl.py
    python3 scripts/mnl.py --no-live
"""

import argparse
import json
import re
import sys

import common

SRC = common.SOURCES / "mnl"
PRODUCT_ID = "mnl-mirai-shushin-v2"
PAGES = {
    "ln-baseint": "https://www.manulife.co.jp/ja/individual/rates/miraisyushinv2-1/ln-baseint.html",
    "ln-baseint-past": "https://www.manulife.co.jp/ja/individual/rates/miraisyushinv2-1/ln-baseint-past.html",
}
CCY = {"円": "JPY", "米ドル": "USD", "豪ドル": "AUD"}
TERMS = {"nashi_kihon": ["2", "3", "5", "7", "10"], "nashi_uketori": ["2", "5", "10"]}
AGE_KEYS = {"30": "0-34", "35": "35-39", "40": "40-44", "45": "45-49", "50": "50-54", "55": "55-59",
            "60": "60-64", "65": "65-69", "70": "70-74", "75": "75-79", "80": "80-84", "85": "85+"}


class LayoutError(Exception):
    pass


def age_band(label):
    """'34歳以下' -> '0-34', '35~39歳' -> '35-39', '85歳以上' -> '85+'."""
    m = re.fullmatch(r"(\d+)歳以下", label)
    if m:
        return "0-%s" % m.group(1)
    m = re.fullmatch(r"(\d+)歳以上", label)
    if m:
        return "%s+" % m.group(1)
    m = re.fullmatch(r"(\d+)[~～](\d+)歳", label)
    if m:
        return "%s-%s" % m.groups()
    raise LayoutError("unknown age label %r" % label)


def point(plan, ccy, term, band, date, text):
    offered = text != "-"
    if offered and not re.fullmatch(r"\d+\.\d+", text):
        raise LayoutError("odd value %r" % text)
    return {"plan": plan, "currency": ccy, "term_years": term, "age_band": band, "effective_date": date,
            "value_pct": text if offered else "", "status": "published" if offered else "not_offered"}


def parse_latest(text):
    """Parse an ln-baseint snapshot into points."""
    m = re.search(r"^#period (\d{4})年(\d\d)月(\d\d)日 から", text, re.M)
    if not m:
        raise LayoutError("no #period line")
    date = "%s-%s-%s" % m.groups()
    out = []
    for line in text.splitlines():
        if not line.startswith("R "):
            continue
        _, plan, ccy, age, *vals = line.split()
        band = age_band(age)
        if plan.startswith("ari_"):
            if ccy != "ALL" or len(vals) != 3:
                raise LayoutError("bad ari row: %s" % line)
            for c, v in zip(("JPY", "USD", "AUD"), vals):
                out.append(point(plan, c, "", band, date, v))
        elif plan in TERMS:
            if ccy not in ("JPY", "USD", "AUD") or len(vals) != len(TERMS[plan]):
                raise LayoutError("bad nashi row: %s" % line)
            for t, v in zip(TERMS[plan], vals):
                out.append(point(plan, ccy, t, band, date, v))
        else:
            raise LayoutError("unknown plan in %r" % line)
    if len(out) < 100:
        raise LayoutError("only %d points read" % len(out))
    return out


def parse_past(text):
    """Parse an ln-baseint-past snapshot into points."""
    lines = text.splitlines()
    try:
        start = lines.index("#periods") + 1
        end = lines.index("#tables")
    except ValueError:
        raise LayoutError("no #periods/#tables markers")
    dates = []
    for l in lines[start:end]:
        m = re.match(r"(\d{4})年(\d\d)月(\d\d)日 ~ ", l)
        if not m:
            raise LayoutError("bad period %r" % l)
        dates.append("%s-%s-%s" % m.groups())
    out = []
    for line in lines[end + 1:]:
        if not line.startswith("P "):
            continue
        _, key, *vals = line.split()
        m = re.fullmatch(r"(ARI|NASHI)_(KIHON|UKETORI)(?:_(JPY|USD|AUD))?_(\d+)", key)
        if not m or m.group(4) not in AGE_KEYS:
            raise LayoutError("bad table key %r" % key)
        plan = "%s_%s" % (m.group(1).lower(), m.group(2).lower())
        band = AGE_KEYS[m.group(4)]
        if plan.startswith("ari_") and band == "50-54":
            band = "50+"  # 告知ありタイプ prints its oldest band as 50歳以上 (see the snapshot's #ages line)
        if plan.startswith("ari_"):
            if m.group(3) or len(vals) != len(dates):
                raise LayoutError("bad ari table %s" % key)
            for date, trio in zip(dates, vals):
                cells = trio.split("/")
                if len(cells) != 3:
                    raise LayoutError("bad triple in %s" % key)
                for c, v in zip(("JPY", "USD", "AUD"), cells):
                    out.append(point(plan, c, "", band, date, v))
        else:
            terms = TERMS[plan]
            if not m.group(3) or not vals[0].startswith("n=") or len(vals) != len(dates) + 1:
                raise LayoutError("bad nashi table %s" % key)
            n = int(vals[0][2:])
            if not 1 <= n <= len(terms):
                raise LayoutError("bad n in %s" % key)
            for date, v in zip(dates, vals[1:]):
                for i, t in enumerate(terms):
                    out.append(point(plan, m.group(3), t, band, date, v if i < n else "-"))
    if len(out) < 1000:
        raise LayoutError("only %d points read" % len(out))
    return out


def load_product():
    return json.loads((common.DATA / "products" / (PRODUCT_ID + ".json")).read_text(encoding="utf-8"))


def write(product, points, page_key, snapshot, retrieved):
    csv_of = {s["plan"]: s["csv"] for s in product["rate_series"]}
    by_plan = {}
    for p in points:
        by_plan.setdefault(p["plan"], []).append({
            "product_id": PRODUCT_ID, "metric": "crediting_rate", "plan": p["plan"], "currency": p["currency"],
            "term_years": p["term_years"], "age_band": p["age_band"], "sex": "", "effective_date": p["effective_date"],
            "value_pct": p["value_pct"], "status": p["status"], "source_url": PAGES[page_key],
            "retrieved_date": retrieved, "snapshot": common.rel(snapshot), "via_wayback": "false"})
    added, conflicts = 0, []
    for plan, rows in sorted(by_plan.items()):
        a, c = common.merge_rates(common.DATA / "rates" / csv_of[plan], rows)
        added += a
        conflicts += c
    return added, conflicts


def try_live():
    for key, url in PAGES.items():
        try:
            common.fetch(url, retries=1, timeout=30)
            print("  live %s answered, but its rates are script-loaded; ingest a browser snapshot instead" % key)
        except Exception as err:
            print("  live %s NOT READABLE by this client (%s). Not worked around; using saved snapshots." % (key, err))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="make no request to the insurer's site")
    args = ap.parse_args()
    product = load_product()
    if args.no_live:
        print("  no request made to the insurer's site (--no-live); re-reading the saved copies under %s" % common.rel(SRC / "rates"))
    else:
        try_live()
    snaps = sorted((SRC / "rates").glob("ln-baseint*_????-??-??.txt"))
    if not snaps:
        print("no snapshots in %s" % common.rel(SRC / "rates"))
        return 2
    total_new, total_conf, failed = 0, 0, 0
    for path in snaps:
        m = re.fullmatch(r"(ln-baseint(?:-past)?)_(\d{4}-\d{2}-\d{2})\.txt", path.name)
        key, retrieved = m.groups()
        try:
            text = path.read_text(encoding="utf-8")
            points = (parse_past if key.endswith("-past") else parse_latest)(text)
        except LayoutError as err:
            print("  %s SKIPPED, layout not parsed: %s" % (path.name, err))
            failed += 1
            continue
        added, conflicts = write(product, points, key, path, retrieved)
        print("  %-34s %4d points read, %4d new" % (path.name, len(points), added))
        for k, old, new in conflicts:
            print("    CONFLICT %s: on file %s, snapshot shows %s" % (" ".join(x for x in k if x), old, new))
        total_new += added
        total_conf += len(conflicts)
    print("%d new points, %d conflicts" % (total_new, total_conf))
    return 1 if (failed or total_conf) else 0


if __name__ == "__main__":
    sys.exit(main())
