"""Collector for Nippon Wealth Life (ニッセイ・ウェルス生命) 悠々時間アドバンス2.

ACCESS: nw-life.co.jp only answers from some networks (it returns HTTP 403
elsewhere); from a connection in Japan, a Japan VPN included, it answers
normally. --live reads the live site and stops with a clear message when it is
not reachable. The Wayback Machine (CDX index and captures) and copies saved
by hand (--file, --live-dir) remain for history and for other networks.

What it does
  --live      saves today's live rate, base-rate and product pages and the two
              current brochures (scripts/snapshot_jp_rates.py: browser
              User-Agent, 2.5 s between requests, stops on a 403 or block page)
              and ingests them as --live-dir does, dated in Japan time
  --wayback   lists the Wayback captures of the shared 積立利率 page and of
              the 基準金利 pages, saves every distinct capture under
              sources/nwl/wayback/, parses the sections that belong to the
              four policy forms of 悠々時間アドバンス2 and merges the points
              into data/rates/<product_id>__<metric>__<plan>.csv
  --docs      downloads the latest Wayback capture of every document listed
              in the product JSON into sources/nwl/docs/<product_id>/ and
              writes sources/nwl/docs/manifest.csv
  --live-dir D --retrieved YYYY-MM-DD
              adds a folder of hand-saved live copies (rate/base pages parsed with
              via_wayback=false, product pages and PDFs filed under sources/nwl/)
  --file F --retrieved YYYY-MM-DD
              parses a locally saved copy of the live 積立利率 page (or of a
              Wayback capture file, for testing) and merges it with
              via_wayback=false and source_url = the live URL. Use
              --kind base:<page> for a 基準金利 page, e.g.
              --kind base:gu_int_rate12_base.

Each capture of the 積立利率 page shows only the then-current half-month
period (rates are set on the 1st and 16th), so the history is only as dense
as the captures. Gaps are never interpolated. effective_date is the first
day of the 適用期間.

Encoding of dimensions that do not fit the CSV columns (see the product JSON
notes): the table dimension 判定年数 and the 年金受取期間 are part of the
plan key; term_years is always the 据置期間; age_band is the 被保険者年齢
band as printed (e.g. 0-69).

Usage:
    python3 scripts/nwl.py --live
    python3 scripts/nwl.py --wayback --docs
    python3 scripts/nwl.py --file saved.html --retrieved 2026-11-16
    python3 scripts/nwl.py --file capture.html --retrieved 2026-11-16 --kind base:gu_int_rate12_base

Re-running is safe: saved captures are reused, unchanged documents are not
saved twice and existing data points are never rewritten.
"""

import argparse
import csv
import gzip
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error

import common
import wayback

SRC = common.SOURCES / "nwl"
PRODUCT_ID = "nwl-yuyu-jikan-advance-2"
PRODUCT_FILE = common.DATA / "products" / (PRODUCT_ID + ".json")
CDX = "https://web.archive.org/cdx/search/cdx?url=%s&output=json&collapse=digest"
WAYBACK_RAW = "https://web.archive.org/web/%sid_/%s"
WAYBACK_PLAIN = "https://web.archive.org/web/%s/%s"
CURRENCY_BY_JA = {"円": "JPY", "米ドル": "USD", "豪ドル": "AUD"}


# ---------------------------------------------------------------- text helpers

def norm(text):
    """Full-width to ASCII (NFKC), tags removed, whitespace collapsed."""
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def strip_comments(page):
    return re.sub(r"<!--[\s\S]*?-->", "", page)


