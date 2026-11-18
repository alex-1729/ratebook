"""Spread of every crediting rate and net yield over the government curve.

Usage:
    python3 scripts/rate_spreads.py                       # all products, candidates included and labelled
    python3 scripts/rate_spreads.py --exclude-candidates  # leave out products marked "candidate": true

Reads data/rates/ (crediting_rate and net_yield) and data/markets/govt_yield__*.csv
(from govt_yields.py), and writes:
    data/markets/rate_spreads.csv   one row per rate point with its matched yield
    reports/rate-spreads/*.svg      charts
    reports/Rate spreads.md         the write-up, method included

Standard library only. Makes no network requests.
"""

import csv
import datetime as dt
import glob
import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import common

ROOT = common.ROOT
OUT_CSV = common.DATA / "markets" / "rate_spreads.csv"
REPORT = ROOT / "reports" / "Rate spreads.md"
CHART_DIR = ROOT / "reports" / "rate-spreads"

METRICS = ("crediting_rate", "net_yield")
CURVES = {"USD": "UST", "JPY": "JGB", "AUD": "ACGB"}
MAX_STALE_DAYS = 7       # yield must be from the effective date or up to 7 days before
MAX_GAP_DAYS = 45        # consecutive rate points further apart are not compared or joined
LAGS = (0, 3, 7, 10, 14, 21, 28, 35, 42)  # days between the yield move and the rate's effective date
MIN_R2 = 0.3             # below this a pass-through fit is reported as "no reliable lag", not as a finding
NOW_STALE_DAYS = 14      # "now" rankings use one yield per curve: the latest within this many days of the as-of date

SPREAD_COLUMNS = [
    "product_id", "metric", "plan", "currency", "term_years", "age_band", "sex",
    "effective_date", "rate_pct", "tenor_used", "tenor_basis", "curve", "curve_tenor",
    "yield_date", "yield_pct", "yield_ae_pct", "spread_bp", "via_wayback", "rate_source_url", "candidate",
]


def d(s):
    return dt.date.fromisoformat(s)


def annual(y):
    """Par yield as published (semi-annual compounding: UST bond-equivalent, JGB and ACGB
    semi-annual coupons) -> annual effective, the basis of a credited rate."""
    return ((1 + y / 200.0) ** 2 - 1) * 100.0


# ---------- inputs ----------

def load_curves():
    """currency -> tenor -> (sorted dates, values)."""
    curves = {}
    for path in sorted((common.DATA / "markets").glob("govt_yield__*.csv")):
        by_tenor = defaultdict(list)
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                by_tenor[int(r["tenor_years"])].append((r["date"], float(r["value_pct"])))
                ccy = r["currency"]
        curves[ccy] = {t: ([x[0] for x in sorted(v)], [x[1] for x in sorted(v)]) for t, v in by_tenor.items()}
    return curves


def yield_on(curve_tenor, date, max_stale=MAX_STALE_DAYS):
    """Yield on `date` or the latest business day before it (within max_stale days)."""
    import bisect
    dates, values = curve_tenor
    i = bisect.bisect_right(dates, date) - 1
    if i < 0 or (d(date) - d(dates[i])).days > max_stale:
        return None, None
    return dates[i], values[i]


def tenor_of(term, plan):
    """Years to match on the curve, and how they were found. None when undefined."""
    if re.search(r"_(shushin|atsu)$", plan):
        return None, "life annuity (rate fixed for life, no single tenor)"
    m = re.search(r"_p(\d+)$", plan)
    if m and term:
        # Nippon Wealth fixed-term payout plans: term_years is the deferral period and
        # the rate is fixed through deferral and payout, so use deferral + half the payout.
        return float(term) + int(m.group(1)) / 2, "deferral + half the payout period"
    if term not in ("", "0"):
        return float(term), "term"
    if term == "0":
        return None, "immediate payout (no term)"
    m = re.search(r"_h(\d+)-(\d+)$", plan)
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2, "plan judgement period, midpoint"
    m = re.search(r"_h(\d+)$", plan)
    if m:
        return float(m.group(1)), "plan period"
    m = re.search(r"_hle(\d+)$", plan)
    if m:
        return float(m.group(1)), "plan period, upper bound"
    m = re.search(r"_hge(\d+)$", plan)
    if m:
        return float(m.group(1)), "plan period, lower bound"
    m = re.search(r"_h(\d+)-life$", plan)
    if m:
        return float(m.group(1)), "plan period, lower bound"
    return None, "no term in data"


def nearest(tenor, available):
    """Nearest published curve tenor; a tie goes to the longer one."""
    return min(available, key=lambda t: (abs(t - tenor), -t))


def candidate_ids():
    """Products marked "candidate": true (not yet enabled in the app)."""
    out = set()
    for f in glob.glob(str(common.DATA / "products" / "*.json")):
        info = json.load(open(f, encoding="utf-8"))
        if info.get("candidate"):
            out.add(info["id"])
    return out


PTYPES = {}      # (product_id, metric, plan) -> product_type key, as the app resolves it
TYPE_LABELS = {}


