"""Independent sample re-check of rate CSVs against the saved snapshots (README rule).

No network, and none of the collectors' parsers: each snapshot is read with the
generic HTML table reader below, every table cell gets a set of labels (its column
headers, the text cells of its row, the table's caption and a few page-level labels
such as the rate period), and each checked CSV row is turned into the labels its
value must carry (currency, term, age band, plan, effective date). The row matches
when exactly one cell value carries all of them (or several that agree) and equals
value_pct; a not_offered row matches when the cell shows "-" / a "not offered"
note, or no cell carries its labels.

Rows checked per product:
  * a reproducible random sample of published rows: ceil(10%) of each rate CSV
    (at least one per CSV), topped up to at least 20 rows and 10% of the product,
  * every outlier among published rows: per series (CSV, currency, term, age band,
    sex) a value more than 3 standard deviations from the series mean, a move of more
    than 1pp from the previous period (10pp for participation rates), or a value
    outside 0-15% (0-300% for participation rates),
  * every not_offered row.

Usage:
    python3 scripts/recheck_candidates_a.py                  # every product configured here
    python3 scripts/recheck_candidates_a.py dfl-premier-journey
    python3 scripts/recheck_candidates_a.py --all            # check every row, not a sample
    python3 scripts/recheck_candidates_a.py --json out.json  # also write per-row results
    python3 scripts/recheck_candidates_a.py --root DIR       # repository root (default: this script's parent)

Exit status 1 if any checked row does not match.
"""

import csv
import glob
import html.parser
import json
import math
import random
import re
import statistics
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = "recheck-a-2026-11-17"

# Rows in scope per product (default: every row). tdf-fiveten-world-4 was fully
# re-checked through retrieved_date 2026-11-16; only later rows are in scope here.
SCOPE = {"tdf-fiveten-world-4": lambda r: r["retrieved_date"] > "2026-11-16"}


# ---------------------------------------------------------------- HTML tables