def parse_value(text):
    """'2.14%' -> ('2.14', 'published'); anything else (-, 取扱停止) -> ('', 'not_offered')."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?", norm(text))
    if m:
        return m.group(1), "published"
    return "", "not_offered"


def age_band(label):
    """'0歳～69歳' / '0歳以上69歳以下' -> '0-69'; '80歳' -> '80'; '85歳以上' -> '85+'."""
    label = norm(label)
    nums = re.findall(r"\d+", label)
    if len(nums) == 2:
        return "%s-%s" % tuple(nums)
    if len(nums) == 1:
        return nums[0] + ("+" if "以上" in label else "")
    raise ValueError("unreadable age band %r" % label)


def years_label(label):
    """判定年数 label -> plan-key fragment: '5年～6年' -> '5-6', '18年～終身' -> '18-life',
    '5年以下' -> 'le5', '11年以上' -> 'ge11', '10年' -> '10'."""
    label = norm(label)
    m = re.fullmatch(r"(\d+)年\s*[～~-]\s*(\d+)年", label)
    if m:
        return "%s-%s" % m.groups()
    m = re.fullmatch(r"(\d+)年\s*[～~-]\s*終身", label)
    if m:
        return m.group(1) + "-life"
    m = re.fullmatch(r"(\d+)年以下", label)
    if m:
        return "le" + m.group(1)
    m = re.fullmatch(r"(\d+)年以上", label)
    if m:
        return "ge" + m.group(1)
    m = re.fullmatch(r"(\d+)年", label)
    if m:
        return m.group(1)
    raise ValueError("unreadable 判定年数 %r" % label)


def period_start(text):
    """First day of '【適用期間】 2026年5月1日 ～ 5月15日'. None when absent."""
    m = re.search(r"適用期間】?\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", norm(text))
    if not m:
        return None
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


# ---------------------------------------------------------------- table helpers

def grid(table_html):
    """Expand a <table> into a list of rows of (text, is_th) with spans filled in."""
    rows = []
    pending = {}  # column -> (rows_left, text, is_th)
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", table_html):
        cells = re.findall(r"<(t[dh])([^>]*)>([\s\S]*?)</t[dh]>", tr)
        line, col, queue = [], 0, list(cells)
        while queue or col in pending:
            if col in pending:
                left, text, th = pending[col]
                line.append((text, th))
                if left > 1:
                    pending[col] = (left - 1, text, th)
                else:
                    del pending[col]
                col += 1
                continue
            tag, attrs, inner = queue.pop(0)
            colspan = re.search(r'colspan="?(\d+)', attrs)
            rowspan = re.search(r'rowspan="?(\d+)', attrs)
            text, th = norm(inner), tag == "th"
            for _ in range(int(colspan.group(1)) if colspan else 1):
                line.append((text, th))
                if rowspan and int(rowspan.group(1)) > 1:
                    pending[col] = (int(rowspan.group(1)) - 1, text, th)
                col += 1
        if line:
            rows.append(line)
    return rows


def is_age(text):
    return re.search(r"\d+歳", text) is not None and "年" not in text


def is_pct(text):
    return re.fullmatch(r"\d+(?:\.\d+)?\s*%", text) is not None


def is_dash(text):
    return text in ("-", "－", "ー", "―", "―", "—")


def age_header_index(rows):
    """Index of the row that carries the 被保険者年齢 bands (no percentages)."""
    for i, r in enumerate(rows):
        if any(is_age(c[0]) for c in r) and not any(is_pct(c[0]) for c in r):
            return i
    raise ValueError("age band header not found")


# ---------------------------------------------------------------- 積立利率 section parsers
# Each parser returns point dicts: plan, currency, term (据置期間, years or ''), age, text.

def parse_hantei_matrix(rows, plan_prefix, currency):
    """判定年数 x 被保険者年齢 matrix. Used for AⅡ型 and the 豪ドル建 確定年金."""
    header = age_header_index(rows)
    if "判定年数" not in " ".join(c[0] for r in rows[: header + 1] for c in r):
        raise ValueError("判定年数 header not found")
    ages = [c[0] for c in rows[header]]
    if not is_age(ages[0]):  # the left-most header cell is the 判定年数 label (rowspan)
        ages = ages
    out = []
    for r in rows[header + 1:]:
        label = r[0][0]
        values = [c[0] for c in r[1:]]
        age_cols = [a for a in ages if is_age(a)]
        if len(values) != len(age_cols):
            raise ValueError("row %r has %d values for %d age bands" % (label, len(values), len(age_cols)))
        for age, val in zip(age_cols, values):
            if not (is_pct(val) or is_dash(val)):
                raise ValueError("unexpected cell %r under %r" % (val, age))
            out.append({"plan": "%s_h%s" % (plan_prefix, years_label(label)), "currency": currency,
                        "term": "", "age": age_band(age), "text": val})
    return out


def parse_deferral_age_table(rows, plan, currency):
    """据置期間 | one column per age band. Used for the tontine form (one table per currency)."""
    header = age_header_index(rows)
    ages = [c[0] for c in rows[header] if is_age(c[0])]
    out = []
    for r in rows[header + 1:]:
        m = re.fullmatch(r"(\d+)年", r[0][0])
        if not m or len(r) != 1 + len(ages):
            raise ValueError("unexpected 据置期間 row %r" % [c[0] for c in r])
        for age, cell in zip(ages, r[1:]):
            out.append({"plan": plan, "currency": currency, "term": m.group(1), "age": age_band(age), "text": cell[0]})
    return out


def deferral_terms(label):
    """'0・1年' -> ['0', '1']; '3・5・10・15・20年' -> [...]; '5年' -> ['5']."""
    label = norm(label)
    if not re.fullmatch(r"\d+(?:[・,]\d+)*年", label):
        raise ValueError("unreadable 据置期間 %r" % label)
    return re.findall(r"\d+", label)


def parse_aud_life(rows, plan):
    """豪ドル建 終身: rows are 据置期間 label(s) then one value per age band."""
    header = age_header_index(rows)
    ages = [c[0] for c in rows[header] if is_age(c[0])]
    out = []
    for r in rows[header + 1:]:
        terms = deferral_terms(r[0][0])
        values = [c[0] for c in r[1:]]
        if len(values) != len(ages):
            raise ValueError("豪ドル建 life row %r does not match age bands %r" % ([c[0] for c in r], ages))
        for term in terms:
            for age, val in zip(ages, values):
                out.append({"plan": plan, "currency": "AUD", "term": term, "age": age_band(age), "text": val})
    return out


def parse_tg_table(rows, flat):
    if "年金種類:確定年金" in flat:
        return parse_hantei_matrix(rows, "aud_kakutei", "AUD")
    if "年金種類:年金総額保証付終身年金・純粋終身年金" in flat:
        return parse_aud_life(rows, "aud_shushin")
    if "年金種類:年金総額保証付後厚終身年金" in flat:
        return parse_aud_life(rows, "aud_atsu")
    raise ValueError("unrecognised 豪ドル建 table")


def parse_dd_life(rows, plan):
    """米ドル建 終身: 据置期間 (rowspan) | age band | rate."""
    out = []
    for r in rows:
        if len(r) != 3 or not (is_pct(r[2][0]) or is_dash(r[2][0])):
            continue
        m = re.fullmatch(r"(\d+)年", r[0][0])
        if not m:
            raise ValueError("unexpected 据置期間 %r" % r[0][0])
        out.append({"plan": plan, "currency": "USD", "term": m.group(1), "age": age_band(r[1][0]), "text": r[2][0]})
    if not out:
        raise ValueError("no rows in %s table" % plan)
    return out


def parse_dd_table(rows, flat):
    """米ドル建 年金額確定特約付: 確定年金 (据置 x 年金受取期間 x age) and two 終身 types."""
    if "年金受取期間" in flat:
        try:
            header = age_header_index(rows)
            ages = [c[0] for c in rows[header] if is_age(c[0])]
        except ValueError:
            # Earlier layout (seen in 2022): one 積立利率 column, no age bands, so age_band is blank.
            header = next(i for i, r in enumerate(rows) if {"積立利率", "基準金利"} & {c[0] for c in r})
            ages = [""]
        out = []
        for r in rows[header + 1:]:
            if len(r) != 2 + len(ages):
                raise ValueError("unexpected 確定年金 row %r" % [c[0] for c in r])
            m1, m2 = re.fullmatch(r"(\d+)年", r[0][0]), re.fullmatch(r"(\d+)年", r[1][0])
            if not m1 or not m2:
                raise ValueError("unexpected 据置/受取 cells %r" % [c[0] for c in r[:2]])
            for age, cell in zip(ages, r[2:]):
                out.append({"plan": "usd_kakutei_p%s" % m2.group(1), "currency": "USD", "term": m1.group(1),
                            "age": age_band(age) if age else "", "text": cell[0]})
        return out
    if "年金種類:年金総額保証付終身年金・純粋終身年金" in flat:
        return parse_dd_life(rows, "usd_shushin")
    if "年金種類:年金総額保証付後厚終身年金" in flat:
        return parse_dd_life(rows, "usd_atsu")
    raise ValueError("unrecognised 米ドル建 table")


def parse_section(kind, section_html):
    """kind in a2 | dd | tg | sh. Returns (effective_date, points)."""
    date = period_start(section_html)
    if date is None:
        raise ValueError("適用期間 not found")
    tables = re.findall(r"<table[\s\S]*?</table>", section_html)
    if not tables:
        raise ValueError("no tables")
    points = []
    for t in tables:
        rows = grid(t)
        flat = " ".join(c[0] for r in rows for c in r)
        if kind == "a2":
            if "判定年数" not in flat:
                raise ValueError("unrecognised AⅡ型 table")
            points += parse_hantei_matrix(rows, "a2_jpy", "JPY")
        elif kind == "sh":
            m = re.search(r"指定通貨:\s*(円|米ドル|豪ドル)", flat)
            if not m:
                raise ValueError("unrecognised 指定通貨 table")
            points += parse_deferral_age_table(rows, "tontine", CURRENCY_BY_JA[m.group(1)])
        elif kind == "tg":
            points += parse_tg_table(rows, flat)
        elif kind == "dd":
            points += parse_dd_table(rows, flat)
    if not points:
        raise ValueError("no points")
    return date, points


# ---------------------------------------------------------------- page layout

# Sections of the shared 積立利率 page that belong to 悠々時間アドバンス2.
SECTIONS = {
    "a2": "積立利率金利連動型年金（AⅡ型）",
    "dd": "積立利率金利連動型年金（米ドル建）年金額確定特約付",
    "tg": "積立利率金利連動型年金（豪ドル建）",
    "sh": "生存保障重視特則付指定通貨建個人年金保険",
}


def section_kind(title):
    """Match a section heading to a policy form. Titles are matched on their text, not their id."""
    t = norm(title).replace(" ", "").replace("-", "")
    for kind, name in SECTIONS.items():
        if t == norm(name).replace(" ", ""):
            return kind
    return None


def split_sections(page):
    """Return [(title, html)] for every heading on the page, in order.

    A heading is a div.tpl-title--l (or --m) carrying a label; a section runs to the next heading.
    """
    page = strip_comments(page)
    heads = [
        (m.start(), m.end(), norm(m.group(1)))
        for m in re.finditer(r'<div class="tpl-title[^"]*"[^>]*>\s*<(?:p|h\d) class="label">([\s\S]*?)</(?:p|h\d)>', page)
    ]
    out = []
    for i, (start, end, title) in enumerate(heads):
        stop = heads[i + 1][0] if i + 1 < len(heads) else len(page)
        out.append((title, page[end:stop]))
    return out


def parse_rate_page(page):
    """Parse the shared 積立利率 page. Returns (points, errors).

    points: list of dicts with effective_date added. errors: list of (kind, message) for
    sections that exist but could not be parsed reliably. A form that is absent from
    the capture produces neither points nor an error.
    """
    points, errors, seen = [], [], set()
    for title, body in split_sections(page):
        kind = section_kind(title)
        if kind is None or kind in seen:
            continue
        seen.add(kind)
        try:
            date, pts = parse_section(kind, body)
        except (ValueError, IndexError) as err:
            errors.append((kind, str(err)))
            continue
        for p in pts:
            p["effective_date"] = date
            p["metric"] = "crediting_rate"
        points += pts
    return points, errors


def parse_base_page(page, spec):
    """Parse a 基準金利 page. It carries one policy form (spec['form']) in the same table layout
    as the 積立利率 page. spec['plan_suffix'] separates contract-date cohorts that have their own page."""
    page = strip_comments(page)
    start = page.find("【適用期間】")
    if start < 0:
        return [], []
    try:
        date, pts = parse_section(spec["form"], page[start:])
    except (ValueError, IndexError) as err:
        return [], [(spec["form"], str(err))]
    for p in pts:
        p["effective_date"] = date
        p["metric"] = "base_rate"
        p["plan"] += spec.get("plan_suffix", "")
    return pts, []


# ---------------------------------------------------------------- rows and merging

def point_rows(points, source_url, retrieved, snapshot, wayback):
    """Group parsed points into CSV rows keyed by csv filename."""
    grouped = {}
    for p in points:
        value, status = parse_value(p["text"])
        name = "%s__%s__%s.csv" % (PRODUCT_ID, p["metric"], p["plan"])
        grouped.setdefault(name, []).append(
            {
                "product_id": PRODUCT_ID,
                "metric": p["metric"],
                "plan": p["plan"],
                "currency": p["currency"],
                "term_years": p["term"],
                "age_band": p["age"],
                "sex": "",
                "effective_date": p["effective_date"],
                "value_pct": value,
                "status": status,
                "source_url": source_url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "true" if wayback else "false",
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
    print("  %-34s %4d points read, %4d new" % (label, sum(len(r) for r in grouped.values()), added_total))
    return conflicts


# ---------------------------------------------------------------- Wayback access

def fetch_wayback(url, tries=5):
    """GET from the Wayback Machine, waiting politely when it answers 429.

    Returns the body (gunzipped when needed), or None when the archive keeps refusing.
    Raises HTTPError for other 4xx so the caller can fall back to another URL form.
    """
    for attempt in range(tries):
        try:
            body = common.fetch(url, pause=3.0, timeout=120)
            if body[:2] == b"\x1f\x8b":  # raw captures are sometimes served gzipped
                body = gzip.decompress(body)
            if len(body) < 4000 and any(m in body for m in wayback.BLOCK_MARKERS):
                # The archive's own throttling page, not the capture: never save it.
                time.sleep(60 * (attempt + 1))
                continue
            return body
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            time.sleep(60 * (attempt + 1))
    return None


def wayback_captures(url):
    """All distinct-content captures of a URL: [(timestamp, original_url, status)]."""
    raw = common.fetch(CDX % url.replace("https://", ""), pause=3.0)
    rows = json.loads(raw) if raw.strip() else []
    return [(r[1], r[2], r[4]) for r in rows[1:]]


CAPTURE_LOG = SRC / "wayback" / "captures.csv"
CAPTURE_FIELDS = ["page", "timestamp", "url", "form", "bytes", "sha256"]


def load_capture_log():
    if not CAPTURE_LOG.exists():
        return {}
    with CAPTURE_LOG.open(newline="", encoding="utf-8") as fh:
        return {(r["page"], r["timestamp"]): r for r in csv.DictReader(fh)}


def save_capture_log(log):
    CAPTURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CAPTURE_LOG.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAPTURE_FIELDS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(log):
            writer.writerow(log[key])


def get_capture(page_name, ts, original, log):
    """Return (path, wayback_url) of a saved capture, downloading it when needed. None on refusal.

    The raw (id_) form is tried first. If the archive refuses it, the normal capture URL is
    used and the file includes Wayback's rewriting (recorded as form=rewritten in captures.csv).
    """
    path = SRC / "wayback" / ("%s_%s.html" % (page_name, ts))
    entry = log.get((page_name, ts))
    if path.exists() and entry:
        return path, entry["url"]
    body, used, form = None, None, None
    for form_name, template in (("raw", WAYBACK_RAW), ("rewritten", WAYBACK_PLAIN)):
        url = template % (ts, original)
        try:
            body = fetch_wayback(url)
        except urllib.error.HTTPError as err:
            print("  wayback %s %s: %s form refused (HTTP %d)" % (page_name, ts, form_name, err.code))
            continue
        if body is None:
            return None
        used, form = url, form_name
        break
    if body is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    log[(page_name, ts)] = {
        "page": page_name, "timestamp": ts, "url": used, "form": form,
        "bytes": str(len(body)), "sha256": common.sha256(body),
    }
    save_capture_log(log)
    return path, used


def ts_date(ts):
    return "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8])


def collect_wayback(product):
    log = load_capture_log()
    conflicts = 0
    for page in product["pages"]:
        kind = page.get("kind")
        if kind not in ("rate", "base", "page"):
            continue
        print("%s  %s" % (page["name"], page["url"]))
        if kind == "page":  # saved for reference only (latest capture), nothing to parse
            cap = latest_capture(page["url"])
            if cap and get_capture(page["name"], cap[0], cap[1], log):
                print("  wayback %s saved" % cap[0])
            continue
        for ts, original, status in wayback_captures(page["url"]):
            if status != "200":
                print("  wayback %s SKIPPED (archived HTTP status %s, no content)" % (ts, status))
                continue
            got = get_capture(page["name"], ts, original, log)
            if got is None:
                print("  wayback %s SKIPPED (Wayback Machine refused: rate limit)" % ts)
                continue
            path, wb_url = got
            conflicts += ingest(page, common.decode(path.read_bytes()), wb_url, ts_date(ts), path, True, "wayback " + ts)
    return conflicts


def ingest(page, text, source_url, retrieved, path, wayback, label):
    """Parse one saved page and merge it. Unparseable pages and sections are reported, not guessed."""
    if page["kind"] == "rate":
        points, errors = parse_rate_page(text)
    else:
        points, errors = parse_base_page(text, page)
    for kind, msg in errors:
        print("  %s SKIPPED section %s (cannot parse reliably): %s" % (label, kind, msg))
    if not points:
        if not errors:
            print("  %s SKIPPED (no data on this capture: %s)" % (label, why_empty(text)))
        return 0
    return merge(point_rows(points, source_url, retrieved, path, wayback), label)


def why_empty(text):
    if "_Incapsula_Resource" in text:
        return "bot-block stub"
    if "403" in text[:2000] and "Forbidden" in text[:2000]:
        return "HTTP 403 page"
    return "no known section found"


# ---------------------------------------------------------------- documents

DOC_FIELDS = ["product_id", "name", "title_ja", "url", "file", "bytes", "sha256", "retrieved_date",
              "via_wayback", "wayback_url", "capture_timestamp", "status"]


def latest_capture(url):
    """Latest 200 capture of a URL: (timestamp, original) or None."""
    caps = [(ts, orig) for ts, orig, status in wayback_captures(url) if status == "200"]
    return max(caps) if caps else None


def pdf_status(body, wayback=True):
    """A PDF that does not end in %%EOF was cut off (the Wayback Machine stored some captures truncated at 1 MiB)."""
    if b"%%EOF" not in body[-2048:]:
        return "INCOMPLETE_truncated_capture_do_not_rely_on"
    return "wayback_copy_may_be_out_of_date" if wayback else "current_live_copy"


def download_documents(product, manifest):
    folder = SRC / "docs" / product["id"]
    for doc in product.get("documents", []):
        cap = latest_capture(doc["url"])
        if cap is None:
            print("  doc   %s: no Wayback capture, SKIPPED" % doc["name"])
            continue
        ts, original = cap
        have = [m for m in manifest.values() if m["product_id"] == product["id"] and m["name"] == doc["name"]
                and m["capture_timestamp"] == ts and (common.ROOT / m["file"]).exists()]
        if have:
            m = have[0]
            m["status"] = pdf_status((common.ROOT / m["file"]).read_bytes())
            print("  doc   %s already on file (captured %s) [%s]" % (m["file"], ts, m["status"]))
            continue
        body, wb_url = None, None
        for template in (WAYBACK_RAW, WAYBACK_PLAIN):
            wb_url = template % (ts, original)
            try:
                body = fetch_wayback(wb_url)
            except urllib.error.HTTPError:
                body = None
                continue
            break
        if body is None:
            print("  doc   %s %s: SKIPPED (Wayback Machine refused)" % (doc["name"], ts))
            continue
        if not body.startswith(b"%PDF"):
            print("  doc   %s %s: SKIPPED (not a PDF)" % (doc["name"], ts))
            continue
        path = common.save_snapshot(folder, doc["name"], "pdf", body, date=ts_date(ts))
        manifest[(product["id"], doc["name"], common.rel(path))] = {
            "product_id": product["id"],
            "name": doc["name"],
            "title_ja": doc["title_ja"],
            "url": original,
            "file": common.rel(path),
            "bytes": str(len(body)),
            "sha256": common.sha256(body),
            "retrieved_date": common.today(),
            "via_wayback": "true",
            "wayback_url": wb_url,
            "capture_timestamp": ts,
            "status": pdf_status(body),
        }
        print("  doc   %s (%.1f MB, captured %s) [%s]" % (common.rel(path), len(body) / 1e6, ts, pdf_status(body)))


def load_manifest(path):
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {(r["product_id"], r["name"], r["file"]): r for r in csv.DictReader(fh)}


def write_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DOC_FIELDS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(manifest):
            writer.writerow({f: manifest[key].get(f, "") for f in DOC_FIELDS})


# ---------------------------------------------------------------- policy-form products (nwl-form-*)
#
# The 積立利率/予定利率 hub is organised by POLICY FORM, not by marketed name. Besides the four forms
# of 悠々時間アドバンス2 (above) it publishes the forms below. Each becomes one product record
# keyed by the policy form (id nwl-form-<slug>). Marketed names (賢者の年金, みらいの果実, 年金新時代,
# 定期受取型終身, 生存給付金付終身 ...) are NOT yet mapped to forms.
#
# Not collected on purpose: 予定利率金利連動型外貨建個人年金保険 (hub section el_gaika, 予定利率 table, base page
# el_int_rate_base3) is a 平準払 (level premium) product (しおり index lists it under <平準払個人年金保険>),
# and the 生存保障重視特則付指定通貨建個人年金保険 (section sh) is already tracked as the 'tontine' plan
# of nwl-yuyu-jikan-advance-2.

FORMS = {
    "sk": {
        "slug": "designated-currency-annuity", "product_type": "fixed_annuity", "shiori": "g41",
        "name_ja": "指定通貨建個人年金保険", "name_en": "Designated-currency individual annuity",
        "titles": ["指定通貨建個人年金保険"], "category": "fixed_annuity", "subtype": None,
        "default_plan": "prem_all", "guarantee_terms_years": None,
        "deferral_note": "据置期間 5 and 10 years; 積立利率 is fixed for the contract (rate lock at contract date).",
    },
    "fla": {
        "slug": "index-linked-annuity", "product_type": "indexed_annuity", "shiori": "g30",
        "name_ja": "指数連動型年金特約付指定通貨建個人年金保険", "name_en": "Designated-currency annuity with index-linked rider",
        "titles": ["指数連動型年金特約付指定通貨建個人年金保険"], "category": "fixed_indexed_annuity",
        "subtype": None, "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "据置期間 5 and 10 years. Only 積立利率 and 基準金利 are collected; the 連動率 and step-rate tables (hub sub-pages fla/linkratio_table, fla/steprate_table) are not.",
    },
    "fla2": {
        "slug": "index-linked-annuity-2", "product_type": "indexed_annuity", "shiori": "g39",
        "name_ja": "指定通貨建積立利率金利連動型年金 指数連動型年金特約Ⅱ付",
        "name_en": "Designated-currency rate-linked annuity with index-linked rider II",
        "titles": ["指定通貨建積立利率金利連動型年金指数連動型年金特約II付"], "category": "fixed_indexed_annuity",
        "subtype": None, "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "据置期間 5 and 10 years x 年金受取期間 10 and 20 years (plan p<N>). Only 積立利率 and 基準金利 are collected; the 連動率 and step-rate tables (fla2/linkratio_table, fla2/steprate_table) are not.",
    },
    "sts": {
        "slug": "designated-currency-whole-life", "product_type": "whole_life_rate_locked", "shiori": "g43",
        "name_ja": "指定通貨建終身保険", "name_en": "Designated-currency whole life (underwritten course)",
        "titles": ["指定通貨建終身保険"], "category": "whole_life", "subtype": None,
        "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "積立利率 is constant over the whole insurance period (hub text: 保険期間を通じて一定). 告知コース per the しおり index.",
    },
    "stts": {
        "slug": "designated-currency-special-whole-life", "product_type": "whole_life_rate_locked", "shiori": "g44",
        "name_ja": "指定通貨建特別終身保険", "name_en": "Designated-currency special whole life (no-underwriting course)",
        "titles": ["指定通貨建特別終身保険"], "category": "whole_life", "subtype": None,
        "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "無告知コース per the しおり index. 積立利率 is constant over the whole insurance period.",
    },
    "stts25": {
        "slug": "designated-currency-special-whole-life-25", "product_type": "whole_life_rate_locked", "shiori": "g45",
        "name_ja": "指定通貨建特別終身保険（25）", "name_en": "Designated-currency special whole life (25)",
        "titles": ["指定通貨建特別終身保険(25)"], "category": "whole_life", "subtype": None,
        "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "無告知コース per the しおり index. 契約時被保険者年齢 bands 40-49 to 81-95 (JPY 91-95 not offered).",
    },
    "sttsr": {
        "slug": "special-whole-life-fixed-accumulation", "product_type": "whole_life_unclassified", "shiori": "g46",
        "name_ja": "積立金区分型終身保険特約（確定積増型）付指定通貨建特別終身保険",
        "name_en": "Designated-currency special whole life with fixed-accumulation reserve-segment rider",
        "titles": ["積立金区分型終身保険特約(確定積増型)付指定通貨建特別終身保険"], "category": "whole_life",
        "subtype": None, "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "Appears on the hub from 2025-11 (first Wayback capture with the section: 2025-11-25).",
    },
    "fiwl": {
        "slug": "index-linked-whole-life", "product_type": "whole_life_unclassified", "shiori": "g42",
        "name_ja": "積立金区分型終身保険特約付指定通貨建特別終身保険",
        "name_en": "Designated-currency special whole life with reserve-segment rider (index-linked)",
        "titles": ["積立金区分型終身保険特約付指定通貨建特別終身保険"], "category": "whole_life",
        "subtype": "index_linked_whole_life", "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "Hub brands this 指数連動型定額終身保険（指定通貨建）. The rate table is by 特約の型 (指数連動型 / 指数連動・確定積増型); plans index and index_fixed. Index tables (fiwl/fiwl_index_table etc.) are not collected.",
    },
    "tsk": {
        "slug": "survival-benefit-whole-life", "product_type": "whole_life_unclassified", "shiori": "g27",
        "name_ja": "積立利率金利連動型生存給付金付終身保険（指定通貨建）",
        "name_en": "Rate-linked whole life with survival benefit (designated currency)",
        "titles": ["積立利率金利連動型生存給付金付終身保険(指定通貨建)"], "category": "whole_life",
        "subtype": "living_benefit_whole_life", "default_plan": "all", "guarantee_terms_years": None,
        "deferral_note": "Plans combine the two published cases (基本終身保険金額が生存給付金額の2倍以上 = r2ge / 2倍未満 = r2lt) with 生存給付金支払期間 (paylt20 = 20年未満, payge20 = 20年以上); age_band is 契約時の被保険者年齢.",
    },
    "old": {
        "slug": "rate-linked-whole-life", "product_type": "whole_life_rate_locked", "shiori": "g14",
        "name_ja": "積立利率金利連動型終身保険", "name_en": "Rate-linked whole life (discontinued)",
        "titles": ["積立利率金利連動型終身保険"], "category": "whole_life", "subtype": "rate_reset_whole_life",
        "default_plan": "all", "guarantee_terms_years": [15], "status": "discontinued",
        "default_currency": "JPY",
        "deferral_note": "積立利率保証期間 is 15 years when the contract age (or renewal age) is 69 or under and lifetime from 70 (hub text); the rate is renewed at the end of the period. The hub marks the form 現在お取扱い中の商品はありません from 2024-04. The hub table prints no currency; JPY is assumed from the magnitude of the rates (0.05%-0.94%, 2020-2024), which is an inference. gu_int_rate3_base is stated as 米ドル建 (確定積立金区分型) and is stored as USD. Caveat (checker, 2026-11-17): the hub section for this form links no 基準金利 page (it refers MVA questions to the call centre); gu_int_rate2_base and gu_int_rate3_base are titled 積立利率金利連動型終身保険（確定積立金区分型） and （米ドル建・確定積立金区分型） and use age bands 51-75 / 76-85, unlike the hub table (50-69 ... 86-90). Their attribution to this form is by name only, and the USD currency of this product rests solely on gu_int_rate3_base.",
    },
}
LEGACY_TITLE_KEYS = {}
for _k, _f in FORMS.items():
    for _t in _f["titles"]:
        LEGACY_TITLE_KEYS[_t] = _k

RATE_DIR_URL = "https://www.nw-life.co.jp/product/annuities/rate/"
HUB_PAGE = {"name": "gu_int_rate", "kind": "rate", "url": RATE_DIR_URL + "gu_int_rate.html", "live_file": "gu_int_rate"}
FORM_BASE_PAGES = [
    {"name": "gu_int_rate14_base", "form": "sk", "suffix": "", "applies_to": "ご契約日が2024年10月1日以降"},
    {"name": "gu_int_rate10_base", "form": "sk", "suffix": "_pre2410", "applies_to": "ご契約日が2024年9月30日以前"},
    {"name": "gu_int_rate6_base", "form": "fla", "suffix": ""},
    {"name": "gu_int_rate9_base", "form": "fla2", "suffix": ""},
    {"name": "gu_int_rate7_base", "form": "sts", "suffix": ""},
    {"name": "gu_int_rate8_base", "form": "stts", "suffix": ""},
    {"name": "gu_int_rate16_base", "form": "stts25", "suffix": ""},
    {"name": "gu_int_rate17_base", "form": "sttsr", "suffix": ""},
    {"name": "gu_int_rate11_base", "form": "fiwl", "suffix": ""},
    {"name": "gu_int_rate4_base", "form": "tsk", "suffix": ""},
    {"name": "gu_int_rate2_base", "form": "old", "suffix": "", "default_currency": "JPY",
     "applies_to": "積立利率金利連動型終身保険（確定積立金区分型）; currency not printed, JPY inferred"},
    {"name": "gu_int_rate3_base", "form": "old", "suffix": "", "applies_to": "積立利率金利連動型終身保険（米ドル建・確定積立金区分型）"},
]
for _p in FORM_BASE_PAGES:
    _p["kind"] = "formbase"
    _p["url"] = RATE_DIR_URL + _p["name"] + ".html"
    _p["live_file"] = _p["name"]

TYPE_MAP = {"指数連動型": "index", "指数連動・確定積増型": "index_fixed"}
IGNORE_LABELS = {"基準金利", "積立利率"}
RATIO_RE = re.compile(r"\((\d)\)\s*基本終身保険金額が生存給付金額の2倍(以上|未満)")


def form_pid(key):
    return "nwl-form-" + FORMS[key]["slug"]


def split_groups(page):
    """Group the hub by top-level heading (div.tpl-title--l / --pagetitle); --m sub-headings stay in their group."""
    page = strip_comments(page)
    heads = [(m.start(), m.end(), m.group(1), norm(m.group(2)))
             for m in re.finditer(r'<div class="(tpl-title[^"]*)"[^>]*>\s*<(?:p|h\d) class="label">([\s\S]*?)</(?:p|h\d)>', page)]
    top = [h for h in heads if "--l" in h[2] or "--pagetitle" in h[2]]
    out = []
    for i, (start, end, cls, title) in enumerate(top):
        stop = top[i + 1][0] if i + 1 < len(top) else len(page)
        out.append((title, page[end:stop]))
    return out


def title_key(title):
    t = title.replace(" ", "")
    return LEGACY_TITLE_KEYS.get(t)


def _currency_from_text(text):
    m = re.search(r"(米ドル|豪ドル|円)", text)
    return CURRENCY_BY_JA[m.group(1)] if m else None


def parse_form_table(t_html, ctx):
    """Parse one rate table of any hub form into points (plan, currency, term, age, text)."""
    rows = grid(t_html)
    summary = re.search(r'summary="([^"]*)"', t_html)
    summary = norm(summary.group(1)) if summary else ""
    currency_title, premium, start = None, None, 0
    if len({c[0] for c in rows[0]}) == 1:
        m = re.search(r"指定通貨[:：]\s*(円|米ドル|豪ドル)", rows[0][0][0])
        if m:
            currency_title, start = CURRENCY_BY_JA[m.group(1)], 1
            if "3,000万円未満" in rows[0][0][0]:
                premium = "prem_lt3000"
            elif "3,000万円以上" in rows[0][0][0]:
                premium = "prem_ge3000"
    is_val = lambda s: is_pct(s) or is_dash(s)
    first = next((i for i in range(start, len(rows)) if is_val(rows[i][-1][0])), None)
    if first is None:
        raise ValueError("no data rows")
    width = len(rows[first])
    header = [r for r in rows[start:first] if len(r) == width]

    def labels_len(r):
        n = 0
        for c in r:
            if is_val(c[0]):
                break
            n += 1
        return n

    L = labels_len(rows[first])
    if L >= width:
        raise ValueError("no value columns")
    col_cur, col_age = {}, {}
    for c in range(L, width):
        for h in header:
            lab = h[c][0]
            if lab in CURRENCY_BY_JA:
                col_cur[c] = CURRENCY_BY_JA[lab]
            elif is_age(lab):
                col_age[c] = age_band(lab)
    out = []
    for r in rows[first:]:
        if len(r) != width or labels_len(r) != L:
            raise ValueError("irregular row %r" % [c[0] for c in r])
        term = payout = age_row = typ = pay = ""
        for lab in (c[0] for c in r[:L]):
            m = re.fullmatch(r"(\d+)年(未満|以上)", lab)
            if re.fullmatch(r"\d+年", lab):
                if not term:
                    term = lab[:-1]
                elif not payout:
                    payout = lab[:-1]
                else:
                    raise ValueError("too many year labels")
            elif m:
                pay = ("lt" if m.group(2) == "未満" else "ge") + m.group(1)
            elif is_age(lab):
                age_row = age_band(lab)
            elif lab in IGNORE_LABELS:
                pass
            elif lab in TYPE_MAP:
                typ = TYPE_MAP[lab]
            else:
                raise ValueError("unrecognised row label %r" % lab)
        parts = [p for p in (premium or ctx.get("premium"), typ, ctx.get("ratio"), ("pay" + pay) if pay else "",
                             ("p" + payout) if payout else "") if p]
        plan = "_".join(parts) or ctx["default_plan"]
        for c in range(L, width):
            cur = col_cur.get(c) or currency_title or _currency_from_text(summary) or ctx.get("default_currency")
            if not cur:
                raise ValueError("currency not found")
            cell = r[c][0]
            if not is_val(cell):
                raise ValueError("unexpected cell %r" % cell)
            out.append({"plan": plan, "currency": cur, "term": term, "age": age_row or col_age.get(c, ""), "text": cell})
    return out


def parse_form_body(key, body, extra=None):
    """Parse every table of a hub group or base page. Returns (effective_date, points)."""
    date = period_start(body)
    if date is None:
        raise ValueError("適用期間 not found")
    spec = FORMS[key]
    points = []
    for m in re.finditer(r"<table[\s\S]*?</table>", body):
        ctx = {"default_plan": spec["default_plan"], "default_currency": spec.get("default_currency")}
        if extra:
            ctx.update(extra)
        if key == "tsk":
            marks = RATIO_RE.findall(norm(body[: m.start()]))
            if not marks:
                raise ValueError("生存給付金 table without a (1)/(2) case heading")
            ctx["ratio"] = "r2ge" if marks[-1][1] == "以上" else "r2lt"
        points += parse_form_table(m.group(0), ctx)
    if not points:
        raise ValueError("no points")
    return date, points


def parse_forms_hub(page):
    """All policy-form sections of a hub capture: ({form key: (date, points)}, errors)."""
    found, errors = {}, []
    for title, body in split_groups(page):
        key = title_key(title)
        if key is None or key in found:
            continue
        if not re.search(r"<table", body):
            continue
        try:
            found[key] = parse_form_body(key, body)
        except (ValueError, IndexError) as err:
            errors.append((key, str(err)))
    return found, errors


def parse_forms_base(page, spec):
    page = strip_comments(page)
    start = page.find("適用期間】")
    if start < 0:
        return None, []
    try:
        extra = {"default_currency": spec.get("default_currency")} if spec.get("default_currency") else None
        date, pts = parse_form_body(spec["form"], page[start:], extra)
    except (ValueError, IndexError) as err:
        return None, [(spec["form"], str(err))]
    for p in pts:
        p["plan"] += spec["suffix"]
    return (date, pts), []


def form_rows(key, date, points, metric, source_url, retrieved, snapshot, wayback):
    grouped = {}
    pid = form_pid(key)
    for p in points:
        value, status = parse_value(p["text"])
        name = "%s__%s__%s.csv" % (pid, metric, p["plan"])
        grouped.setdefault(name, []).append({
            "product_id": pid, "metric": metric, "plan": p["plan"], "currency": p["currency"],
            "term_years": p["term"], "age_band": p["age"], "sex": "", "effective_date": date,
            "value_pct": value, "status": status, "source_url": source_url, "retrieved_date": retrieved,
            "snapshot": common.rel(snapshot), "via_wayback": "true" if wayback else "false",
        })
    return grouped


def ingest_forms(page, text, source_url, retrieved, path, wayback, label):
    """Parse one saved hub/base page for the policy forms and merge. Returns the conflict count."""
    pending = []  # (form key, date, points, metric)
    if page["kind"] == "rate":
        found, errors = parse_forms_hub(text)
        pending = [(k, d, p, "crediting_rate") for k, (d, p) in found.items()]
    else:
        got, errors = parse_forms_base(text, page)
        if got:
            pending = [(page["form"], got[0], got[1], "base_rate")]
    for key, msg in errors:
        print("  %s SKIPPED %s (cannot parse reliably): %s" % (label, key, msg))
    read = new = bad_total = 0
    for key, date, pts, metric in pending:
        grouped = form_rows(key, date, pts, metric, source_url, retrieved, path, wayback)
        for name, rows in sorted(grouped.items()):
            added, bad = common.merge_rates(common.DATA / "rates" / name, rows)
            read += len(rows)
            new += added
            bad_total += len(bad)
            for k, old, newv in bad:
                print("    CONFLICT %s: on file %s, source shows %s" % (" ".join(x for x in k if x), old, newv))
    if not pending and not errors:
        print("  %-34s no policy-form data on this capture" % label)
    else:
        print("  %-34s %4d points read, %4d new (%d forms)" % (label, read, new, len(pending)))
    return bad_total


def collect_forms_wayback():
    log = load_capture_log()
    conflicts = 0
    for page in [HUB_PAGE] + FORM_BASE_PAGES:
        print("%s  %s" % (page["name"], page["url"]))
        for ts, original, status in wayback_captures(page["url"]):
            if status != "200":
                print("  wayback %s SKIPPED (archived HTTP status %s, no content)" % (ts, status))
                continue
            got = get_capture(page["name"], ts, original, log)
            if got is None:
                print("  wayback %s SKIPPED (Wayback Machine refused: rate limit)" % ts)
                continue
            path, wb_url = got
            conflicts += ingest_forms(page, common.decode(path.read_bytes()), wb_url, ts_date(ts), path, True, "wayback " + ts)
    return conflicts


def ingest_forms_live_dir(folder, date):
    """Hand-saved live copies of the hub and of the policy-form base pages (via_wayback=false)."""
    from pathlib import Path
    conflicts = 0
    for page in [HUB_PAGE] + FORM_BASE_PAGES:
        src = Path(folder) / ("%s_%s.html" % (page["live_file"], date))
        if not src.exists():
            continue
        body = src.read_bytes()
        path = common.save_snapshot(SRC / "rates", page["name"], "html", body, date=date)
        conflicts += ingest_forms(page, common.decode(body), page["url"], date, path, False, "forms live " + date)
    return conflicts


# ---- product records

def plan_labels(plan):
    base, _, suffix = plan.partition("_pre2410")
    ja, en = [], []
    table = {
        "prem_lt3000": ("一時払保険料3,000万円未満", "single premium under JPY 30m"),
        "prem_ge3000": ("一時払保険料3,000万円以上", "single premium JPY 30m or more"),
        "prem_all": ("保険料区分の表示なし", "no premium split shown"),
        "all": ("全区分", "all (single table)"),
        "index": ("指数連動型", "index-linked type"),
        "index_fixed": ("指数連動・確定積増型", "index-linked, fixed-accumulation type"),
        "r2ge": ("基本終身保険金額が生存給付金額の2倍以上", "base whole-life amount at least 2x survival benefit"),
        "r2lt": ("基本終身保険金額が生存給付金額の2倍未満", "base whole-life amount under 2x survival benefit"),
        "paylt20": ("生存給付金支払期間20年未満", "benefit payment period under 20 years"),
        "payge20": ("生存給付金支払期間20年以上", "benefit payment period 20 years or more"),
    }
    toks = re.findall(r"prem_(?:lt3000|ge3000|all)|index_fixed|index|r2ge|r2lt|paylt20|payge20|p\d+|all", base)
    if "_".join(toks) != base:
        raise ValueError("no label for plan %r" % plan)
    for tok in toks:
        if tok.startswith("p") and tok[1:].isdigit():
            ja.append("年金受取期間%s年" % tok[1:])
            en.append("annuity payout period %s years" % tok[1:])
        elif tok in table:
            ja.append(table[tok][0])
            en.append(table[tok][1])
        else:
            raise ValueError("no label for plan token %r in %r" % (tok, plan))
    if "_pre2410" in plan or plan.endswith("pre2410"):
        ja.append("ご契約日が2024年9月30日以前")
        en.append("contracts dated 2024-09-30 or earlier")
    return "／".join(ja), ", ".join(en)


def series_for(pid):
    out = []
    for path in sorted((common.DATA / "rates").glob(pid + "__*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        metric, plan = path.stem.split("__")[1:3]
        ja, en = plan_labels(plan)
        dates = sorted({r["effective_date"] for r in rows})
        out.append({
            "metric": metric, "plan": plan, "plan_ja": ja, "plan_en": en,
            "currencies": sorted({r["currency"] for r in rows}), "csv": path.name, "points": len(rows),
            "first_effective": dates[0], "last_effective": dates[-1], "effective_dates": len(dates),
        })
    return sorted(out, key=lambda s: (s["metric"], s["plan"]))


def form_product(key):
    spec = FORMS[key]
    pid = form_pid(key)
    series = series_for(pid)
    currencies = sorted({c for s in series for c in s["currencies"]}, key=["JPY", "USD", "AUD"].index) if series else []
    pages = [{"name": HUB_PAGE["name"], "kind": "rate", "url": HUB_PAGE["url"], "live_file": "gu_int_rate"}]
    pages += [{"name": p["name"], "kind": "base", "url": p["url"], "live_file": p["live_file"],
               **({"applies_to": p["applies_to"]} if p.get("applies_to") else {})}
              for p in FORM_BASE_PAGES if p["form"] == key]
    status = spec.get("status", "on_sale")
    notes = [
        "Keyed by POLICY FORM, not marketed name: the 積立利率 hub (gu_int_rate.html) publishes one rate section per policy form. Marketed names such as 賢者の年金, みらいの果実, 年金新時代, 定期受取型終身 and 生存給付金付終身 are not yet mapped to forms; distributors are therefore not listed.",
        "Rates come from the shared hub page and the form's 基準金利 page. Collected by scripts/nwl.py (Wayback captures via_wayback=true; live pages via_wayback=false, saved by hand on 2026-11-17 and read directly by --live since). nw-life.co.jp only answers from some networks, such as a connection in Japan. Each capture shows only the then-current half-month 適用期間; effective_date is its first day. Gaps between captures are not interpolated.",
        spec["deferral_note"],
        "Plan key encoding: term_years is 据置期間 where the table has one; age_band is 契約時の被保険者年齢 as printed (the live table, whatever its bands at the time); plan holds the other table dimensions (see plan_ja). Cells printed '-' are status not_offered.",
        "has_mva is true because the form's 基準金利 page states that the 基準金利 is used for the 積立利率 and for the 市場価格調整率 on surrender; the MVA formula and loads are not collected.",
        "Premium type: single premium is assumed from the task scope and from the しおり index, which groups only the 予定利率金利連動型外貨建個人年金保険 and 利率固定 外貨建個人年金保険 under 平準払; the product pages were not read to confirm the premium type of this form." if spec["category"] == "whole_life"
        else "Single premium: the hub states 一時払保険料 for the form's rate tables or groups it with the other single-premium annuities; the product page was not read.",
        "Launch and withdrawal dates not established; the earliest Wayback capture of the hub section is the earliest date any rate here is on file." if status == "on_sale"
        else "Withdrawn: the しおり index files this form under お取扱停止. The last hub capture on file that shows it is 2024-04-04; off-sale date not established.",
    ]
    return {
        "id": pid, "insurer_id": "nwl", "name_ja": spec["name_ja"], "name_en": spec["name_en"],
        "policy_name_ja": spec["name_ja"], "category": spec["category"], "product_type": spec["product_type"], "subtype": spec["subtype"],
        "premium_type": "single", "currencies": currencies, "has_mva": True, "status": status,
        "on_sale_date": None, "off_sale_date": None, "guarantee_terms_years": spec["guarantee_terms_years"],
        "metrics": {"crediting_rate": "積立利率", "base_rate": "基準金利（積立利率の設定および市場価格調整率の計算に用いる）"},
        "rate_page": "gu_int_rate", "pages": pages, "rate_series": series,
        "documents": [{"name": "shiori_web", "title_ja": "WEB版ご契約のしおり・約款（%s）" % spec["name_ja"],
                       "url": "https://www.nw-life.co.jp/shiori/%s/" % spec["shiori"]}],
        "distributors": [], "editions": [], "notes": notes,
    }


SERIES_STATS = ("currencies", "points", "first_effective", "last_effective", "effective_dates")


def keep_maintained(generated, existing):
    """Merge a regenerated form product into the file on disk without losing hand-maintained fields.

    Names, reader notes, documents, plan names, product type and the candidate flag are edited by hand
    after the first generation, so the file on disk wins for every field except the data-derived ones:
    the per-series counts and dates, any new series, and the product's currencies.
    """
    out = dict(existing)
    by_key = {(s["metric"], s["plan"]): s for s in existing.get("rate_series", [])}
    series = []
    for s in generated["rate_series"]:
        old = by_key.pop((s["metric"], s["plan"]), None)
        if old is None:
            series.append(s)
        else:
            series.append({**old, **{k: s[k] for k in SERIES_STATS}})
    series += list(by_key.values())  # a series whose CSV vanished is kept, not dropped silently
    out["rate_series"] = sorted(series, key=lambda s: (s["metric"], s["plan"]))
    out["currencies"] = generated["currencies"]
    return out


def write_form_products():
    for key in FORMS:
        data = form_product(key)
        path = common.DATA / "products" / (data["id"] + ".json")
        if path.exists():
            data = keep_maintained(data, json.loads(path.read_text(encoding="utf-8")))
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print("  wrote %s (%d rate series)" % (common.rel(path), len(data["rate_series"])))


# ---------------------------------------------------------------- main

def ingest_file(product, args):
    """Parse a locally saved page (live copy saved from a browser, or a Wayback capture for testing)."""
    kind = args.kind
    if kind == "rate":
        page = next(p for p in product["pages"] if p.get("kind") == "rate")
    elif kind.startswith("base:"):
        name = kind.split(":", 1)[1]
        page = next(p for p in product["pages"] if p.get("kind") == "base" and p["url"].endswith("/" + name + ".html"))
    else:
        raise SystemExit("--kind must be rate or base:<page name>")
    body = open(args.file, "rb").read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    path = common.save_snapshot(SRC / "rates", page["name"], "html", body, date=args.retrieved)
    print("  saved %s" % common.rel(path))
    return ingest(page, common.decode(body), page["url"], args.retrieved, path, False, "file " + args.retrieved)


def ingest_live_dir(product, args):
    """Add a folder of hand-saved live copies (raw bytes, named <name>_<YYYY-MM-DD>.<ext>).

    Rate and base pages are parsed with via_wayback=false; product pages are kept under
    sources/nwl/pages/; PDFs go to sources/nwl/docs/<product_id>/ and into the manifest as
    the current version. Files are matched by the names in the product JSON.
    """
    from pathlib import Path
    folder, date = Path(args.live_dir), args.retrieved
    conflicts = 0
    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    for page in product["pages"]:
        src = folder / ("%s_%s.html" % (page.get("live_file", page["name"]), date))
        if not src.exists():
            continue
        body = src.read_bytes()
        if page["kind"] in ("rate", "base"):
            path = common.save_snapshot(SRC / "rates", page["name"], "html", body, date=date)
            print("  saved %s" % common.rel(path))
            conflicts += ingest(page, common.decode(body), page["url"], date, path, False, "live " + date)
        else:
            print("  saved %s" % common.rel(common.save_snapshot(SRC / "pages", page["name"], "html", body, date=date)))
    for doc in product["documents"]:
        src = folder / ("%s_%s.pdf" % (doc["live_file"], date)) if doc.get("live_file") else None
        if src is None or not src.exists():
            continue
        body = src.read_bytes()
        if not body.startswith(b"%PDF"):
            print("  doc   %s SKIPPED (not a PDF)" % src.name)
            continue
        path = common.save_snapshot(SRC / "docs" / product["id"], doc["name"], "pdf", body, date=date)
        manifest[(product["id"], doc["name"], common.rel(path))] = {
            "product_id": product["id"], "name": doc["name"], "title_ja": doc["title_ja"], "url": doc["url"],
            "file": common.rel(path), "bytes": str(len(body)), "sha256": common.sha256(body),
            "retrieved_date": date, "via_wayback": "false", "wayback_url": "", "capture_timestamp": "",
            "status": pdf_status(body, wayback=False),
        }
        print("  doc   %s (%.1f MB, live copy)" % (common.rel(path), len(body) / 1e6))
    write_manifest(manifest_path, manifest)
    return conflicts


def save_live(folder):
    """Save today's live pages into `folder` (named as --live-dir expects); returns the Japan date, or exits."""
    import subprocess
    from pathlib import Path
    code = subprocess.call([sys.executable, str(Path(__file__).resolve().parent / "snapshot_jp_rates.py"),
                            "--out", str(folder), "--sites", "nwl", "--no-zip"])
    manifest = Path(folder) / "manifest.csv"
    saved = []
    if manifest.exists():
        with manifest.open(newline="", encoding="utf-8") as fh:
            saved = [r for r in csv.DictReader(fh) if r["file"]]
    if code != 0 or not saved:
        raise SystemExit("nw-life.co.jp could not be read from this network (it only answers from some networks, "
                         "such as a connection in Japan). Connect through one and run again, or use --live-dir.")
    date = saved[0]["file"].rsplit("_", 1)[1].split(".")[0]
    save_form_base_pages(Path(folder) / "nwl", date)
    return date


