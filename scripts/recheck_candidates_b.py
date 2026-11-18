"""Independent sample re-check of collected rates against the saved snapshots.

README rule: an agent that did not collect the data re-checks about 10% of the
rows per insurer against the snapshots under sources/, plus all outliers. This
script does that for the products it knows (see PARSERS), with its OWN parsing
of every snapshot format: it imports nothing from the collectors (nwl.py,
met.py, mnl.py, nli.py, orx.py, pgf.py) or from check_offline_parse.py.

For each product:
  1. all rows of data/rates/<product>__*.csv are read;
  2. a reproducible random sample is drawn (seed "recheck-sample:<product>"):
     every (metric, plan, currency) group contributes at least one row, every
     (metric, plan) group at least ceil(10%) of its rows, and the sample is
     topped up to at least 20 rows (or all rows when there are fewer);
  3. outliers are added: a published value outside 0-15%, more than 3 standard
     deviations from its series mean (series = metric, plan, currency, term,
     age band, sex; at least 5 points), or more than 1 percentage point away
     from the previous point of its series (both rows of the jump are added);
  4. each selected row is looked up in its snapshot by its own key (currency,
     term, age band, plan dimensions, effective date) and value and status are
     compared.

No network. Usage (from the repository root, or set RECHECK_ROOT):
    python3 scripts/recheck_candidates_b.py                 # the default product list
    python3 scripts/recheck_candidates_b.py orx-moonshot    # some products
    python3 scripts/recheck_candidates_b.py --all-rows ...  # check every row, not a sample
    python3 scripts/recheck_candidates_b.py --json out.json # also write the full result

Exit status 1 if any checked row does not match.
"""

import argparse
import csv
import glob
import html.parser
import json
import math
import os
import random
import re
import statistics
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

ROOT = Path(os.environ.get("RECHECK_ROOT") or Path(__file__).resolve().parent.parent)
RATES = ROOT / "data" / "rates"

DEFAULT_PRODUCTS = [
    "nwl-form-designated-currency-annuity",
    "nwl-form-designated-currency-special-whole-life-25",
    "nwl-form-designated-currency-special-whole-life",
    "nwl-form-designated-currency-whole-life",
    "nwl-form-index-linked-annuity-2",
    "nwl-form-index-linked-annuity",
    "nwl-form-index-linked-whole-life",
    "nwl-form-rate-linked-whole-life",
    "nwl-form-special-whole-life-fixed-accumulation",
    "nwl-form-survival-benefit-whole-life",
    "met-be-with-you-plus-2",
    "met-sunny-garden-prime",
    "mnl-mirai-shushin-v2",
    "nli-longdream-gold3",
    "orx-moonshot",
    "pgf-hosho-sentaku-neo-usd",
    "pgf-sodateru-kajitsu",
]

CUR_JA = {"円": "JPY", "米ドル": "USD", "豪ドル": "AUD"}
DASHES = {"-", "－", "—", "–", "ー", "‐"}


# ---------------------------------------------------------------- helpers

def norm(text):
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def nospace(text):
    return re.sub(r"\s+", "", norm(text))


def pct(text):
    """'1.45%' / '年4.21%' / '4.21' -> Decimal; dash -> '-'; else None."""
    t = nospace(text)
    if t in DASHES:
        return "-"
    m = re.fullmatch(r"年?(-?\d+(?:\.\d+)?)%?", t)
    return Decimal(m.group(1)) if m else None


def is_value_text(text):
    t = nospace(text)
    return t in DASHES or re.fullmatch(r"年?-?\d+\.\d+%", t) is not None


def age_label(text):
    t = nospace(text).replace("※", "")
    for pat, fmt in ((r"(\d+)歳~(\d+)歳", "{0}-{1}"), (r"(\d+)~(\d+)歳", "{0}-{1}"),
                     (r"(\d+)歳以上(\d+)歳以下", "{0}-{1}"),
                     (r"(\d+)歳以下", "0-{0}"), (r"(\d+)歳以上", "{0}+"), (r"(\d+)歳", "{0}")):
        m = re.fullmatch(pat, t)
        if m:
            return fmt.format(*m.groups())
    return None


def first_date(text):
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", norm(text))
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def read_text(path):
    raw = (ROOT / path).read_bytes()
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- a small DOM