def norm(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("〜", "~").replace("～", "~")
    return re.sub(r"\s+", "", text)


class _Tables(html.parser.HTMLParser):
    """Tables as rows of cells, with browser-like implicit <tr> (a cell after </tr> opens a row)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.cell, self.pre, self.skip = [], [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        a = dict(attrs)
        if tag == "table":
            self.stack.append({"rows": [], "open": False, "caption": "", "pre_at": len(self.pre)})
        elif not self.stack:
            return
        elif tag == "tr":
            self.stack[-1]["rows"].append([])
            self.stack[-1]["open"] = True
        elif tag in ("td", "th"):
            if not self.stack[-1]["open"]:
                self.stack[-1]["rows"].append([])
                self.stack[-1]["open"] = True

            def span(v):
                v = re.sub(r"\D", "", v or "")
                return int(v) if v else 1

            self.cell = {"text": "", "rs": span(a.get("rowspan")), "cs": span(a.get("colspan"))}
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += " "

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
            return
        if not self.stack:
            return
        if tag in ("td", "th") and self.cell is not None:
            self.stack[-1]["rows"][-1].append(self.cell)
            self.cell = None
        elif tag in ("tr", "thead", "tbody", "tfoot"):
            self.stack[-1]["open"] = False
        elif tag == "table":
            self.tables.append(self.stack.pop())

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell["text"] += data
        elif self.stack:
            self.stack[-1]["caption"] += data
        elif data.strip():
            self.pre.append(norm(data))


def _grid(rows):
    """Expand rowspan/colspan into a grid of normalized texts."""
    out, pending = [], {}
    for r, row in enumerate(rows):
        line, c, i = [], 0, 0
        while i < len(row) or (r, c) in pending:
            if (r, c) in pending:
                line.append(pending.pop((r, c)))
                c += 1
                continue
            cell = row[i]
            i += 1
            txt = norm(cell["text"])
            for dr in range(1, cell["rs"]):
                for dc in range(cell["cs"]):
                    pending[(r + dr, c + dc)] = txt
            for _ in range(cell["cs"]):
                line.append(txt)
                c += 1
        out.append(line)
    return out


def read_page(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    p = _Tables()
    p.feed(text)
    tables = []
    for t in sorted(p.tables, key=lambda t: t["pre_at"]):
        tables.append({"grid": _grid(t["rows"]), "caption": norm(t["caption"]), "pre": p.pre[:t["pre_at"]]})
    return tables, "".join(p.pre)


# ---------------------------------------------------------------- cell labels

VALUE = re.compile(r"^-?\d+\.\d+%?$")
DASH = re.compile(r"^[-－―‐−ー]+$")


def is_value(text):
    return bool(VALUE.match(text) or DASH.match(text) or "ありません" in text or "停止中" in text)


def derived(label):
    """The label plus canonical forms for ages and dates."""
    out = {label}
    m = re.fullmatch(r"(\d+)歳?~(\d+)歳", label)
    if m:
        out.add("AGE:%s-%s" % m.groups())
    m = re.fullmatch(r"(\d+)歳", label)
    if m:
        out.add("AGE:%s" % m.group(1))
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2})", label)
    if m:
        out.add("D:20%s-%s-%s" % m.groups())
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})~", label)
    if m:
        out.add("D:%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))))
    return out


def cells(tables, page_date, table_labels):
    """Every value cell as (table index, labels, text)."""
    out = []
    for ti, t in enumerate(tables):
        grid = t["grid"]
        tlabels = set(table_labels(t)) | ({"CAP:" + t["caption"]} if t["caption"] else set())
        first = next((i for i, row in enumerate(grid) if any(is_value(x) for x in row)), None)
        if first is None:
            continue
        header = grid[:first]
        sub = {}
        for row in grid[first:]:
            if not any(is_value(x) for x in row):
                sub = {c: x for c, x in enumerate(row) if x}  # a label row inside the body
                continue
            row_labels = set()
            for x in row:
                if x and not is_value(x):
                    row_labels |= derived(x)
            if page_date and not any(l.startswith("D:") for l in row_labels):
                row_labels.add("D:" + page_date)
            for c, x in enumerate(row):
                if not is_value(x):
                    continue
                labels = set(row_labels) | tlabels
                for h in header:
                    if c < len(h) and h[c]:
                        labels |= derived(h[c])
                if c in sub:
                    labels |= derived(sub[c])
                out.append((ti, labels, x))
    return out


def page_dates(text, patterns):
    found = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            y, mo, d = m.groups()
            found.add("%s-%02d-%02d" % (y, int(mo), int(d)))
    return found


# ---------------------------------------------------------------- per insurer / product

def last_match(pre, pattern):
    for tok in reversed(pre):
        m = re.search(pattern, tok)
        if m:
            return m.group(1)
    return None


def no_labels(t):
    return []


CUR = {
    "dfl": {"USD": "米ドル", "AUD": "豪ドル", "JPY": "円貨"},
    "sml": {"USD": "米ドル", "AUD": "豪ドル", "JPY": "円"},
    "msp": {"USD": "米ドル", "AUD": "豪ドル", "JPY": "円", "EUR": "ユーロ", "NZD": "NZドル"},
    "tdf": {"USD": "米ドル", "AUD": "豪ドル"},
    "mys": {"USD": "米ドル建", "AUD": "豪ドル建"},
}


def age(r):
    return "AGE:" + r["age_band"]


def D(r):
    return "D:" + r["effective_date"]


# Each locator: row -> dict(req=[label | tuple of alternatives | "~substring"], forbid=[...], table=index)

def loc_dfl_simple(r):
    return {"req": [CUR["dfl"][r["currency"]], r["term_years"] + "年", D(r)]}


PAY = {"pay10": "10年", "pay15-20": "15・20年", "pay25-40": "25・30・35・40年"}
DEF = {"def1-4": "1~4年", "def5-6": "5~6年", "def7-10": "7~10年"}


def loc_dfl_partner(r):
    parts = r["plan"].split("_")
    req = [CUR["dfl"][r["currency"]], D(r)]
    if parts[1] == "kakutei":
        req += [PAY[parts[2]], DEF[parts[3]]]
    else:
        req += [parts[2][1:] + "%", age(r)]
    return {"req": req}


def loc_dfl_present(r):
    req = [CUR["dfl"][r["currency"]], r["term_years"] + "年", D(r)]
    if r["plan"] == "shibo_holdback_9m":
        req.append("9ヵ月")
    elif r["plan"] == "shibo_holdback_5y":
        req.append("5年")
    return {"req": req}


def loc_dfl_story(r):
    cur = CUR["dfl"][r["currency"]]
    if r["plan"] == "shushin_nashi":
        # 受取回数 N: the first payment is at issue, so the term on file is N - 1.
        return {"req": [cur, "%d回" % (int(r["term_years"]) + 1), D(r)], "forbid": ["1倍", "3倍"],
                "table": 0 if r["metric"] == "crediting_rate" else 1}
    m = re.fullmatch(r"shushin_ari_(?:(cover|payout)_)?x(\d)_n(\d+)", r["plan"])
    part, mult, n = m.groups()
    table = 1 if r["metric"] == "crediting_rate" else (0 if part == "cover" else 2)
    return {"req": [cur, mult + "倍", n + "回", r["term_years"] + "年", D(r)], "table": table}


SML_PLAN = {"declaration_3": "3つの健康告知プラン", "no_declaration": "健康告知なしプラン",
            "target": "目標額設定あり", "no_target": "目標額設定なし"}


def loc_sml_fh(r):
    req = [CUR["sml"][r["currency"]], D(r), "~" + SML_PLAN[r["plan"]]]
    if r["metric"] == "planned_rate":
        req.append("予定利率")
    elif r["metric"] == "net_yield":
        req.append("%s歳・%s性" % (r["age_band"], {"M": "男", "F": "女"}[r["sex"]]))
    return {"req": req}


def loc_sml_idx(r):
    req = [CUR["sml"][r["currency"]], "据置期間%s年" % r["term_years"], D(r), "~" + SML_PLAN[r["plan"]]]
    if r["metric"] == "crediting_rate":
        req.append("積立利率")
    elif r["metric"] == "participation_rate":
        req.append("連動率")
    return {"req": req}


def loc_msp_61(r):
    cur = CUR["msp"][r["currency"]]
    plan = r["plan"]
    if plan.startswith("mankiki_jujitsu_g"):
        return {"req": [cur, r["term_years"] + "年", plan.rsplit("g", 1)[1] + "%保障型", D(r)]}
    if plan == "tsumitate":
        return {"req": [cur, r["term_years"] + "年", "積立利率", D(r)]}
    if plan == "sakidori_anshin":
        return {"req": [cur, age(r), "契約年齢別積立利率", D(r)]}
    m = re.fullmatch(r"ukedori_kakutei_def([\d-]+)_pay(\d+)", plan)
    if m:
        return {"req": [cur, m.group(1).replace("-", "年~") + "年", m.group(2) + "年", "年金支払期間別積立利率", D(r)]}
    m = re.fullmatch(r"ukedori_shushin_def([\d-]+)", plan)
    return {"req": [cur, m.group(1).replace("-", "年~") + "年", age(r), D(r)], "forbid": ["契約年齢別積立利率"]}


MSP51_PLAN = {"target": "ターゲットコース", "teiki_shiharai": "定期支払コース", "nenkin_genshi_jujitsu": "年金原資充実コース"}


def loc_msp_51(r):
    return {"req": ["TITLE:" + CUR["msp"][r["currency"]], MSP51_PLAN[r["plan"]], r["term_years"] + "年", D(r)]}


def labels_msp_51(t):
    # each currency's table sits under a title block naming the currency
    return ["TITLE:" + (t["pre"][-1] if t["pre"] else "")]


SEX = {"M": "男性", "F": "女性"}
RIDER = "保険金最低保証特約を付加した場合"


def loc_tdf_pw6(r):
    if r["metric"] == "crediting_rate":
        if r["currency"] == "JPY":
            req = [SEX[r["sex"]], age(r), D(r)]
            if r["plan"] == "jpy_20m":
                return {"req": req + ["~2,000万円"]}
            return {"req": req, "forbid": ["~2,000万円"]}
        req = ["CUR:" + CUR["tdf"][r["currency"]], SEX[r["sex"]], age(r), D(r)]
        example = "~年間"  # the net-yield example tables label each row with the guarantee period
        if r["plan"] == "fx_rider":
            return {"req": req + [RIDER], "forbid": [example]}
        return {"req": req, "forbid": [RIDER, example]}
    # net_yield: the two example tables, captioned without / with the rider
    m = re.fullmatch(r"fx_(no_rider|rider)_(periodic|optional)", r["plan"])
    rider, course = m.groups()
    course_label = ("定期受取コース", "定期支払コース") if course == "periodic" else ("任意受取コース*", "積立コース*")
    cap = "~" + ("<保険金最低保証特約を付加しない場合>" if rider == "no_rider" else "<保険金最低保証特約を付加した場合>")
    return {"req": [CUR["tdf"][r["currency"]], SEX[r["sex"]], age(r), r["term_years"] + "年間", course_label, cap, D(r)]}


def labels_tdf_pw6(t):
    cur = last_match(t["pre"], r"^運用通貨:(.+)$")
    return ["CUR:" + cur] if cur else []


TDF_COURSE = {"jpy_nenkin": "年金重視コース", "jpy_kaigo": "介護認知症保障コース"}


def loc_tdf_annuity(r):
    if r["currency"] == "JPY":
        course = TDF_COURSE[r["plan"]]
        if r["metric"] == "crediting_rate":
            return {"req": [course, r["term_years"] + "年", "積立利率", D(r)]}
        return {"req": [course, r["term_years"] + "年間", "~実質的な利回り", D(r)]}
    rider = "付加あり" if r["plan"] == "fx_rider" else "付加なし"
    col = "積立利率" if r["metric"] == "crediting_rate" else "~実質的な利回り"
    return {"req": [rider, CUR["tdf"][r["currency"]], r["term_years"] + "年間", col, D(r)]}


MYS_TYPE = {"unyou": "運用重視タイプ", "balance": "バランスタイプ"}


def loc_mys(r):
    return {"req": [CUR["mys"][r["currency"]], age(r), "予定利率", r["term_years"] + "年",
                    "TYPE:" + MYS_TYPE[r["plan"]], D(r)]}


def labels_mys(t):
    typ = last_match(t["pre"], r"■(運用重視タイプ|バランスタイプ)")
    return ["TYPE:" + typ] if typ else []


def loc_cal(r):
    payout, remaining = re.fullmatch(r"payout_(\d+)y(?:_remaining_(\d+)y)?", r["plan"]).groups()
    if r["metric"] == "mva_reference_rate":
        return {"req": ["PAYOUT:" + payout, remaining + "年", "調整用基準指標金利", D(r)]}
    col = "積立利率" if r["metric"] == "crediting_rate" else "基準指標金利"
    return {"req": [payout + "年", col, D(r)], "forbid": ["~PAYOUT:"]}


def labels_cal(t):
    p = last_match(t["pre"], r"年金支払期間(\d+)年の場合")
    return ["PAYOUT:" + p] if p else []


DATE_MSP = [r"(\d{4})年(\d{1,2})月(\d{1,2})日~\d{4}年\d{1,2}月\d{1,2}日が契約日"]
DATE_TDF = [r"積立利率設定期間:?(\d{4})年(\d{1,2})月(\d{1,2})日~"]
DATE_MYS = [r"●(\d{4})年(\d{1,2})月(\d{1,2})日~"]
DATE_CAL = [r"契約日が(\d{4})年(\d{1,2})月(\d{1,2})日~", r"(\d{4})年(\d{1,2})月(\d{1,2})日~\d{4}年\d{1,2}月末日が解約日"]

PRODUCTS = {
    "dfl-premier-journey": (loc_dfl_simple, no_labels, None),
    "dfl-premier-road-m": (loc_dfl_simple, no_labels, None),
    "dfl-shisu-rendou-nenkin-2": (loc_dfl_simple, no_labels, None),
    "dfl-premier-partner": (loc_dfl_partner, no_labels, None),
    "dfl-premier-present-5": (loc_dfl_present, no_labels, None),
    "dfl-premier-story-5": (loc_dfl_story, no_labels, None),
    "sml-fullheart-jroad-global-3": (loc_sml_fh, no_labels, None),
    "sml-tanoshimi-global-3-shisuu": (loc_sml_idx, no_labels, None),
    "msp-ashitano-yorokobi-2": (loc_msp_61, no_labels, DATE_MSP),
    "msp-broadway-world-3": (loc_msp_51, labels_msp_51, DATE_MSP),
    "tdf-premium-world-6": (loc_tdf_pw6, labels_tdf_pw6, DATE_TDF),
    "tdf-yasashii-yen-nenkin-2": (loc_tdf_annuity, no_labels, DATE_TDF),
    "tdf-fiveten-world-4": (loc_tdf_annuity, no_labels, DATE_TDF),
    "mys-fc-everybody-plus": (loc_mys, labels_mys, DATE_MYS),
    "cal-jibun-nenkin-kaigo-plus": (loc_cal, labels_cal, DATE_CAL),
}


# ---------------------------------------------------------------- matching

def has(labels, want):
    if isinstance(want, tuple):
        return any(has(labels, w) for w in want)
    if want.startswith("~"):
        return any(want[1:] in l for l in labels)
    return want in labels


def to_dec(text):
    try:
        return Decimal(text.rstrip("%"))
    except InvalidOperation:
        return None


_pages = {}


def page(snapshot, pid):
    key = (snapshot, pid)
    if key not in _pages:
        _, table_labels, date_patterns = PRODUCTS[pid]
        tables, text = read_page(ROOT / snapshot)
        pdate, err = None, None
        if date_patterns:
            found = page_dates(text, date_patterns)
            if len(found) == 1:
                pdate = found.pop()
            else:
                err = "page period not unique: %s" % sorted(found)
        _pages[key] = (cells(tables, pdate, table_labels), err)
    return _pages[key]


def check_row(pid, r):
    found, err = page(r["snapshot"], pid)
    if err:
        return "ERROR", err
    spec = PRODUCTS[pid][0](r)
    hits = [(ti, x) for ti, labels, x in found
            if all(has(labels, w) for w in spec["req"])
            and not any(has(labels, w) for w in spec.get("forbid", []))
            and ("table" not in spec or ti == spec["table"])]
    if r["status"] == "not_offered":
        if not hits or all(to_dec(x) is None for _, x in hits):
            return "MATCH", "not offered (%s)" % ("no cell" if not hits else hits[0][1])
        return "MISMATCH", "file not_offered, snapshot shows %s" % sorted({x for _, x in hits})
    if not hits:
        return "MISMATCH", "no cell found for %s" % spec["req"]
    vals = {to_dec(x) for _, x in hits}
    if len(vals) > 1:
        return "MISMATCH", "ambiguous: %d cells with %s" % (len(hits), sorted({x for _, x in hits}))
    v = vals.pop()
    if v is None:
        return "MISMATCH", "file %s, snapshot shows %s" % (r["value_pct"], hits[0][1])
    if v != Decimal(r["value_pct"]):
        return "MISMATCH", "file %s, snapshot %s" % (r["value_pct"], hits[0][1])
    return "MATCH", "%s%s" % (hits[0][1], " (%d identical cells)" % len(hits) if len(hits) > 1 else "")


# ---------------------------------------------------------------- selection

def load_rows(pid):
    rows = []
    for f in sorted(glob.glob(str(ROOT / "data" / "rates" / (pid + "__*.csv")))):
        with open(f, newline="", encoding="utf-8") as fh:
            for i, r in enumerate(csv.DictReader(fh), start=2):
                r["_file"] = Path(f).name
                r["_line"] = i
                rows.append(r)
    return rows


def outliers(rows):
    """Published rows flagged by the outlier rules: id(row) -> reason."""
    series = {}
    for r in rows:
        if r["status"] == "published":
            series.setdefault((r["_file"], r["currency"], r["term_years"], r["age_band"], r["sex"]), []).append(r)
    flagged = {}
    for rs in series.values():
        rs.sort(key=lambda r: r["effective_date"])
        vals = [float(r["value_pct"]) for r in rs]
        lo, hi, jump = (0, 300, 10) if rs[0]["metric"] == "participation_rate" else (0, 15, 1)
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals) if len(vals) > 2 else 0
        for i, (r, v) in enumerate(zip(rs, vals)):
            why = []
            if sd and abs(v - mean) > 3 * sd:
                why.append(">3sd (mean %.2f, sd %.2f)" % (mean, sd))
            if i and abs(v - vals[i - 1]) > jump:
                why.append("jump %+.2fpp from %s" % (v - vals[i - 1], rs[i - 1]["effective_date"]))
            if not lo <= v <= hi:
                why.append("outside %d-%d" % (lo, hi))
            if why:
                flagged[id(r)] = "; ".join(why)
    return flagged


def select(pid, rows, flagged, check_all):
    rng = random.Random("%s:%s" % (SEED, pid))
    chosen = {}
    if check_all:
        return {id(r): (r, "all") for r in rows}
    published = [r for r in rows if r["status"] == "published"]
    by_file = {}
    for r in published:
        by_file.setdefault(r["_file"], []).append(r)
    for _, rs in sorted(by_file.items()):
        for r in rng.sample(rs, max(1, math.ceil(len(rs) * 0.10))):
            chosen[id(r)] = (r, "sample")
    rest = [r for r in published if id(r) not in chosen]
    need = max(20, math.ceil(len(published) * 0.10)) - len(chosen)
    if need > 0 and rest:
        for r in rng.sample(rest, min(need, len(rest))):
            chosen[id(r)] = (r, "sample")
    for r in published:
        if id(r) in flagged:
            chosen[id(r)] = (r, "sample+outlier" if id(r) in chosen else "outlier")
    for r in rows:
        if r["status"] == "not_offered":
            chosen[id(r)] = (r, "not_offered")
    return chosen


def run(pid, check_all, report):
    all_rows = load_rows(pid)
    flagged = outliers(all_rows)  # series statistics use every row on file
    scope = SCOPE.get(pid)
    rows = [r for r in all_rows if scope(r)] if scope else all_rows
    in_scope = {id(r) for r in rows}
    flagged = {k: v for k, v in flagged.items() if k in in_scope}
    chosen = select(pid, rows, flagged, check_all)
    counts = {"rows": len(rows), "published": sum(r["status"] == "published" for r in rows),
              "sampled": 0, "outliers": 0, "not_offered": 0, "checked": 0, "matched": 0}
    mismatches, outlier_rows = [], []
    for r, why in sorted(chosen.values(), key=lambda x: (x[0]["_file"], x[0]["_line"])):
        status, detail = check_row(pid, r)
        counts["checked"] += 1
        counts["matched"] += status == "MATCH"
        counts["sampled"] += "sample" in why or why == "all"
        counts["not_offered"] += why == "not_offered"
        entry = {"file": r["_file"], "line": r["_line"],
                 "key": "|".join(r[k] for k in ("currency", "term_years", "age_band", "sex", "effective_date")),
                 "value": r["value_pct"], "status": r["status"], "snapshot": r["snapshot"],
                 "result": status, "detail": detail}
        if id(r) in flagged:
            counts["outliers"] += 1
            entry["outlier"] = flagged[id(r)]
            outlier_rows.append(entry)
        if status != "MATCH":
            mismatches.append(entry)
        report.append(dict(entry, product=pid, selected=why))
    counts["latest_retrieved"] = max(r["retrieved_date"] for r in rows)
    print("%-32s %5d rows in scope (%d published): checked %d (%d sampled, %d outliers, %d not_offered), "
          "matched %d  %s" % (pid, counts["rows"], counts["published"], counts["checked"], counts["sampled"],
                              counts["outliers"], counts["not_offered"], counts["matched"],
                              "OK" if not mismatches else "FAIL"))
    for e in outlier_rows:
        print("    outlier %s:%d %s %s [%s] -> %s" % (e["file"], e["line"], e["key"], e["value"], e["outlier"], e["result"]))
    for e in mismatches:
        print("    %s %s:%d %s %s (%s): %s" % (e["result"], e["file"], e["line"], e["key"], e["value"],
                                              e["snapshot"], e["detail"]))
    return not mismatches, counts


def main():
    global ROOT
    args = sys.argv[1:]
    out = None
    for flag in ("--json", "--root"):
        if flag in args:
            i = args.index(flag)
            if flag == "--json":
                out = args[i + 1]
            else:
                ROOT = Path(args[i + 1]).resolve()
            del args[i:i + 2]
    check_all = "--all" in args
    pids = [a for a in args if not a.startswith("--")] or sorted(PRODUCTS)
    report, ok, summary = [], True, {}
    for pid in pids:
        good, counts = run(pid, check_all, report)
        ok &= good
        summary[pid] = counts
    if out:
        Path(out).write_text(json.dumps({"summary": summary, "rows": report}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