def type_of(key):
    return PTYPES.get((key[0], key[1], key[2])) or PTYPES.get((key[0], None, None)) or "unclassified"


def load_names():
    products, plans, insurers = {}, {}, {}
    tf = common.DATA / "product_types.json"
    if tf.exists():
        for t in json.load(open(tf, encoding="utf-8")).get("types", []):
            TYPE_LABELS[t["key"]] = t["label"]
    for f in glob.glob(str(common.DATA / "insurers" / "*.json")):
        info = json.load(open(f, encoding="utf-8"))
        insurers[info["id"]] = info["name_en"]
    for f in glob.glob(str(common.DATA / "products" / "*.json")):
        info = json.load(open(f, encoding="utf-8"))
        products[info["id"]] = (info["name_en"], info["insurer_id"])
        PTYPES[(info["id"], None, None)] = info.get("product_type")
        for s in info.get("rate_series", []):
            if s.get("product_type"):
                PTYPES[(info["id"], s["metric"], s["plan"])] = s["product_type"]
            plans[(info["id"], s["metric"], s["plan"])] = s.get("plan_en") or s["plan"]
    return products, plans, insurers


def build_spreads(curves, include_candidates=True):
    rows, skipped = [], defaultdict(int)
    candidates = candidate_ids()
    for path in sorted(glob.glob(str(common.DATA / "rates" / "*.csv"))):
        metric = Path(path).name.split("__")[1]
        if metric not in METRICS:
            continue
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r["value_pct"]:
                    continue
                if r["product_id"] in candidates and not include_candidates:
                    skipped["candidate product (not yet enabled)"] += 1
                    continue
                ccy = r["currency"]
                if ccy not in CURVES:
                    skipped["currency %s has no tracked curve" % ccy] += 1
                    continue
                if ccy not in curves:
                    skipped["%s curve not collected yet" % CURVES[ccy]] += 1
                    continue
                tenor, basis = tenor_of(r["term_years"], r["plan"])
                if tenor is None:
                    skipped[basis] += 1
                    continue
                ct = nearest(tenor, curves[ccy].keys())
                ydate, yv = yield_on(curves[ccy][ct], r["effective_date"])
                if yv is None:
                    skipped["no yield within %d days before the effective date" % MAX_STALE_DAYS] += 1
                    continue
                rate = float(r["value_pct"])
                yae = annual(yv)
                rows.append({
                    "product_id": r["product_id"], "metric": metric, "plan": r["plan"],
                    "currency": ccy, "term_years": r["term_years"], "age_band": r["age_band"],
                    "sex": r["sex"], "effective_date": r["effective_date"], "rate_pct": "%g" % rate,
                    "tenor_used": "%g" % tenor, "tenor_basis": basis, "curve": CURVES[ccy],
                    "curve_tenor": str(ct), "yield_date": ydate, "yield_pct": "%g" % yv,
                    "yield_ae_pct": "%.4f" % yae, "spread_bp": "%.1f" % ((rate - yae) * 100), "via_wayback": r["via_wayback"],
                    "rate_source_url": r["source_url"],
                    "candidate": "true" if r["product_id"] in candidates else "",
                })
    rows.sort(key=lambda r: (r["product_id"], r["metric"], r["plan"], r["currency"],
                             float(r["tenor_used"]), r["age_band"], r["sex"], r["effective_date"]))
    return rows, skipped


# ---------- series and statistics ----------

def series_key(r):
    return (r["product_id"], r["metric"], r["plan"], r["currency"], r["term_years"] or r["tenor_used"], r["age_band"], r["sex"])


def group_series(rows):
    series = defaultdict(list)
    for r in rows:
        series[series_key(r)].append(r)
    for pts in series.values():
        pts.sort(key=lambda r: r["effective_date"])
    return series