def save_form_base_pages(folder, date):
    """snapshot_jp_rates.py saves only the hub and five 基準金利 pages, so the policy-form 基準金利 pages
    (FORM_BASE_PAGES) would never get a current period from --live. Save the missing ones here with the same
    manners: browser User-Agent, 2.5 s between requests, stop on a 403 or a block page."""
    import urllib.request
    from pathlib import Path
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for page in FORM_BASE_PAGES:
        dest = folder / ("%s_%s.html" % (page["live_file"], date))
        if dest.exists():
            continue
        time.sleep(2.5)
        try:
            with urllib.request.urlopen(urllib.request.Request(page["url"], headers={"User-Agent": ua}), timeout=60) as r:
                body = r.read()
        except (urllib.error.URLError, OSError) as err:
            print("  %s NOT SAVED (%s); stopping the 基準金利 fetches" % (page["name"], err))
            return
        low = body[:200000].lower()
        if any(m in low for m in (b"captcha", b"access denied", b"incapsula incident")) or "適用期間】".encode() not in body:
            print("  %s NOT SAVED (block page or no 適用期間); stopping the 基準金利 fetches" % page["name"])
            return
        dest.write_bytes(body)
        print("  saved %s" % dest.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="read today's pages from the live site (needs a network that reaches it, e.g. in Japan)")
    parser.add_argument("--wayback", action="store_true", help="ingest every Wayback capture of the rate and base-rate pages")
    parser.add_argument("--docs", action="store_true", help="download the latest Wayback capture of each document")
    parser.add_argument("--file", help="parse a locally saved page (live copy saved from a browser)")
    parser.add_argument("--live-dir", help="folder of hand-saved live copies of all pages and PDFs (needs --retrieved)")
    parser.add_argument("--retrieved", help="date (YYYY-MM-DD) the --file / --live-dir copies were saved")
    parser.add_argument("--forms-only", action="store_true", help="skip the 悠々時間アドバンス2 pages and handle only the policy-form products (nwl-form-*)")
    parser.add_argument("--kind", default="rate", help="rate (default) or base:<page name> for --file")
    args = parser.parse_args()
    if (args.file or args.live_dir) and not (args.retrieved and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.retrieved)):
        parser.error("--file / --live-dir need --retrieved YYYY-MM-DD")
    if args.live and args.live_dir:
        parser.error("use --live or --live-dir, not both")
    if not (args.live or args.wayback or args.docs or args.file or args.live_dir or args.forms_only):
        parser.error("nothing to do: use --live, --wayback, --docs, --file or --live-dir")
    if args.live:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            args.retrieved = save_live(tmp)
            args.live_dir = str(Path(tmp) / "nwl")
            return run(args)
    return run(args)


def run(args):
    product = json.loads(PRODUCT_FILE.read_text(encoding="utf-8"))
    conflicts = 0
    if not args.forms_only:
        print("%s  %s" % (product["id"], product["name_ja"]))
        if args.file:
            conflicts += ingest_file(product, args)
        if args.live_dir:
            conflicts += ingest_live_dir(product, args)
        if args.wayback:
            conflicts += collect_wayback(product)
    print("policy-form products (nwl-form-*)")
    if args.live_dir:
        conflicts += ingest_forms_live_dir(args.live_dir, args.retrieved)
    if args.wayback:
        conflicts += collect_forms_wayback()
    write_form_products()
    if args.docs:
        manifest_path = SRC / "docs" / "manifest.csv"
        manifest = load_manifest(manifest_path)
        download_documents(product, manifest)
        write_manifest(manifest_path, manifest)
    if conflicts:
        print("%d conflicts: the source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