VOID = {"br", "img", "meta", "link", "input", "hr", "area", "base", "col", "embed", "source", "wbr", "param", "track"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    def text(self):
        return "".join(ch.text() if isinstance(ch, Node) else ch for ch in self.children)

    def cls(self):
        return self.attrs.get("class") or ""

    def iter(self):
        for ch in self.children:
            if isinstance(ch, Node):
                yield ch
                yield from ch.iter()

    def find_all(self, tag):
        return [n for n in self.iter() if n.tag == tag]


class TreeBuilder(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        if tag not in ("script", "style"):
            self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if not self.skip:
            self.cur.children.append(data)


def dom(path):
    b = TreeBuilder()
    b.feed(read_text(path))
    b.close()
    return b.root


def table_grid(table):
    """Expand a <table> (not its nested tables) into {(r, c): cell}; cell = dict(text, th, id)."""
    rows = []

    def walk(node):
        for ch in node.children:
            if not isinstance(ch, Node) or ch.tag == "table":
                continue
            if ch.tag == "tr":
                rows.append(ch)
            else:
                walk(ch)

    walk(table)
    grid, cid = {}, 0
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr.children:
            if not isinstance(cell, Node) or cell.tag not in ("td", "th"):
                continue
            while (r, c) in grid:
                c += 1
            rs = int(cell.attrs.get("rowspan") or 1)
            cs = int(cell.attrs.get("colspan") or 1)
            obj = {"text": norm(cell.text()), "th": cell.tag == "th", "id": cid}
            cid += 1
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = obj
            c += cs
    return grid


def value_cells(grid):
    """Yield (cell, row labels [(text, column header)], column labels [text]) for every value cell."""
    if not grid:
        return
    nrows = max(r for r, _ in grid) + 1
    ncols = max(c for _, c in grid) + 1
    is_val = {pos: is_value_text(cell["text"]) for pos, cell in grid.items()}
    data_rows = {r for (r, c), v in is_val.items() if v}
    seen = set()
    for r in range(nrows):
        for c in range(ncols):
            cell = grid.get((r, c))
            if cell is None or not is_val[(r, c)] or cell["id"] in seen:
                continue
            seen.add(cell["id"])
            row_labels, col_labels, used = [], [], set()
            for k in range(c):
                lab = grid.get((r, k))
                if lab is None or is_val[(r, k)] or lab["id"] in used:
                    continue
                used.add(lab["id"])
                head, hid = [], set()
                for i in range(nrows):
                    h = grid.get((i, k))
                    if i not in data_rows and h is not None and h["id"] not in hid:
                        hid.add(h["id"])
                        head.append(h["text"])
                row_labels.append((lab["text"], " ".join(head)))
            for i in range(r):
                lab = grid.get((i, c))
                if lab is None or is_val[(i, c)] or lab["id"] in used:
                    continue
                used.add(lab["id"])
                col_labels.append(lab["text"])
            yield cell, row_labels, col_labels


# ---------------------------------------------------------------- NWL (Nissay Wealth)

NWL_FORMS = {
    # product -> (hub section title, {base page stem: title inside 基準金利 [...]}), all NFKC without spaces
    "nwl-form-designated-currency-annuity": (
        "指定通貨建個人年金保険",
        {"gu_int_rate14_base": "指定通貨建個人年金保険", "gu_int_rate10_base": "指定通貨建個人年金保険"}),
    "nwl-form-index-linked-annuity": (
        "指数連動型年金特約付指定通貨建個人年金保険",
        {"gu_int_rate6_base": "指数連動型年金特約付指定通貨建個人年金保険"}),
    "nwl-form-index-linked-annuity-2": (
        "指定通貨建積立利率金利連動型年金指数連動型年金特約II付",
        {"gu_int_rate9_base": "指定通貨建積立利率金利連動型年金指数連動型年金特約II付"}),
    "nwl-form-designated-currency-whole-life": (
        "指定通貨建終身保険", {"gu_int_rate7_base": "指定通貨建終身保険"}),
    "nwl-form-designated-currency-special-whole-life": (
        "指定通貨建特別終身保険", {"gu_int_rate8_base": "指定通貨建特別終身保険"}),
    "nwl-form-designated-currency-special-whole-life-25": (
        "指定通貨建特別終身保険(25)", {"gu_int_rate16_base": "指定通貨建特別終身保険(25)"}),
    "nwl-form-special-whole-life-fixed-accumulation": (
        "積立金区分型終身保険特約(確定積増型)付指定通貨建特別終身保険",
        {"gu_int_rate17_base": "積立金区分型終身保険特約(確定積増型)付指定通貨建特別終身保険"}),
    "nwl-form-index-linked-whole-life": (
        "積立金区分型終身保険特約付指定通貨建特別終身保険",
        {"gu_int_rate11_base": "積立金区分型終身保険特約付指定通貨建特別終身保険"}),
    "nwl-form-survival-benefit-whole-life": (
        "積立利率金利連動型生存給付金付終身保険(指定通貨建)",
        {"gu_int_rate4_base": "積立利率金利連動型生存給付金付終身保険(指定通貨建)"}),
    "nwl-form-rate-linked-whole-life": (
        "積立利率金利連動型終身保険",
        {"gu_int_rate2_base": "積立利率金利連動型終身保険(確定積立金区分型)",
         "gu_int_rate3_base": "積立利率金利連動型終身保険(米ドル建・確定積立金区分型)"}),
}
NWL_TYPES = {"指数連動型": "index", "指数連動・確定積増型": "index_fixed"}


def nwl_sections(path):
    """[(title, [events])] for the content area; events are ('text', s) or ('table', grid)."""
    root = dom(path)
    content = next((n for n in root.iter() if "tpl-body__content" in n.cls().split()), root)
    sections = [("", [])]

    def walk(node):
        for ch in node.children:
            if isinstance(ch, str):
                if ch.strip():
                    sections[-1][1].append(("text", norm(ch)))
                continue
            c = ch.cls()
            if "tpl-title--l" in c or "tpl-title--pagetitle" in c:
                sections.append((nospace(ch.text()), []))
                continue
            if ch.tag == "table":
                sections[-1][1].append(("table", table_grid(ch)))
                continue
            walk(ch)

    walk(content)
    return sections


def page_currency(text):
    t = nospace(text)
    for ja, code in (("米ドル建", "USD"), ("豪ドル建", "AUD"), ("円建", "JPY")):
        if ja in t:
            return code
    return None


def nwl_points(events, page_cur):
    """Parse one section's events into points (attrs + value) and the 適用期間 start."""
    points, ratio, period = [], None, None
    for kind, ev in events:
        if kind == "text":
            t = nospace(ev)
            if "生存給付金額の2倍以上" in t:
                ratio = "r2ge"
            elif "生存給付金額の2倍未満" in t:
                ratio = "r2lt"
            if period is None and "適用期間" in t:
                period = first_date(t)
            continue
        for cell, row_labels, col_labels in value_cells(ev):
            attrs, clash = {"ratio": ratio}, []

            def put(k, v):
                if attrs.get(k) not in (None, v):
                    clash.append((k, attrs[k], v))
                attrs[k] = v

            labels = list(row_labels) + [(t, "") for t in col_labels]
            for text, head in labels:
                t = nospace(text)
                m = re.search(r"指定通貨:(円|米ドル|豪ドル)", t)
                if m:
                    put("cur", CUR_JA[m.group(1)])
                elif t in CUR_JA:
                    put("cur", CUR_JA[t])
                if "3,000万円未満" in t:
                    put("prem", "lt3000")
                elif "3,000万円以上" in t:
                    put("prem", "ge3000")
                a = age_label(t)
                if a:
                    put("age", a)
                if t in NWL_TYPES:
                    put("type", NWL_TYPES[t])
                h = nospace(head)
                if t in ("20年未満", "20年以上") and "生存給付金" in h:
                    put("paydur", "lt20" if t.endswith("未満") else "ge20")
                elif re.fullmatch(r"\d+年", t):
                    if "受取" in h:
                        put("payout", t[:-1])
                    elif "据置" in h:
                        put("term", t[:-1])
                    else:
                        put("year?", t[:-1])
            if attrs.get("cur") is None and page_cur:
                attrs["cur"] = page_cur
            points.append({"attrs": attrs, "value": pct(cell["text"]), "clash": clash})
    return points, period


def nwl_expected(pid, row):
    e = {"cur": row["currency"], "term": row["term_years"] or None, "age": row["age_band"] or None}
    plan = row["plan"]
    if pid == "nwl-form-designated-currency-annuity":
        e["prem"] = {"prem_lt3000": "lt3000", "prem_ge3000": "ge3000"}.get(plan)
    elif pid == "nwl-form-index-linked-annuity-2":
        e["payout"] = plan[1:]
    elif pid == "nwl-form-index-linked-whole-life":
        e["type"] = plan
    elif pid == "nwl-form-survival-benefit-whole-life":
        r, p = plan.split("_")
        e["ratio"], e["paydur"] = r, {"payge20": "ge20", "paylt20": "lt20"}[p]
    return e


_NWL_CACHE = {}
NWL_KEYS = ("cur", "term", "age", "prem", "payout", "type", "paydur", "ratio", "year?")


def check_nwl(pid, row):
    snap = row["snapshot"]
    stem = re.sub(r"_(\d{14}|\d{4}-\d{2}-\d{2})\.html$", "", snap.rsplit("/", 1)[-1])
    hub_title, base_pages = NWL_FORMS[pid]
    is_base = stem != "gu_int_rate"
    notes = []
    if (row["metric"] == "base_rate") != is_base:
        return False, "metric %s but snapshot page %s" % (row["metric"], stem), notes
    if snap not in _NWL_CACHE:
        _NWL_CACHE[snap] = nwl_sections(snap)
    sections = _NWL_CACHE[snap]
    if is_base:
        if stem not in base_pages:
            return False, "base page %s is not a page of this form" % stem, notes
        title = next((t for t, _ in sections if t.startswith("基準金利")), "")
        bracket = re.sub(r"^基準金利\[|\]$", "", title)
        if bracket != base_pages[stem]:
            return False, "base page title %r, expected %r" % (bracket, base_pages[stem]), notes
        if pid == "nwl-form-designated-currency-annuity":
            want = "gu_int_rate10_base" if row["plan"].endswith("_pre2410") else "gu_int_rate14_base"
            if stem != want:
                return False, "plan %s on page %s" % (row["plan"], stem), notes
        events = [ev for _, evs in sections for ev in evs]
        page_cur = page_currency(title)
    else:
        found = [evs for t, evs in sections if t == hub_title]
        if len(found) != 1:
            return False, "hub section %r found %d times" % (hub_title, len(found)), notes
        events, page_cur = found[0], page_currency(hub_title)
    points, eff = nwl_points(events, page_cur)
    if eff != row["effective_date"]:
        return False, "適用期間 starts %s, row says %s" % (eff, row["effective_date"]), notes
    exp = nwl_expected(pid, row)
    cands = []
    for p in points:
        a = dict(p["attrs"])
        inferred = False
        if a.get("cur") is None and pid == "nwl-form-rate-linked-whole-life" and exp["cur"] == "JPY":
            a["cur"], inferred = "JPY", True
        if all(a.get(k) == exp.get(k) for k in NWL_KEYS):
            cands.append((p, inferred))
    if len(cands) != 1:
        return False, "%d table cells match key %s" % (len(cands), exp), notes
    p, inferred = cands[0]
    if inferred:
        notes.append("currency not printed; JPY inferred")
    if p["clash"]:
        return False, "conflicting labels %s" % p["clash"], notes
    return compare(row, p["value"]) + (notes,)


def compare(row, value):
    if value == "-":
        ok = row["status"] == "not_offered"
        return ok, "" if ok else "page shows '-', row is %s %s" % (row["status"], row["value_pct"])
    if value is None:
        ok = row["status"] == "not_offered" and row["value_pct"] == ""
        return ok, "" if ok else "page shows blank, row is %s %s" % (row["status"], row["value_pct"])
    if row["status"] != "published":
        return False, "page shows %s, row is %s" % (value, row["status"])
    ok = Decimal(row["value_pct"]) == value
    return ok, "" if ok else "page shows %s, row has %s" % (value, row["value_pct"])


# ---------------------------------------------------------------- MET (MetLife), JSON answers of the rate API

def met_key(pid, row):
    """Field of the API answer that holds this row's value (read from the field names)."""
    plan, cur, term, age = row["plan"], row["currency"], row["term_years"], row["age_band"]
    if pid == "met-sunny-garden-prime":
        if plan == "teiki_noreset":
            return {"USD": "spwl26c", "JPY": "spwl26c_y"}[cur]
        course = {"teiki": "b", "zoukai": "a"}[plan.split("_")[0]]
        infix = {"USD": "", "JPY": "y_", "AUD": "g_"}[cur]
        return "spwl26%s_%s%s%s" % (course, infix, term, "h" if plan.endswith("_boost") else "")
    if plan == "usd_no_declaration":
        return "spwl25nd_" + age.split("-")[0]
    if plan == "usd_declaration":
        return "spwl16yu"
    if plan == "usd_declaration_to_2025_09":
        return "baseInterestRate"
    if plan == "jpy_type1":
        return {"30": "baseInterestRate", "15": "baseInterestRate1"}[term]
    if plan == "jpy_type2":
        return {"30": "baseInterestRate1", "15": "baseInterestRate2"}[term]
    raise KeyError(plan)


def check_met(pid, row):
    data = json.loads(read_text(row["snapshot"]))
    key = met_key(pid, row)
    if "_main_" in row["snapshot"]:
        block = data.get("lfCreditRate15Days") or {}
        start = "%s-%02d-%02d" % (block.get("date1Year"), int(block.get("date1Month")), int(block.get("date1Day")))
        if start != row["effective_date"]:
            return False, "main window starts %s" % start, []
        if row["plan"].startswith("jpy_type"):
            key = "spiswl24_%s_type%s" % (row["term_years"], row["plan"][-1])
        if key not in block:
            return False, "field %s not in main answer" % key, []
        raw = block[key]
    else:
        recs = [rec for rec in data.get("lfCreditRateList") or []
                if rec["date1"].split("~")[0].strip().replace("/", "-") == row["effective_date"]]
        if len(recs) != 1:
            return False, "%d records start on %s" % (len(recs), row["effective_date"]), []
        rec = recs[0]
        if key not in rec:
            return False, "field %s missing in record" % key, []
        raw = rec[key]
        if row["plan"] == "usd_declaration_to_2025_09" and rec.get("gurantee") != row["term_years"]:
            return False, "record guarantee %s, row term %s" % (rec.get("gurantee"), row["term_years"]), []
    value = None if raw in ("", None) else pct(raw)
    return compare(row, value) + ([],)


# ---------------------------------------------------------------- MNL (Manulife), condensed text snapshots

MNL_TERMS = {"nashi_kihon": ["2", "3", "5", "7", "10"], "nashi_uketori": ["2", "5", "10"]}


def mnl_points(path):
    lines = read_text(path).splitlines()
    points = {}
    if lines[0].startswith("#format mnl-condensed-v1-past"):
        periods, agemap, ari50, mode = [], {}, None, None
        for ln in lines:
            if ln.startswith("#ages"):
                body = ln[len("#ages"):]
                m = re.search(r"\(ari_\*:\s*([^)]+)\)", body)
                ari50 = m.group(1).strip() if m else None
                body = re.sub(r"\([^)]*\)", "", body)
                for k, lab in re.findall(r"(\d+)=([^,]+)", body):
                    agemap[k] = lab.strip()
            elif ln.startswith("#periods"):
                mode = "p"
            elif ln.startswith("#tables"):
                mode = "t"
            elif ln.startswith("#"):
                mode = None
            elif mode == "p" and ln.strip():
                periods.append(first_date(ln))
            elif mode == "t" and ln.startswith("P "):
                parts = ln.split()
                m = re.fullmatch(r"(ARI|NASHI)_(KIHON|UKETORI)(?:_(JPY|USD|AUD))?_(\d+)", parts[1])
                plan = (m.group(1) + "_" + m.group(2)).lower()
                lab = ari50 if plan.startswith("ari") and m.group(4) == "50" else agemap[m.group(4)]
                age = age_label(lab)
                if plan.startswith("ari"):
                    vals = parts[2:]
                    assert len(vals) == len(periods), parts[1]
                    for per, triple in zip(periods, vals):
                        for cur, v in zip(("JPY", "USD", "AUD"), triple.split("/")):
                            points[(plan, cur, "", age, per)] = pct(v)
                else:
                    n = int(parts[2].split("=")[1])
                    vals = parts[3:]
                    assert len(vals) == len(periods), parts[1]
                    for per, v in zip(periods, vals):
                        for i, t in enumerate(MNL_TERMS[plan]):
                            points[(plan, m.group(3), t, age, per)] = pct(v) if i < n else "-"
        assert len(periods) == 12
    else:
        per = next(first_date(ln) for ln in lines if ln.startswith("#period"))
        for ln in lines:
            if not ln.startswith("R "):
                continue
            parts = ln.split()
            plan, cur, lab, vals = parts[1], parts[2], parts[3], parts[4:]
            age = age_label(lab)
            if plan.startswith("ari"):
                assert len(vals) == 3, ln
                for c, v in zip(("JPY", "USD", "AUD"), vals):
                    points[(plan, c, "", age, per)] = pct(v)
            else:
                assert len(vals) == len(MNL_TERMS[plan]), ln
                for t, v in zip(MNL_TERMS[plan], vals):
                    points[(plan, cur, t, age, per)] = pct(v)
    return points


_MNL = {}


def check_mnl(pid, row):
    snap = row["snapshot"]
    if snap not in _MNL:
        _MNL[snap] = mnl_points(snap)
    key = (row["plan"], row["currency"], row["term_years"], row["age_band"], row["effective_date"])
    if key not in _MNL[snap]:
        return False, "key %s not in snapshot" % (key,), []
    return compare(row, _MNL[snap][key]) + ([],)


# ---------------------------------------------------------------- NLI (Nippon Life), div layout read as a token stream

BLOCK = {"div", "p", "li", "tr", "td", "th", "dt", "dd", "br", "h1", "h2", "h3", "h4", "h5", "span", "section",
         "table", "ul", "dl"}


def block_tokens(root):
    out = []

    def walk(n):
        for ch in n.children:
            if isinstance(ch, str):
                out.append(ch)
            else:
                if ch.tag in BLOCK:
                    out.append("\n")
                walk(ch)
                if ch.tag in BLOCK:
                    out.append("\n")

    walk(root)
    return [norm(t) for t in "".join(out).split("\n") if norm(t)]


def nli_points(path):
    toks = block_tokens(dom(path))
    points = {}
    starts = [k for k, t in enumerate(toks) if re.fullmatch(r"<.+タイプ>", t)]
    for idx, i in enumerate(starts):
        label = toks[i]
        kind = {"<ふやすタイプ>": "fuyasu", "<うけとるタイプ>": "uketoru"}[label]
        seg = toks[i:starts[idx + 1] if idx + 1 < len(starts) else len(toks)]
        eff = first_date(next(t for t in seg if "をご契約日とする" in t))
        heads = [nospace(t) for t in seg[:12]]
        kn = next(k for k, t in enumerate(heads) if "実質的な" in t)
        if kind == "fuyasu":
            assert heads.index("積立利率") < kn
        else:
            assert heads.index("積立利率") < heads.index("定期支払率") < kn
        cur = age = None
        k = 0
        while k < len(seg):
            t = seg[k]
            if t in CUR_JA:
                cur = CUR_JA[t]
            elif age_label(t):
                age = age_label(t)
            elif kind == "fuyasu" and t in ("付加する", "付加しない"):
                plan = "fuyasu_rider" if t == "付加する" else "fuyasu_no_rider"
                points[("crediting_rate", plan, cur, age, eff)] = pct(seg[k + 1])
                points[("net_yield", plan, cur, age, eff)] = pct(seg[k + 2])
                k += 2
            elif kind == "uketoru" and t in ("指定通貨で10年一定", "円で10年一定"):
                plan = "uketoru_fx_const" if t.startswith("指定通貨") else "uketoru_yen_const"
                points[("crediting_rate", plan, cur, age, eff)] = pct(seg[k + 1])
                points[("payment_rate", plan, cur, age, eff)] = pct(seg[k + 2])
                points[("net_yield", plan, cur, age, eff)] = pct(seg[k + 3])
                k += 3
            k += 1
    return points, any("積立利率は10年間保証" in nospace(t) for t in toks)


_NLI = {}


def check_nli(pid, row):
    snap = row["snapshot"]
    if snap not in _NLI:
        _NLI[snap] = nli_points(snap)
    points, ten = _NLI[snap]
    if row["term_years"] != "10" or not ten:
        return False, "term %s; page states 10-year guarantee: %s" % (row["term_years"], ten), []
    key = (row["metric"], row["plan"], row["currency"], row["age_band"], row["effective_date"])
    if key not in points:
        return False, "key %s not on page" % (key,), []
    return compare(row, points[key]) + ([],)


# ---------------------------------------------------------------- ORX (ORIX Life)

def check_orx(pid, row):
    root = dom(row["snapshot"])
    alltext = norm(root.text())
    m = re.search(r"積立利率\s*(\d{4}年\d{1,2}月\d{1,2}日)\s*~[^が]{0,30}が契約日となる契約", alltext)
    if not m:
        return False, "contract-date window not found", []
    eff = first_date(m.group(1))
    if eff != row["effective_date"]:
        return False, "window starts %s" % eff, []
    hits = []
    for t in root.find_all("table"):
        g = table_grid(t)
        if not g:
            continue
        heads = [g[(0, c)]["text"] for c in range(max(c for _, c in g) + 1) if (0, c) in g]
        if "指定通貨" not in heads or "積立利率" not in heads:
            continue
        ci, ct, cv = heads.index("指定通貨"), heads.index("積立利率保証期間"), heads.index("積立利率")
        for r in range(1, max(r for r, _ in g) + 1):
            cur = CUR_JA.get(g[(r, ci)]["text"])
            term = g[(r, ct)]["text"].rstrip("年")
            if cur == row["currency"] and term == row["term_years"]:
                hits.append(pct(g[(r, cv)]["text"]))
    if len(hits) != 1:
        return False, "%d table rows match" % len(hits), []
    return compare(row, hits[0]) + ([],)


# ---------------------------------------------------------------- PGF (Prudential Gibraltar Financial)

def check_pgf(pid, row):
    root = dom(row["snapshot"])
    if pid == "pgf-sodateru-kajitsu":
        g = table_grid(root.find_all("table")[0])
        heads = [g[(0, c)]["text"] for c in range(5)]
        col = heads.index({"cap_rate": "上限率", "reference_yield": "参照運用利回り"}[row["metric"]])
        hits = [g[(r, col)]["text"] for r in range(1, max(r for r, _ in g) + 1)
                if first_date(g[(r, 0)]["text"]) == row["effective_date"]]
        if len(hits) != 1:
            return False, "%d rows for %s" % (len(hits), row["effective_date"]), []
        return compare(row, pct(hits[0])) + ([],)
    span_id = {"base": "disp_rate2", "withdrawal": "disp_rate4"}[row["plan"]]
    span = next((n for n in root.iter() if n.attrs.get("id") == span_id), None)
    if span is None:
        return False, "%s not in snapshot" % span_id, []
    g = table_grid(span.find_all("table")[0])
    want = {"crediting_rate": "積立利率", "net_yield": "実質的な利回り"}[row["metric"]]
    hits = []
    for cell, row_labels, col_labels in value_cells(g):
        date = first_date(" ".join(t for t, _ in row_labels))
        ages = [age_label(re.sub(r"^契約年齢:", "", nospace(t))) for t in col_labels if "契約年齢" in t]
        terms = [re.search(r"(\d+)年", t).group(1) for t in col_labels if "適用期間" in t]
        metric = [nospace(t) for t in col_labels if nospace(t) in ("積立利率", "実質的な利回り")]
        if date == row["effective_date"] and ages == [row["age_band"]] and terms == [row["term_years"]] \
                and metric == [want]:
            hits.append(pct(cell["text"]))
    if len(hits) != 1:
        return False, "%d cells match" % len(hits), []
    return compare(row, hits[0]) + ([],)


PARSERS = {"nwl": check_nwl, "met": check_met, "mnl": check_mnl, "nli": check_nli, "orx": check_orx, "pgf": check_pgf}


# ---------------------------------------------------------------- sampling and outliers

def load_rows(pid):
    rows = []
    for path in sorted(glob.glob(str(RATES / (pid + "__*.csv")))):
        with open(path, newline="", encoding="utf-8") as fh:
            for i, r in enumerate(csv.DictReader(fh), start=2):
                r["_where"] = "%s:%d" % (Path(path).name, i)
                rows.append(r)
    return rows


def series_key(r):
    return (r["metric"], r["plan"], r["currency"], r["term_years"], r["age_band"], r["sex"])


def outliers(rows):
    out, groups = {}, {}
    for r in rows:
        if r["status"] == "published":
            groups.setdefault(series_key(r), []).append(r)
    for rs in groups.values():
        rs.sort(key=lambda r: r["effective_date"])
        vals = [float(r["value_pct"]) for r in rs]
        for r, v in zip(rs, vals):
            if not 0 <= v <= 15:
                out.setdefault(r["_where"], []).append("outside 0-15%")
        if len(vals) >= 5:
            mu, sd = statistics.mean(vals), statistics.pstdev(vals)
            for r, v in zip(rs, vals):
                if sd > 0 and abs(v - mu) > 3 * sd:
                    out.setdefault(r["_where"], []).append("%.2f sd from series mean %.2f" % ((v - mu) / sd, mu))
        for a, b, va, vb in zip(rs, rs[1:], vals, vals[1:]):
            if abs(vb - va) > 1.0 + 1e-9:
                why = "jump %+.2fpp %s -> %s" % (vb - va, a["effective_date"], b["effective_date"])
                out.setdefault(a["_where"], []).append(why)
                out.setdefault(b["_where"], []).append(why)
    return out


def sample(pid, rows):
    rng = random.Random("recheck-sample:" + pid)
    chosen, groups, sub = set(), {}, {}
    for r in rows:
        groups.setdefault((r["metric"], r["plan"]), []).append(r["_where"])
        sub.setdefault((r["metric"], r["plan"], r["currency"]), []).append(r["_where"])
    for k in sorted(sub):
        chosen.add(rng.choice(sorted(sub[k])))
    for k in sorted(groups):
        members = sorted(groups[k])
        need = math.ceil(0.10 * len(members))
        rest = [w for w in members if w not in chosen]
        rng.shuffle(rest)
        chosen.update(rest[:max(0, need - (len(members) - len(rest)))])
    target = min(len(rows), max(20, math.ceil(0.10 * len(rows))))
    rest = sorted(r["_where"] for r in rows if r["_where"] not in chosen)
    rng.shuffle(rest)
    chosen.update(rest[:max(0, target - len(chosen))])
    return chosen


def run(pid, all_rows=False):
    rows = load_rows(pid)
    if not rows:
        raise SystemExit("no rate CSVs for " + pid)
    check = PARSERS[pid.split("-", 1)[0]]
    outl = outliers(rows)
    sampled = {r["_where"] for r in rows} if all_rows else sample(pid, rows)
    picked = sampled | set(outl)
    results = []
    for r in rows:
        if r["_where"] not in picked:
            continue
        try:
            ok, why, notes = check(pid, r)
        except Exception as err:  # a parsing failure is a failed check, reported with the row
            ok, why, notes = False, "checker error: %r" % err, []
        results.append({"row": r["_where"], "key": [r[k] for k in ("metric", "plan", "currency", "term_years",
                                                                     "age_band", "effective_date")],
                        "value": r["value_pct"], "status": r["status"], "snapshot": r["snapshot"],
                        "retrieved_date": r["retrieved_date"], "ok": ok, "why": why, "notes": notes,
                        "sampled": r["_where"] in sampled, "outlier": outl.get(r["_where"], [])})
    return {"product": pid, "rows": len(rows), "published": sum(r["status"] == "published" for r in rows),
            "sampled": len(sampled), "checked": len(results), "matched": sum(x["ok"] for x in results),
            "outliers": {w: outl[w] for w in sorted(outl)},
            "max_retrieved": max(r["retrieved_date"] for r in rows), "results": results}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("products", nargs="*")
    ap.add_argument("--all-rows", action="store_true", help="check every row instead of a sample")
    ap.add_argument("--json", help="write the full result to this file")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every checked row")
    args = ap.parse_args()
    out, bad = [], 0
    for pid in args.products or DEFAULT_PRODUCTS:
        res = run(pid, args.all_rows)
        out.append(res)
        print("%-52s rows %5d  sampled %4d  checked %4d  matched %4d  outlier rows %3d" % (
            pid, res["rows"], res["sampled"], res["checked"], res["matched"], len(res["outliers"])))
        for x in res["results"]:
            if not x["ok"] or args.verbose:
                print("    %s %s %s %s value=%s %s -> %s %s %s" % (
                    "OK " if x["ok"] else "BAD", x["row"], x["key"], x["status"], x["value"],
                    x["snapshot"].rsplit("/", 1)[-1], x["why"], x["notes"] or "", x["outlier"] or ""))
            bad += not x["ok"]
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