def headline_series(series, metric="crediting_rate"):
    """One series per product, currency and curve tenor: the plan with the longest
    history (ties: first plan name), and the middle age band."""
    groups = defaultdict(list)
    for key, pts in series.items():
        if key[1] != metric:
            continue
        groups[(key[0], key[3], pts[0]["curve_tenor"], key[4])].append(key)
    out = {}
    for (prod, ccy, ct, term), keys in groups.items():
        by_plan = defaultdict(list)
        for k in keys:
            by_plan[k[2]].append(k)
        plan = sorted(by_plan, key=lambda p: (-max(len(series[k]) for k in by_plan[p]), p))[0]
        ks = sorted(by_plan[plan], key=lambda k: (k[5], k[6]))
        out[(prod, ccy, ct, term)] = ks[len(ks) // 2]
    return out


def ols(xs, ys):
    """Slope, intercept and R^2 of y on x."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy else 0.0
    return b, my - b * mx, r2


def pass_through(pts, curve_tenor):
    """Short-run beta of rate changes on yield changes at each lag, and the level beta.

    For consecutive effective dates d0 < d1 (at most MAX_GAP_DAYS apart):
        dRate = rate(d1) - rate(d0)
        dYield(L) = yield(d1 - L) - yield(d0 - L)
    and dRate is regressed on dYield(L) for each lag L.
    """
    res = {}
    for lag in LAGS:
        xs, ys = [], []
        for a, b in zip(pts, pts[1:]):
            d0, d1 = d(a["effective_date"]), d(b["effective_date"])
            if (d1 - d0).days > MAX_GAP_DAYS:
                continue
            _, y0 = yield_on(curve_tenor, (d0 - dt.timedelta(days=lag)).isoformat())
            _, y1 = yield_on(curve_tenor, (d1 - dt.timedelta(days=lag)).isoformat())
            if y0 is None or y1 is None:
                continue
            xs.append(annual(y1) - annual(y0))
            ys.append(float(b["rate_pct"]) - float(a["rate_pct"]))
        fit = ols(xs, ys)
        if fit:
            res[lag] = (fit[0], fit[2], len(xs), sum(1 for y in ys if abs(y) > 1e-9))
    levels = ols([float(p["yield_ae_pct"]) for p in pts], [float(p["rate_pct"]) for p in pts])
    return res, levels


# ---------- SVG charts ----------

PALETTE = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
           ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#6250d6", "#9085e9"), ("#e34948", "#e66767")]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def nice_ticks(lo, hi, n=5):
    span = hi - lo or 1
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while True:
        ticks.append(round(t, 6))
        if t >= hi - 1e-9:
            return ticks
        t += step


def svg_step_chart(title, subtitle, series, y_label, path, x_min=None, zero_line=True):
    """series: list of (label, [(date, value)]) drawn as step lines, broken at gaps."""
    W, H = 760, 360
    L, R, T, B = 56, 16, 48, 40
    legend_rows = len(series)
    H_total = H + 18 * legend_rows + 8
    pts_all = [(d(x), y) for _, pts in series for x, y in pts]
    if not pts_all:
        return False
    x0 = d(x_min) if x_min else min(p[0] for p in pts_all)
    x1 = max(p[0] for p in pts_all) + dt.timedelta(days=15)
    ys = [p[1] for p in pts_all if p[0] >= x0]
    ticks = nice_ticks(min(ys + ([0] if zero_line else [])), max(ys + ([0] if zero_line else [])))
    y0v, y1v = ticks[0], ticks[-1]
    sx = lambda day: L + (W - L - R) * (day - x0).days / max(1, (x1 - x0).days)
    sy = lambda v: T + (H - T - B) * (1 - (v - y0v) / ((y1v - y0v) or 1))

    css = ["svg{font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
           ".bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.grid{stroke:#e4e3df;stroke-width:1}",
           ".zero{stroke:#8a8983;stroke-width:1}"]
    dark = [".bg{fill:#1a1a19}.t1{fill:#f0efec}.t2{fill:#c3c2b7}.grid{stroke:#34332f}.zero{stroke:#8f8e86}"]
    for i in range(len(series)):
        light, drk = PALETTE[i % len(PALETTE)]
        css.append(".s%d{stroke:%s}.f%d{fill:%s}" % (i, light, i, light))
        dark.append(".s%d{stroke:%s}.f%d{fill:%s}" % (i, drk, i, drk))
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">'
           % (W, H_total, W, H_total, esc(title)),
           "<style>%s @media (prefers-color-scheme: dark){%s}</style>" % ("".join(css), "".join(dark)),
           '<rect class="bg" width="100%" height="100%" rx="6"/>',
           '<text class="t1" x="%d" y="20" font-weight="600" font-size="14">%s</text>' % (L, esc(title)),
           '<text class="t2" x="%d" y="36">%s</text>' % (L, esc(subtitle))]
    for t in ticks:
        out.append('<line class="%s" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % ("zero" if t == 0 and zero_line else "grid", L, W - R, sy(t), sy(t)))
        out.append('<text class="t2" x="%d" y="%.1f" text-anchor="end">%g</text>' % (L - 6, sy(t) + 4, t))
    out.append('<text class="t2" x="12" y="%d" transform="rotate(-90 12 %d)" text-anchor="middle">%s</text>' % ((T + H - B) / 2, (T + H - B) / 2, esc(y_label)))
    for year in range(x0.year, x1.year + 1):
        for month in (1, 7) if (x1 - x0).days > 900 else (1, 4, 7, 10):
            day = dt.date(year, month, 1)
            if x0 <= day <= x1:
                out.append('<text class="t2" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (sx(day), H - B + 18, day.strftime("%b %Y" if month == 1 else "%b")))
                out.append('<line class="grid" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>' % (sx(day), sx(day), H - B, H - B + 5))
    for i, (label, pts) in enumerate(series):
        pts = [(d(x), y) for x, y in pts if d(x) >= x0]
        segs, cur = [], []
        for j, (day, v) in enumerate(pts):
            if cur and (day - cur[-1][0]).days > MAX_GAP_DAYS:
                segs.append(cur)
                cur = []
            cur.append((day, v))
        if cur:
            segs.append(cur)
        for seg in segs:
            pathd = "M%.1f,%.1f" % (sx(seg[0][0]), sy(seg[0][1]))
            for (da, va), (db, vb) in zip(seg, seg[1:]):
                pathd += " H%.1f V%.1f" % (sx(db), sy(vb))
            end = seg[-1][0] + dt.timedelta(days=15)
            pathd += " H%.1f" % sx(min(end, x1))
            out.append('<path class="s%d" d="%s" fill="none" stroke-width="2" stroke-linejoin="round"/>' % (i, pathd))
            if len(seg) == 1:
                out.append('<circle class="f%d" cx="%.1f" cy="%.1f" r="3"/>' % (i, sx(seg[0][0]), sy(seg[0][1])))
    for i, (label, _) in enumerate(series):
        lx = L
        ly = H + 4 + 18 * i
        out.append('<rect class="f%d" x="%.1f" y="%d" width="14" height="4" rx="2"/>' % (i, lx, ly + 4))
        out.append('<text class="t1" x="%.1f" y="%d">%s</text>' % (lx + 20, ly + 10, esc(label)))
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")
    return True


# ---------- report ----------

def fmt_bp(v):
    return "%+d" % round(v)  # round first, so -0.3 shows as +0, not -0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exclude-candidates", action="store_true",
                    help='leave out products marked "candidate": true (default: include them, labelled)')
    args = ap.parse_args(argv)
    include_candidates = not args.exclude_candidates
    cands = candidate_ids()
    curves = load_curves()
    rows, skipped = build_spreads(curves, include_candidates)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SPREAD_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    products, plans, insurers = load_names()
    series = group_series(rows)
    heads = headline_series(series)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    for old in CHART_DIR.glob("*.svg"):
        old.unlink()

    def label(key, with_plan=True, with_insurer=True):
        prod, metric, plan = key[0], key[1], key[2]
        name, ins = products.get(prod, (prod, ""))
        s = name + (" [candidate]" if prod in cands else "")
        if with_insurer:
            s = "%s %s" % (insurers.get(ins, ins).replace(" Life", "").replace(" Financial", ""), name)
        if with_plan:
            s += " · " + plans.get((prod, metric, plan), plan)
        if key[5]:
            s += " · age " + key[5]
        return s

    today = max(r["effective_date"] for r in rows)
    lines = []
    A = lines.append
    A("# Rate spreads over government bonds")
    A("")
    A("Generated %s by `scripts/rate_spreads.py` from `data/rates/` and `data/markets/`. Rates in force up to %s." % (common.today(), today))
    A("")
    A("Each crediting rate and net yield is read against the risk-free par yield of the same currency and a matching tenor: "
      "**spread = rate − government yield**, in basis points. A positive spread is what the insurer credits above a "
      "same-currency government bond bought on the same day, before loads, surrender charges, MVA and FX.")
    A("")

    head_at = len(lines)

    # ---- coverage
    A("## Coverage")
    A("")
    have = {c: CURVES[c] for c in CURVES if c in curves}
    A("| Curve | Source | Tenors kept | From | To |")
    A("|---|---|---|---|---|")
    srcs = {"USD": "US Treasury daily par yield curve (home.treasury.gov)",
            "JPY": "MOF Japan JGB par yields (jgbcm_all.csv, jgbcm.csv)",
            "AUD": "RBA table F2, government bond yields, daily"}
    for ccy, curve in CURVES.items():
        if ccy in curves:
            any_t = next(iter(curves[ccy].values()))[0]
            A("| %s (%s) | %s | %s | %s | %s |" % (curve, ccy, srcs[ccy], ", ".join("%dY" % t for t in sorted(curves[ccy])), any_t[0], any_t[-1]))
        else:
            A("| %s (%s) | %s | not collected | | |" % (curve, ccy, srcs[ccy]))
    A("")
    n_pts = len(rows)
    all_ids = set(products)
    covered = {r["product_id"] for r in rows}
    n_cand = len(cands & all_ids)
    A("**Products covered.** %d of %d products in `data/products` have at least one matched rate point (%d tracked, %d candidate%s)." % (
        len(covered), len(all_ids), len(covered - cands), len(covered & cands),
        "" if include_candidates else "; candidates excluded by option"))
    A("")
    if not include_candidates:
        A("%d candidate products were left out." % n_cand)
        A("")
    uncovered = sorted(all_ids - covered - (cands if not include_candidates else set()))
    if uncovered:
        A("Products with no matched point, and so not in the tables or charts (their rate points carry no term, are life-annuity or whole-life rates with no single tenor, or are not a crediting rate or net yield): %s." % ", ".join(
            "%s%s" % (products[i][0], " [candidate]" if i in cands else "") for i in uncovered))
        A("")
    latest = {}
    for r_ in rows:
        latest[r_["product_id"]] = max(latest.get(r_["product_id"], ""), r_["effective_date"])
    if latest:
        A("**As-of dates.** Latest rate effective date matched: %s (oldest per-product latest: %s, %s). Latest yield date used: %s." % (
            max(latest.values()), min(latest.values()), products[min(latest, key=latest.get)][0],
            max(r_["yield_date"] for r_ in rows)))
        A("")
    A("%d rate points matched to a yield (crediting rate and net yield). Points not matched:" % n_pts)
    A("")
    for reason, n in sorted(skipped.items(), key=lambda x: -x[1]):
        A("- %s: %d" % (reason, n))
    if "AUD" not in curves:
        A("")
        A("**AUD is missing.** The RBA site refused the scripted download of table F2 (HTTP 403) from this network, and nothing "
          "was done to get round that. Save https://www.rba.gov.au/statistics/tables/csv/f2-data.csv from a browser and run "
          "`python3 scripts/govt_yields.py --only AUD --rba-file <saved file>`, then re-run this script: every AUD rate is then "
          "matched and appears in the tables and charts below.")
    A("")

    # ---- method
    A("## Method")
    A("")
    A("- **Yields.** Par yields as published, daily, from %s. The curve for USD is the Treasury par yield curve (constant maturity, bond-equivalent), JPY is the MOF JGB par yield table, AUD is RBA F2 (yields interpolated by the RBA to constant 2/3/5/10-year maturities). Tenors kept: UST 1/2/3/5/7/10/20/30Y, JGB 1/2/3/5/7/10/15/20/30Y, ACGB 2/3/5/10Y. F2 stops at 10 years, so AUD terms of 15-30 years are matched to ACGB 10Y and their spreads overstate the pick-up wherever the curve slopes up. The RBA publishes F2 a few days in arrears, so the latest AUD points use a yield up to a week old." % "2019")
    A("- **Tenor of a rate.** The rate point's `term_years` (the product's accumulation or guarantee period). Where the plan has no term column but a period in its name (Nippon Wealth Yuyu Jikan Advance 2 judgement-period plans, e.g. 10–12 years), the midpoint, or the stated bound for open-ended bands (≤5 → 5, ≥11 → 11, 18-to-life → 18). Nippon Wealth's fixed-term payout plans (5/10/20-year payout) store the deferral period as the term, and the rate is fixed through deferral and payout, so their tenor is deferral + half the payout period (roughly the duration of a level annuity). Life-annuity plans and immediate-payout points (term 0) have no single tenor and are left out.")
    A("- **Matching.** Same currency; the nearest published curve tenor (a tie goes to the longer). No interpolation along the curve. Yield on the rate's effective date, or the latest business day before it, at most %d days earlier." % MAX_STALE_DAYS)
    A("- **Spread** = rate − yield, in bp, on each rate point as stored, with the yield first converted from its published semi-annual basis to annual effective, the basis of a credited rate: (1 + y/2)² − 1. At 5% that adds about 6 bp to the yield, at 2% about 1 bp; `yield_pct` in the CSV is the yield as published and `yield_ae_pct` the converted one. Crediting rates and net yields are both used; net yields (T&D, Dai-ichi Frontier) are after the product's loads and so are the fairer read.")
    if include_candidates:
        A("- **Products.** All products in `data/products` are included, tracked and candidate. Candidate products (marked as candidates in the product file, not yet enabled in the app) are labelled “[candidate]” in the tables and charts and have `candidate = true` in the CSV. `--exclude-candidates` leaves them out.")
    else:
        A("- **Products.** Candidate products (marked as candidates in the product file, not yet enabled in the app) are left out (`--exclude-candidates`).")
    A("- **Rankings by product type.** Headlines and “richest now” tables are split by product type (`product_type` of the product, or of the plan where it overrides it, as in the app), so a whole-life rate is never ranked against a fixed annuity.")
    A("- **Caveats.** Insurers price off swaps, corporate spreads and their own asset mix, so the spread is a measure of competitiveness, not of margin. Spreads on Wayback-derived points (`via_wayback`) are included and flagged in the CSV. Survival-weighted (tontine) and annuity-focused plans can credit a higher rate because part of the value is forfeited on early death or surrender, so their spreads are not like-for-like with plain savings plans.")
    A("- **Pass-through.** For each headline series, consecutive effective dates d0 < d1 at most %d days apart give ΔRate = rate(d1) − rate(d0) and ΔYield(L) = yield(d1 − L) − yield(d0 − L) for lags L = %s days. ΔRate is regressed on ΔYield(L) by OLS for each lag; the **lag** reported is the one with the highest R², and the **beta** is the slope at that lag (1.0 = full pass-through of a yield move within one rate period). Picking the best of %d lags flatters R², so a fit with R² below %.1f is reported as “no reliable lag” and kept out of the headlines. The **level beta** is the slope of the rate on the matched yield across all points; both series trend, so it describes how they moved together and is not an estimate of cause. An insurer's medians pool its headline series, which are often one product at several tenors and so not independent. Series with fewer than 8 comparable period pairs (archive captures on scattered dates, or histories that start in 2024-25) are not estimated." % (MAX_GAP_DAYS, ", ".join(map(str, LAGS)), len(LAGS), MIN_R2))
    A("- **Headline series.** Charts and pass-through use one series per product, currency and curve tenor: the plan with the longest history and the middle age band. All series are in `data/markets/rate_spreads.csv`.")
    A("")
    A("Data: `data/markets/rate_spreads.csv` (one row per rate point: matched tenor, curve, yield date, yield and spread; written by this script and not kept in git, so run it to get the file). Charts are SVG in `reports/rate-spreads/`; the y axis is the spread in bp, steps hold until the next rate, and a line breaks where points are more than %d days apart." % MAX_GAP_DAYS)
    A("")

    # ---- curves context chart
    ctx = []
    for ccy in ("USD", "JPY", "AUD"):
        if ccy not in curves:
            continue
        for t in (5, 10):
            if t in curves[ccy]:
                dates, vals = curves[ccy][t]
                # month-end points keep the chart light
                pts, last = [], None
                for x, v in zip(dates, vals):
                    if last and x[:7] != last[0][:7]:
                        pts.append(last)
                    last = (x, v)
                pts.append(last)
                ctx.append(("%s %dY" % (CURVES[ccy], t), pts))
    if svg_step_chart("Government par yields", "Month-end, %", ctx, "%", CHART_DIR / "govt-yields.svg", zero_line=True):
        A("## The curves")
        A("")
        A("![Government par yields](rate-spreads/govt-yields.svg)")
        A("")

    # ---- spread over time charts
    A("## Spread over time")
    A("")
    A("Crediting rate minus the matched government yield, one headline series per product. Charts for the 5Y and 10Y points of each curve; other tenors (short USD deferrals, JPY 15-30Y) are in the CSV and the tables below.")
    A("")
    by_chart = defaultdict(list)
    for (prod, ccy, ct, term), key in heads.items():
        by_chart[(ccy, int(ct))].append(key)
    for (ccy, ct) in sorted(k for k in by_chart if k[1] in (5, 10)):
        keys = sorted(by_chart[(ccy, ct)], key=lambda k: (-len(series[k]), k))
        keys = [k for k in keys if len(series[k]) >= 2][:6]
        if not keys:
            continue
        sers = []
        for k in keys:
            lab = label(k)
            if k[4] and k[4] != str(ct):
                lab += " · %sY term" % k[4]
            sers.append((lab, [(p["effective_date"], float(p["spread_bp"])) for p in series[k]]))
        fname = "spread-%s-%dy.svg" % (ccy.lower(), ct)
        if svg_step_chart("%s crediting rate spread over %s %dY" % (ccy, CURVES[ccy], ct), "bp, crediting rate − par yield on the effective date",
                          sers, "bp", CHART_DIR / fname, x_min="2019-01-01"):
            A("### %s against %s %dY" % (ccy, CURVES[ccy], ct))
            A("")
            A("![%s spread over %s %dY](rate-spreads/%s)" % (ccy, CURVES[ccy], ct, fname))
            A("")

    # ---- pass-through
    A("## Pass-through of yield moves")
    A("")
    A("One row per headline crediting-rate series with at least 8 period-to-period comparisons. "
      "Lag = days between the yield move and the effective date that fits best; beta = share of a yield move passed into the rate at that lag; "
      "R² of that fit; level beta = long-run slope of rate on yield; moves = periods in which the rate changed.")
    A("")
    A("| Product · plan | Ccy | Curve | Pairs | Moves | Best lag (d) | Beta | R² | Beta at lag 14 d | Level beta |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    pt_rows = []
    for (prod, ccy, ct, term), key in heads.items():
        pts = series[key]
        res, levels = pass_through(pts, curves[ccy][int(ct)])
        if not res:
            continue
        best = max(res, key=lambda L: res[L][1])
        b, r2, n, moves = res[best]
        if n < 8:
            continue
        b14 = res.get(14, (float("nan"),))[0]
        pt_rows.append((ccy, ct, label(key), b, r2, n, moves, best, b14, levels[0] if levels else float("nan"), key))
    pt_rows.sort(key=lambda x: (x[0], int(x[1]), -x[4]))
    for ccy, ct, lab, b, r2, n, moves, best, b14, lb, key in pt_rows:
        A("| %s%s | %s | %s %sY | %d | %d | %s | %s | %.2f | %.2f | %.2f |" % (
            lab, (" · %sY term" % key[4]) if key[4] != str(ct) else "", ccy, CURVES[ccy], ct, n, moves,
            "%d" % best if r2 >= MIN_R2 else "none", "%.2f" % b if r2 >= MIN_R2 else "–", r2, b14, lb))
    A("")
    # by insurer summary
    agg = defaultdict(list)
    for ccy, ct, lab, b, r2, n, moves, best, b14, lb, key in pt_rows:
        agg[(products[key[0]][1], ccy)].append((b, r2, best, lb, n))
    A("By insurer and currency (median across that insurer's headline series, weighted equally):")
    A("")
    A("| Insurer | Ccy | Series | Median best lag (d) | Median beta | Median R² | Median level beta |")
    A("|---|---|---|---|---|---|---|")
    weak = lambda v: med([x[1] for x in v]) < MIN_R2

    def med(v):
        v = sorted(v)
        return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2

    def med_lag(v):  # a lag that was actually tested: the lower middle value
        v = sorted(v)
        return v[(len(v) - 1) // 2]
    for (ins, ccy), v in sorted(agg.items(), key=lambda x: (x[0][1], x[0][0])):
        A("| %s | %s | %d | %s | %s | %.2f | %.2f |" % (insurers.get(ins, ins), ccy, len(v),
                                                   "no reliable lag" if weak(v) else "%g" % med_lag([x[2] for x in v]),
                                                   "–" if weak(v) else "%.2f" % med([x[0] for x in v]), med([x[1] for x in v]), med([x[3] for x in v])))
    A("")

    # pass-through chart: beta by lag for the insurers' headline series at 10Y USD and JPY
    for ccy in ("USD", "JPY", "AUD"):
        lagsers, shown = [], set()
        # one line per product: its series whose curve tenor is closest to 10Y
        for c, ct, lab, b, r2, n, moves, best, b14, lb, key in sorted(pt_rows, key=lambda x: abs(int(x[1]) - 10)):
            if c != ccy or len(lagsers) >= 6 or key[0] in shown:
                continue
            shown.add(key[0])
            res, _ = pass_through(series[key], curves[ccy][int(ct)])
            lagsers.append(("%s · %s %sY" % (lab, CURVES[ccy], ct), [((dt.date(2000, 1, 1) + dt.timedelta(days=L)).isoformat(), res[L][1]) for L in LAGS if L in res]))
        if lagsers:
            fname = "fit-by-lag-%s.svg" % ccy.lower()
            if svg_lag_chart("%s: how well yield moves explain rate moves, by lag" % ccy, "R² of ΔRate on ΔYield(L); the peak is the typical lag",
                             lagsers, CHART_DIR / fname):
                A("![%s fit by lag](rate-spreads/%s)" % (ccy, fname))
                A("")

    # ---- richest now
    A("## Richest against the curve now")
    A("")
    A("Each series' latest rate (effective within 31 days of %s), all read against **one yield per curve and tenor**: the latest "
      "published on or before %s (shown above each table), so every row in a table is compared with the same bond. "
      "Percentile = where that spread sits in the series' own history of spreads at its effective dates (100 = widest ever)." % (today, today))
    A("")
    latest, now_yield = defaultdict(list), {}
    for key, pts in series.items():
        last = pts[-1]
        if (d(today) - d(last["effective_date"])).days > 31:
            continue
        ccy, ct = key[3], int(last["curve_tenor"])
        if (ccy, ct) not in now_yield:
            now_yield[(ccy, ct)] = yield_on(curves[ccy][ct], today, NOW_STALE_DAYS)
        ydate, yv = now_yield[(ccy, ct)]
        if yv is None:
            continue
        cur = (float(last["rate_pct"]) - annual(yv)) * 100
        hist = [float(p["spread_bp"]) for p in pts]
        pct = 100.0 * sum(1 for h in hist if h <= cur) / len(hist)
        latest[(key[1], key[3], ct, type_of(key))].append((cur, key, last, pct, len(hist)))
    for metric in METRICS:
        A("### %s" % ("Crediting rate" if metric == "crediting_rate" else "Net yield (after loads)"))
        A("")
        for (m, ccy, ct, ty) in sorted((k for k in latest if k[0] == metric), key=lambda k: (k[1], k[2], k[3])):
            items = sorted(latest[(m, ccy, ct, ty)], key=lambda x: -x[0])
            # one row per product+plan+term: best age band, to keep tables short
            seen, rowsx = set(), []
            for cur, key, last, pct, nh in items:
                k2 = (key[0], key[2], key[4])
                if k2 in seen:
                    continue
                seen.add(k2)
                rowsx.append((cur, key, last, pct, nh))
            ydate, yv = now_yield[(ccy, ct)]
            A("**%s vs %s %dY, %s** (yield %g%% on %s, %.2f%% annual effective)" % (ccy, CURVES[ccy], ct, TYPE_LABELS.get(ty, ty), yv, ydate, annual(yv)))
            A("")
            A("| # | Product · plan | Term | Rate % | Spread bp | Effective | Own-history percentile |")
            A("|---|---|---|---|---|---|---|")
            for i, (cur, key, last, pct, nh) in enumerate(rowsx[:10], 1):
                term = key[4] if last["tenor_basis"] == "term" else "%s (%s used)" % (key[4] or "–", last["tenor_used"])
                A("| %d | %s | %s | %s | %s | %s | %s |" % (i, label(key), term, last["rate_pct"], fmt_bp(cur), last["effective_date"],
                                                         ("%.0f (n=%d)" % (pct, nh)) if nh >= 6 else "n=%d" % nh))
            if len(rowsx) > 10:
                A("")
                A("%d more in the CSV." % (len(rowsx) - 10))
            A("")
    # ---- headlines, computed from the sections above and placed after the introduction
    hl = ["## Headlines", ""]
    for ccy in ("USD", "JPY", "AUD"):
        for ct in (5, 10):
            for ty in sorted({k[3] for k in latest if k[:3] == ("crediting_rate", ccy, ct)}):
                items = latest[("crediting_rate", ccy, ct, ty)]
                cur, key, last, pct, nh = max(items, key=lambda x: x[0])
                spreads = sorted(x[0] for x in items)
                ydate, yv = now_yield[(ccy, ct)]
                hl.append("- **%s %dY, %s:** widest crediting-rate spread now is %s at %s%% (%s bp over %s %dY, %.2f%% annual effective on %s); range across %d series %s to %s bp." % (
                    ccy, ct, TYPE_LABELS.get(ty, ty), label(key), last["rate_pct"], fmt_bp(cur), CURVES[ccy], ct, annual(yv), ydate, len(items), fmt_bp(spreads[0]), fmt_bp(spreads[-1])))
    weak_list = []
    for (ins, ccy), v in sorted(agg.items(), key=lambda x: (x[0][1], x[0][0])):
        if weak(v):
            weak_list.append("%s %s (median R² %.2f)" % (insurers.get(ins, ins), ccy, med([x[1] for x in v])))
            continue
        hl.append("- **%s, %s pass-through:** best fit at a lag of about %d days, beta %.2f (median R² %.2f across %d series)." % (
            insurers.get(ins, ins), ccy, med_lag([x[2] for x in v]), med([x[0] for x in v]), med([x[1] for x in v]), len(v)))
    if weak_list:
        hl.append("- **No reliable pass-through lag** (yield moves explain little of the rate moves): %s." % "; ".join(weak_list))
    if "AUD" not in curves:
        hl.append("- **AUD:** not yet analysed; the RBA yield table could not be downloaded from this network (see Coverage).")
    hl.append("")
    lines[head_at:head_at] = hl
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("%d spread rows, report %s" % (len(rows), common.rel(REPORT)))
    for reason, n in skipped.items():
        print("  skipped %d: %s" % (n, reason))


def svg_lag_chart(title, subtitle, series, path):
    """R² against lag (days) - plain lines with markers; x is days, encoded as dates from 2000-01-01."""
    W, H = 760, 300
    L, R, T, B = 56, 16, 48, 36
    legend_rows = len(series)
    H_total = H + 18 * legend_rows + 8
    sx = lambda days: L + (W - L - R) * days / max(LAGS)
    sy = lambda v: T + (H - T - B) * (1 - v)
    css = ["svg{font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
           ".bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.grid{stroke:#e4e3df;stroke-width:1}"]
    dark = [".bg{fill:#1a1a19}.t1{fill:#f0efec}.t2{fill:#c3c2b7}.grid{stroke:#34332f}"]
    for i in range(len(series)):
        light, drk = PALETTE[i % len(PALETTE)]
        css.append(".s%d{stroke:%s}.f%d{fill:%s}" % (i, light, i, light))
        dark.append(".s%d{stroke:%s}.f%d{fill:%s}" % (i, drk, i, drk))
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">' % (W, H_total, W, H_total, esc(title)),
           "<style>%s @media (prefers-color-scheme: dark){%s}</style>" % ("".join(css), "".join(dark)),
           '<rect class="bg" width="100%" height="100%" rx="6"/>',
           '<text class="t1" x="%d" y="20" font-weight="600" font-size="14">%s</text>' % (L, esc(title)),
           '<text class="t2" x="%d" y="36">%s</text>' % (L, esc(subtitle))]
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (L, W - R, sy(t), sy(t)))
        out.append('<text class="t2" x="%d" y="%.1f" text-anchor="end">%g</text>' % (L - 6, sy(t) + 4, t))
    for lag in LAGS:
        out.append('<text class="t2" x="%.1f" y="%d" text-anchor="middle">%d</text>' % (sx(lag), H - B + 16, lag))
    out.append('<text class="t2" x="%d" y="%d" text-anchor="middle">lag, days before the effective date</text>' % ((L + W - R) / 2, H - 4))
    base = dt.date(2000, 1, 1)
    for i, (label, pts) in enumerate(series):
        xy = [(sx((d(x) - base).days), sy(v)) for x, v in pts]
        out.append('<path class="s%d" d="M%s" fill="none" stroke-width="2"/>' % (i, " L".join("%.1f,%.1f" % p for p in xy)))
        for x, y in xy:
            out.append('<circle class="f%d" cx="%.1f" cy="%.1f" r="4" stroke="#fcfcfb" stroke-width="1.5"/>' % (i, x, y))
    for i, (label, _) in enumerate(series):
        lx = L
        ly = H + 4 + 18 * i
        out.append('<rect class="f%d" x="%.1f" y="%d" width="14" height="4" rx="2"/>' % (i, lx, ly + 4))
        out.append('<text class="t1" x="%.1f" y="%d">%s</text>' % (lx + 20, ly + 10, esc(label)))
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")
    return True


if __name__ == "__main__":
    main()
