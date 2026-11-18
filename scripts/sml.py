"""Collector for Sumitomo Life (住友生命), たのしみグローバルⅢ（定率増加プラン）.

Reads every data/products/sml-*.json and, for each product:
  - snapshots its pages (plan page, distributor list, distributor pages)
  - walks the insurer's rate site (a JSF application that needs a session
    cookie) and collects, for every year and every 目標額 setting on offer:
      * 積立利率 history   (metric crediting_rate)
      * 市場価格調整用利率 history (metric mva_rate)
    It also reads the current-rate page and cross-checks it against the
    newest history row.
  - with --channels, compares the current 積立利率 shown on the other
    distributor channels' rate pages (read only, nothing is stored as data)
  - with --docs, downloads the listed documents

Products whose rate_site has a "kind" use their own collector:
  index_linked  たのしみグローバルⅢ（指数連動プラン）: 連動率 and 積立利率 share one
                history table (two rows per period), plus 市場価格調整用利率
  whole_life    ふるはーとJロードグローバルⅢ: 予定利率 and 実質的な利回り (age 60,
                male / female) per 健康告知 plan, plus 市場価格調整用利率
Products without a "kind" are the fixed-rate たのしみグローバルⅢ plan above.

Usage:
    python3 scripts/sml.py             # pages and rates
    python3 scripts/sml.py --docs      # pages, rates and documents
    python3 scripts/sml.py --channels  # also compare the other channels
    python3 scripts/sml.py --only sml-tanoshimi-global-3-teiritsu

Raw snapshots go to sources/sml/, rates to data/rates/. Re-running is safe:
unchanged pages are not saved twice and existing data points are never
rewritten.

Privacy: requests carry the generic User-Agent from common.py and the rate
site's own session cookie. The only form fields sent are the ones the site
itself puts in its hidden fields plus the year / 目標額 selectors.

effective_date is the first day of the 責任開始日 period (the half-month
period within which the policy must be taken up) that the rate applies to.
The site's table also shows a single 契約日 for each period, which falls
about a month later; that date is not stored.
"""

import argparse
import csv
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import common

SRC = common.SOURCES / "sml"
DATE_RANGE = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s*～\s*(\d{4})/(\d{2})/(\d{2})")


# ---------------------------------------------------------------- session ---


class Session:
    """A cookie-keeping client for the JSF rate site. One request per second."""

    def __init__(self, base, pause=1.0):
        self.base = base
        self.pause = pause
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, fields=None, retries=3):
        url = path if path.startswith("http") else self.base + path
        data = urllib.parse.urlencode(fields, encoding="shift_jis").encode("ascii") if fields is not None else None
        last = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": common.USER_AGENT})
                with self.opener.open(req, timeout=40) as resp:
                    body = resp.read()
                time.sleep(self.pause)
                return body
            except urllib.error.HTTPError as err:
                if err.code < 500:
                    raise
                last = err
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                last = err
            time.sleep(3 * (attempt + 1))
        raise RuntimeError("failed to fetch %s: %s" % (url, last))


def text_of(body):
    return html.unescape(body.decode("shift_jis", "replace"))


def forms(page):
    """Return [(form id, action, [(name, value, type)])] for every <form>."""
    out = []
    for m in re.finditer(r"<form\b([^>]*)>([\s\S]*?)</form>", page):
        attrs = m.group(1)
        action = re.search(r'action="([^"]*)"', attrs)
        fid = re.search(r'\bid="([^"]*)"', attrs)
        fields = []
        for tag in re.finditer(r"<input\b([^>]*)>", m.group(2)):
            at = tag.group(1)
            name = re.search(r'\bname="([^"]*)"', at)
            if not name:
                continue
            value = re.search(r'\bvalue="([^"]*)"', at)
            ftype = re.search(r'\btype="([^"]*)"', at)
            fields.append((name.group(1), value.group(1) if value else "", ftype.group(1) if ftype else ""))
        out.append((fid.group(1) if fid else "", action.group(1) if action else "", fields))
    return out


def find_form(page, form_id):
    for fid, action, fields in forms(page):
        if fid == form_id:
            return action, fields
    raise RuntimeError("form %r not found on page" % form_id)


def bootstrap(session, url):
    """GET an entry URL, then submit the auto-submit form it returns.

    Returns the raw bytes of the real page.
    """
    page = text_of(session.request(url))
    _, action, fields = forms(page)[0]
    return session.request(action, [(n, v) for n, v, _ in fields])


def hidden_fields(fields, drop=()):
    """The form's non-button fields, minus any named in drop."""
    return [(n, v) for n, v, t in fields if t != "submit" and n not in drop]


def select_years(page, name):
    block = re.search(r'<select[^>]*name="%s"[^>]*>([\s\S]*?)</select>' % re.escape(name), page)
    if not block:
        raise RuntimeError("selector %s not found" % name)
    return sorted(re.findall(r'<option value="(\d{4})"', block.group(1)))


# --------------------------------------------------------------- snapshots ---

_VOLATILE = [
    (re.compile(rb'(name="javax\.faces\.ViewState"[^>]*value=")[^"]*'), rb"\1"),
    (re.compile(rb'(name="sourceData"[^>]*value=")[^"]*'), rb"\1"),
    (re.compile(rb"<script[^>]*ruxitagentjs[^>]*></script>"), b""),
]


def stable_hash(body):
    """Hash of a page with per-session tokens removed, so that a re-run that
    only differs in ViewState / sourceData / monitoring-script values counts
    as unchanged."""
    for pattern, repl in _VOLATILE:
        body = pattern.sub(repl, body)
    return common.sha256(body)


def save_page(folder, name, body):
    """Like common.save_snapshot, but ignores the per-session tokens."""
    date = common.today()
    folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(re.escape(name) + r"_\d{4}-\d{2}-\d{2}\.html$")
    earlier = sorted(p for p in folder.iterdir() if pattern.match(p.name))
    if earlier and stable_hash(earlier[-1].read_bytes()) == stable_hash(body):
        return earlier[-1]
    path = folder / ("%s_%s.html" % (name, date))
    path.write_bytes(body)
    return path


# ----------------------------------------------------------------- parsing ---


def cells(page):
    """Table rows as lists of (text, colspan) with colspans expanded."""
    out = []
    page = re.sub(r"<style[\s\S]*?</style>|<script[\s\S]*?</script>", "", page)
    for row in common.table_rows(page):
        line = []
        for text, colspan, _ in row:
            line.extend([text] * colspan)
        out.append(line)
    return out


def column_labels(rows, first_data):
    """(currency, term_years) for each value column, from the two header rows."""
    header = rows[first_data - 1]  # 契約日 | 責任開始日 | 据置期間N年 ...
    curr_row = rows[first_data - 2]  # blank | 米ドル | 米ドル | 豪ドル ...
    offset = len(header) - len(curr_row)
    cols = []
    for i, label in enumerate(header):
        term = re.fullmatch(r"据置期間(\d+)年", label)
        if not term:
            continue
        currency = common.CURRENCIES.get(curr_row[i - offset] if i - offset >= 0 else "")
        if not currency:
            raise ValueError("no currency above column %d (%r)" % (i, label))
        cols.append((i, currency, term.group(1)))
    return cols


def parse_history(page):
    """Parse a history result page into [(period_start, currency, term, text)]."""
    rows = cells(page)
    is_data = lambda r: len(r) > 2 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", r[0]) and DATE_RANGE.search(r[1])
    first = next((i for i, r in enumerate(rows) if is_data(r)), None)
    if first is None:
        return []
    cols = column_labels(rows, first)
    out = []
    for r in rows[first:]:
        if not is_data(r):
            continue
        m = DATE_RANGE.search(r[1])
        start = "%s-%s-%s" % m.group(1, 2, 3)
        values = r[2:]
        if len(values) < len(cols):  # a trailing cell spanning the rest, e.g. 円建の取扱いはありません
            values = values + [values[-1]] * (len(cols) - len(values))
        if len(values) != len(cols):
            raise ValueError("row %s has %d values for %d columns" % (r[0], len(values), len(cols)))
        for (_, currency, term), text in zip(cols, values):
            out.append((start, currency, term, text))
    return out


def parse_current(page):
    """Parse the current-rate page into (period_start, period_end, {(plan_no, currency, term): text})."""
    rows = cells(page)
    period = None
    for r in rows:
        m = DATE_RANGE.search(" ".join(r))
        if m and "適用される" in " ".join(r):
            period = ("%s-%s-%s" % m.group(1, 2, 3), "%s-%s-%s" % m.group(4, 5, 6))
            break
    if not period:
        raise ValueError("no period found on current-rate page")
    values = {}
    for r in rows:
        if r and r[0] in ("米ドル", "豪ドル", "円") and len(r) > 1:
            currency = common.CURRENCIES[r[0]]
            cols = r[1:]
            if len(cols) == 3:  # 円: 5年, 10年, then one cell spanning the 目標額設定あり columns
                cols = cols + [cols[-1]]
            for idx, text in enumerate(cols):
                plan = "1" if idx < 2 else "2"
                values[(plan, currency, "5" if idx % 2 == 0 else "10")] = text
    return period[0], period[1], values


def is_rate(text):
    return re.fullmatch(r"\d+(\.\d+)?%", text) is not None


# --------------------------------------------------------------- collection ---


def make_rows(product, series, year, plan_value, url, snapshot, parsed, retrieved):
    rows = []
    for start, currency, term, text in parsed:
        offered = is_rate(text)
        rows.append(
            {
                "product_id": product["id"],
                "metric": series["metric"],
                "plan": series["plan"],
                "currency": currency,
                "term_years": term,
                "age_band": "",
                "sex": "",
                "effective_date": start,
                "value_pct": text.rstrip("%") if offered else "",
                "status": "published" if offered else "not_offered",
                "source_url": url,
                "retrieved_date": retrieved,
                "snapshot": common.rel(snapshot),
                "via_wayback": "false",
            }
        )
    return rows


def write_series(series, rows, label):
    csv_path = common.DATA / "rates" / series["csv"]
    added, conflicts = common.merge_rates(csv_path, rows)
    dates = sorted({r["effective_date"] for r in rows})
    if dates:
        print("  %-62s %4d points read, %4d new, %s to %s" % (series["csv"], len(rows), added, dates[0], dates[-1]))
    else:
        print("  %s: nothing read" % series["csv"])
    for key, old, new in conflicts:
        print("    CONFLICT %s: on file %s, site now shows %s" % (" ".join(k for k in key if k), old, new))
    return len(conflicts)


def collect_rates(product):
    site = product["rate_site"]
    session = Session(site["base"])
    entry = site["entry_url"]
    retrieved = common.today()
    conflicts = 0
    warnings = 0

    # 1. current-rate page
    current = bootstrap(session, entry)
    path = save_page(SRC / "rates", "current_" + site["chid"], current)
    print("  page  %s" % common.rel(path))
    cur_start, cur_end, cur_values = parse_current(text_of(current))
    print("  current period %s to %s" % (cur_start, cur_end))

    # 2. 積立利率 history: its own entry URL and form.
    # 3. MVA: tab button on a fresh current-rate page -> bootstrap -> MVA page,
    #    which carries its own history form.
    jobs = [("crediting_rate", site["history"]["crediting_rate"]), ("mva_rate", site["history"]["mva_rate"])]

    all_rows = {}
    for metric, cfg in jobs:
        session = Session(site["base"])
        if metric == "crediting_rate":
            hist_page = text_of(bootstrap(session, site["base"] + cfg["entry_path"]))
        else:
            start_page = text_of(bootstrap(session, entry))
            action, fields = find_form(start_page, "j_id_a")
            tab = bootstrap_post(session, action, hidden_fields(fields) + [(cfg["tab_button"], cfg["tab_label"])])
            path = save_page(SRC / "rates", "mva_current_" + site["chid"], tab)
            print("  page  %s" % common.rel(path))
            hist_page = text_of(tab)
        action, fields = find_form(hist_page, "j_id_a")
        years = select_years(hist_page, cfg["year_field"])
        for series in [s for s in product["rate_series"] if s["metric"] == metric]:
            rows = []
            for year in years:
                body = session.request(
                    action,
                    hidden_fields(fields, drop=(cfg["year_field"], cfg["plan_field"]))
                    + [(cfg["year_field"], year), (cfg["plan_field"], series["plan_value"]), (cfg["submit"][0], cfg["submit"][1])],
                )
                name = "%s_%s_%s" % (cfg["snapshot_prefix"], year, series["plan"])
                snap = save_page(SRC / "rates", name, body)
                parsed = parse_history(text_of(body))
                if not parsed:
                    print("  %s %s: no rows" % (series["plan"], year))
                url = "%s#year=%s&plan=%s" % (cfg.get("source_url", entry), year, series["plan"])
                rows.extend(make_rows(product, series, year, series["plan_value"], url, snap, parsed, retrieved))
            all_rows[(metric, series["plan"])] = rows
            conflicts += write_series(series, rows, metric)

    # 4. cross-check: the current page must agree with the newest history rows
    for series in [s for s in product["rate_series"] if s["metric"] == "crediting_rate"]:
        newest = {(r["currency"], r["term_years"]): r for r in all_rows[("crediting_rate", series["plan"])] if r["effective_date"] == cur_start}
        for (plan_no, currency, term), text in cur_values.items():
            if plan_no != series["plan_value"]:
                continue
            row = newest.get((currency, term))
            shown = text.rstrip("%") if is_rate(text) else ""
            if row is None or row["value_pct"] != shown:
                warnings += 1
                print("    MISMATCH current page %s %s %sy %s vs history %s" % (series["plan"], currency, term, text, row and row["value_pct"]))
    print("  current page agrees with newest history row: %s" % ("yes" if not warnings else "NO"))
    return conflicts + warnings


def bootstrap_post(session, action, fields):
    """POST a tab button, then submit the auto-submit form in the answer."""
    page = text_of(session.request(action, fields))
    _, act2, f2 = forms(page)[0]
    return session.request(act2, [(n, v) for n, v, _ in f2])


def compare_channels(product):
    """Read the current-rate page of each other channel and compare 積立利率."""
    site = product["rate_site"]
    reference = None
    for ch in product.get("channels", []):
        session = Session(site["base"])
        url = "%s?chid=%s&type=%s" % (site["base"] + site["entry_path"], ch["chid"], site["type"])
        body = bootstrap(session, url)
        save_page(SRC / "rates", "current_" + ch["chid"], body)
        try:
            start, end, values = parse_current(text_of(body))
        except (ValueError, IndexError):
            print("  %-5s no rate page (%s)" % (ch["chid"], common.clean(text_of(body))[:80]))
            continue
        plans = sorted({k[0] for k in values})
        shown = {k: v for k, v in values.items() if k[0] == "1"}
        if reference is None:
            reference = shown
        same = shown == reference
        print("  %-5s period %s to %s, 目標額 plans shown: %s, no_target values %s the reference channel" % (ch["chid"], start, end, ",".join(plans), "match" if same else "DIFFER from"))
    return 0


# ------------------------------------------------- index-linked plan (idx) ---
# The rate page shows, for every period, a 連動率 row and a 積立利率 row.


def paired_values(rows, first, cols):
    """For one period starting at rows[first] (the 連動率 row), return
    {metric: [text per column]} using that row and the 積立利率 row after it."""
    top = rows[first][3:]
    nxt = rows[first + 1] if first + 1 < len(rows) else []
    if not nxt or nxt[0] != "積立利率":
        raise ValueError("no 積立利率 row after %s" % rows[first][0])
    bottom = nxt[1:]
    out = {}
    for metric, values in (("participation_rate", top), ("crediting_rate", bottom)):
        if metric == "crediting_rate" and 0 < len(values) < len(cols):
            # the 円建の取扱いはありません cell spans both rows, so the lower row omits it
            values = values + top[len(values):]
        if 0 < len(values) < len(cols):  # a trailing cell spanning the rest
            values = values + [values[-1]] * (len(cols) - len(values))
        if len(values) != len(cols):
            raise ValueError("%s row of %s has %d values for %d columns" % (metric, rows[first][0], len(values), len(cols)))
        out[metric] = values
    return out


def parse_history_paired(page):
    """Parse the index-linked history page into [(period_start, metric, currency, term, text)]."""
    rows = cells(page)
    is_first = lambda r: len(r) > 3 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", r[0]) and DATE_RANGE.search(r[1]) and r[2] == "連動率"
    out = []
    first_idx = next((i for i, r in enumerate(rows) if is_first(r)), None)
    if first_idx is None:
        return out
    cols = column_labels(rows, first_idx)
    for i, r in enumerate(rows):
        if not is_first(r):
            continue
        m = DATE_RANGE.search(r[1])
        start = "%s-%s-%s" % m.group(1, 2, 3)
        values = paired_values(rows, i, cols)
        for metric, texts in values.items():
            for (_, currency, term), text in zip(cols, texts):
                out.append((start, metric, currency, term, text))
    return out


def parse_current_index(page):
    """Current-rate page of the index-linked plan.

    Returns (period_start, period_end, {(metric, plan_no, currency, term): text}).
    """
    rows = cells(page)
    period = None
    for r in rows:
        m = DATE_RANGE.search(" ".join(r))
        if m and "適用される" in " ".join(r):
            period = ("%s-%s-%s" % m.group(1, 2, 3), "%s-%s-%s" % m.group(4, 5, 6))
            break
    if not period:
        raise ValueError("no period found on current-rate page")
    values = {}
    for i, r in enumerate(rows):
        if len(r) > 2 and r[0] in ("米ドル", "豪ドル", "円") and r[1] == "連動率":
            currency = common.CURRENCIES[r[0]]
            nxt = rows[i + 1]
            if nxt[0] != "積立利率":
                raise ValueError("no 積立利率 row under %s" % r[0])
            for metric, cols in (("participation_rate", r[2:]), ("crediting_rate", nxt[1:])):
                if metric == "crediting_rate" and len(cols) < 4:
                    cols = cols + r[2:][len(cols):]  # cell spanning both rows
                if len(cols) < 4:
                    cols = cols + [cols[-1]] * (4 - len(cols))
                for idx, text in enumerate(cols[:4]):
                    values[(metric, "1" if idx < 2 else "2", currency, "5" if idx % 2 == 0 else "10")] = text
    return period[0], period[1], values


def point(product, series, start, currency, term, age, sex, text, url, snapshot, retrieved):
    offered = is_rate(text)
    return {
        "product_id": product["id"],
        "metric": series["metric"],
        "plan": series["plan"],
        "currency": currency,
        "term_years": term,
        "age_band": age,
        "sex": sex,
        "effective_date": start,
        "value_pct": text.rstrip("%") if offered else "",
        "status": "published" if offered else "not_offered",
        "source_url": url,
        "retrieved_date": retrieved,
        "snapshot": common.rel(snapshot),
        "via_wayback": "false",
    }


def collect_index(product):
    site = product["rate_site"]
    pre = site["snapshot_prefix"]
    retrieved = common.today()
    problems = 0

    session = Session(site["base"])
    current = bootstrap(session, site["entry_url"])
    path = save_page(SRC / "rates", "%s_current_%s" % (pre, site["chid"]), current)
    print("  page  %s" % common.rel(path))
    cur_start, cur_end, cur_values = parse_current_index(text_of(current))
    print("  current period %s to %s" % (cur_start, cur_end))

    # 連動率 + 積立利率: one history page per year and plan carries both
    cfg = site["history"]["indexed_rates"]
    session = Session(site["base"])
    hist_page = text_of(bootstrap(session, site["base"] + cfg["entry_path"]))
    action, fields = find_form(hist_page, "j_id_a")
    years = select_years(hist_page, cfg["year_field"])
    all_rows = {}
    for plan in sorted({s["plan"]: s["plan_value"] for s in product["rate_series"]}.items()):
        plan_key, plan_value = plan
        by_metric = {"participation_rate": [], "crediting_rate": []}
        for year in years:
            body = session.request(
                action,
                hidden_fields(fields, drop=(cfg["year_field"], cfg["plan_field"]))
                + [(cfg["year_field"], year), (cfg["plan_field"], plan_value), (cfg["submit"][0], cfg["submit"][1])],
            )
            snap = save_page(SRC / "rates", "%s_history_%s_%s" % (pre, year, plan_key), body)
            parsed = parse_history_paired(text_of(body))
            if not parsed:
                print("  %s %s: no rows" % (plan_key, year))
            url = "%s#year=%s&plan=%s" % (cfg["source_url"], year, plan_key)
            for series in [s for s in product["rate_series"] if s["plan"] == plan_key and s["metric"] in by_metric]:
                by_metric[series["metric"]].extend(
                    point(product, series, start, currency, term, "", "", text, url, snap, retrieved)
                    for start, metric, currency, term, text in parsed
                    if metric == series["metric"]
                )
        for series in [s for s in product["rate_series"] if s["plan"] == plan_key and s["metric"] in by_metric]:
            all_rows[(series["metric"], plan_key)] = by_metric[series["metric"]]
            problems += write_series(series, by_metric[series["metric"]], series["metric"])

    # 市場価格調整用利率: same layout as the fixed-rate plan
    cfg = site["history"]["mva_rate"]
    session = Session(site["base"])
    start_page = text_of(bootstrap(session, site["entry_url"]))
    action, fields = find_form(start_page, "j_id_a")
    tab = bootstrap_post(session, action, hidden_fields(fields) + [(cfg["tab_button"], cfg["tab_label"])])
    path = save_page(SRC / "rates", "%s_mva_current_%s" % (pre, site["chid"]), tab)
    print("  page  %s" % common.rel(path))
    hist_page = text_of(tab)
    action, fields = find_form(hist_page, "j_id_a")
    years = select_years(hist_page, cfg["year_field"])
    for series in [s for s in product["rate_series"] if s["metric"] == "mva_rate"]:
        rows = []
        for year in years:
            body = session.request(
                action,
                hidden_fields(fields, drop=(cfg["year_field"], cfg["plan_field"]))
                + [(cfg["year_field"], year), (cfg["plan_field"], series["plan_value"]), (cfg["submit"][0], cfg["submit"][1])],
            )
            snap = save_page(SRC / "rates", "%s_mva_history_%s_%s" % (pre, year, series["plan"]), body)
            parsed = parse_history(text_of(body))
            if not parsed:
                print("  %s %s: no rows" % (series["plan"], year))
            url = "%s#year=%s&plan=%s" % (cfg["source_url"], year, series["plan"])
            rows.extend(make_rows(product, series, year, series["plan_value"], url, snap, parsed, retrieved))
        problems += write_series(series, rows, "mva_rate")

    # cross-check: the current page must agree with the newest history rows
    warnings = 0
    for (metric, plan_no, currency, term), text in sorted(cur_values.items()):
        plan_key = next(s["plan"] for s in product["rate_series"] if s["plan_value"] == plan_no)
        newest = {(r["currency"], r["term_years"]): r for r in all_rows[(metric, plan_key)] if r["effective_date"] == cur_start}
        row = newest.get((currency, term))
        shown = text.rstrip("%") if is_rate(text) else ""
        if row is None or row["value_pct"] != shown:
            warnings += 1
            print("    MISMATCH current page %s %s %s %sy %s vs history %s" % (metric, plan_key, currency, term, text, row and row["value_pct"]))
    print("  current page agrees with newest history row: %s" % ("yes" if not warnings else "NO"))
    return problems + warnings


def compare_channels_index(product):
    """Read the current-rate page of each other channel and compare 連動率 / 積立利率."""
    site = product["rate_site"]
    reference = None
    for ch in product.get("channels", []):
        session = Session(site["base"])
        url = "%s?chid=%s&type=%s" % (site["base"] + site["entry_path"], ch["chid"], site["type"])
        body = bootstrap(session, url)
        save_page(SRC / "rates", "%s_current_%s" % (site["snapshot_prefix"], ch["chid"]), body)
        try:
            start, end, values = parse_current_index(text_of(body))
        except (ValueError, IndexError, StopIteration, KeyError):
            print("  %-5s no rate page (%s)" % (ch["chid"], common.clean(text_of(body))[:80]))
            continue
        if reference is None:
            reference = values
        diff = sorted(k for k in set(values) | set(reference) if values.get(k) != reference.get(k))
        print("  %-5s period %s to %s, %s the reference channel%s" % (ch["chid"], start, end, "match" if not diff else "DIFFER from", "" if not diff else ": " + str(diff[:6])))
    return 0


# ----------------------------------------- whole life (ふるはーとJロードグローバルⅢ) ---

WL_PLANS = {"健康告知なしプラン": "no_declaration", "3つの健康告知プラン": "declaration_3"}
WL_METRICS = ("planned_rate", "net_yield")


def wl_period(rows):
    for r in rows:
        m = DATE_RANGE.search(" ".join(r))
        if m and "適用される" in " ".join(r):
            return "%s-%s-%s" % m.group(1, 2, 3), "%s-%s-%s" % m.group(4, 5, 6)
    raise ValueError("no period found on current-rate page")


def wl_columns(rows, h):
    """Describe the value columns under the 予定利率 / 実質的な利回り header row rows[h].

    Returns [(position, metric, age_band, sex)] where position indexes the value
    list of a data row (the label column removed).
    """
    labels = rows[h][1:]
    ages = iter(rows[h + 1])
    cols = []
    for pos, label in enumerate(labels):
        if label == "予定利率":
            cols.append((pos, "planned_rate", "", ""))
        elif label.startswith("実質的な利回り"):
            age_label = next(ages)
            age = re.match(r"(\d+)歳", age_label)
            sex = "M" if "男性" in age_label else "F" if "女性" in age_label else ""
            if not age or not sex:
                raise ValueError("unexpected age label %r" % age_label)
            cols.append((pos, "net_yield", age.group(1), sex))
        else:
            raise ValueError("unexpected column label %r" % label)
    return cols


def parse_wl_current(page):
    """Current-rate page: (start, end, [(plan, metric, currency, age, sex, text)])."""
    rows = cells(page)
    start, end = wl_period(rows)
    h = next(i for i, r in enumerate(rows) if len(r) > 2 and r[1] == "予定利率")
    cols = wl_columns(rows, h)
    plans = rows[h - 1][1:]
    out = []
    for r in rows[h + 2 :]:
        if r and r[0] in ("米ドル", "豪ドル") and len(r) == len(cols) + 1:
            currency = common.CURRENCIES[r[0]]
            for pos, metric, age, sex in cols:
                out.append((WL_PLANS[plans[pos]], metric, currency, age, sex, r[1 + pos]))
    if not out:
        raise ValueError("no rate rows on current-rate page")
    return start, end, out


def parse_wl_history(page, plan):
    """予定利率 / 実質的な利回り history page of one plan: [(start, metric, currency, age, sex, text)]."""
    rows = cells(page)
    h = next((i for i, r in enumerate(rows) if len(r) > 2 and r[1] == "予定利率"), None)
    if h is None:
        return []
    cols = wl_columns(rows, h)
    currencies = rows[h - 1][1:]
    out = []
    for r in rows[h + 2 :]:
        m = DATE_RANGE.fullmatch(r[0]) if r else None
        if not m:
            continue
        if len(r) != len(cols) + 1:
            raise ValueError("row %s has %d values for %d columns" % (r[0], len(r) - 1, len(cols)))
        start = "%s-%s-%s" % m.group(1, 2, 3)
        for pos, metric, age, sex in cols:
            out.append((start, metric, common.CURRENCIES[currencies[pos]], age, sex, r[1 + pos]))
    return out


def parse_wl_mva_history(page):
    """市場価格調整用利率 history: [(start, plan, currency, text)] (both plans share one table)."""
    rows = cells(page)
    first = next((i for i, r in enumerate(rows) if r and DATE_RANGE.fullmatch(r[0])), None)
    if first is None:
        return []
    plans = rows[first - 2][1:]
    currencies = rows[first - 1][1:]
    out = []
    for r in rows[first:]:
        m = DATE_RANGE.fullmatch(r[0]) if r else None
        if not m:
            continue
        if len(r) - 1 != len(currencies):
            raise ValueError("row %s has %d values for %d columns" % (r[0], len(r) - 1, len(currencies)))
        start = "%s-%s-%s" % m.group(1, 2, 3)
        for pos, text in enumerate(r[1:]):
            out.append((start, WL_PLANS[plans[pos]], common.CURRENCIES[currencies[pos]], text))
    return out


def parse_wl_mva_current(page):
    rows = cells(page)
    start, end = wl_period(rows)
    # the heading says 解約した場合等に適用される; wl_period matches on 適用される as well
    h = next(i for i, r in enumerate(rows) if len(r) == 3 and r[1] in WL_PLANS)
    plans = rows[h][1:]
    out = []
    for r in rows[h + 1 :]:
        if r and r[0] in ("米ドル", "豪ドル") and len(r) == 3:
            for pos, text in enumerate(r[1:]):
                out.append((start, WL_PLANS[plans[pos]], common.CURRENCIES[r[0]], text))
    return out


def wl_history_pages(site, chid, plans, kinds=("yield", "mva")):
    """Yield (kind, year, plan_key, body) for every history page of one channel.

    kind "yield" is the 予定利率 / 実質的な利回り page (one per year and plan),
    kind "mva" the 市場価格調整用利率 page (one per year, both plans in it).
    """
    if "yield" in kinds:
        cfg = site["history"]["yield"]
        session = Session(site["base"])
        page = text_of(bootstrap(session, site["base"] + cfg["entry_path"].replace("chid=cmon", "chid=" + chid)))
        action, fields = find_form(page, "j_id_a")
        for year in select_years(page, cfg["year_field"]):
            for plan_key, plan_value in plans:
                body = session.request(
                    action,
                    hidden_fields(fields, drop=(cfg["year_field"], cfg["plan_field"]))
                    + [(cfg["year_field"], year), (cfg["plan_field"], plan_value), (cfg["submit"][0], cfg["submit"][1])],
                )
                yield "yield", year, plan_key, body
    if "mva" in kinds:
        cfg = site["history"]["mva_rate"]
        session = Session(site["base"])
        entry = "%s%s?chid=%s" % (site["base"], site["entry_path"], chid)
        start_page = text_of(bootstrap(session, entry))
        action, fields = find_form(start_page, "j_id_a")
        tab = bootstrap_post(session, action, hidden_fields(fields) + [(cfg["tab_button"], cfg["tab_label"])])
        page = text_of(tab)
        action, fields = find_form(page, "j_id_a")
        yield "mva_current", "", "", tab
        for year in select_years(page, cfg["year_field"]):
            body = session.request(
                action,
                hidden_fields(fields, drop=(cfg["year_field"],)) + [(cfg["year_field"], year), (cfg["submit"][0], cfg["submit"][1])],
            )
            yield "mva", year, "", body


def collect_wl(product):
    site = product["rate_site"]
    pre = site["snapshot_prefix"]
    retrieved = common.today()
    series_by = {(s["metric"], s["plan"]): s for s in product["rate_series"]}
    plans = sorted({(s["plan"], s["plan_value"]) for s in product["rate_series"]})
    problems = 0

    session = Session(site["base"])
    current = bootstrap(session, site["entry_url"])
    path = save_page(SRC / "rates", "%s_current_%s" % (pre, site["chid"]), current)
    print("  page  %s" % common.rel(path))
    cur_start, cur_end, cur_values = parse_wl_current(text_of(current))
    print("  current period %s to %s" % (cur_start, cur_end))

    collected = {key: [] for key in series_by}
    mva_current = []
    for kind, year, plan_key, body in wl_history_pages(site, site["chid"], plans):
        if kind == "yield":
            snap = save_page(SRC / "rates", "%s_history_%s_%s" % (pre, year, plan_key), body)
            parsed = parse_wl_history(text_of(body), plan_key)
            if not parsed:
                print("  %s %s: no rows" % (plan_key, year))
            url = "%s#year=%s&plan=%s" % (site["history"]["yield"]["source_url"], year, plan_key)
            for start, metric, currency, age, sex, text in parsed:
                collected[(metric, plan_key)].append(point(product, series_by[(metric, plan_key)], start, currency, "", age, sex, text, url, snap, retrieved))
        elif kind == "mva_current":
            snap = save_page(SRC / "rates", "%s_mva_current_%s" % (pre, site["chid"]), body)
            print("  page  %s" % common.rel(snap))
            mva_current = parse_wl_mva_current(text_of(body))
        else:
            snap = save_page(SRC / "rates", "%s_mva_history_%s" % (pre, year), body)
            parsed = parse_wl_mva_history(text_of(body))
            if not parsed:
                print("  mva %s: no rows" % year)
            url = "%s#year=%s" % (site["history"]["mva_rate"]["source_url"], year)
            for start, plan_key, currency, text in parsed:
                collected[("mva_rate", plan_key)].append(point(product, series_by[("mva_rate", plan_key)], start, currency, "", "", "", text, url, snap, retrieved))
    for key in sorted(collected):
        problems += write_series(series_by[key], collected[key], key[0])

    # cross-check: the current pages must agree with the newest history rows
    warnings = 0
    newest = {}
    for (metric, plan_key), rows in collected.items():
        for r in rows:
            if r["effective_date"] == cur_start:
                newest[(metric, plan_key, r["currency"], r["age_band"], r["sex"])] = r["value_pct"]
    shown = [(plan, metric, currency, age, sex, text) for plan, metric, currency, age, sex, text in cur_values]
    shown += [(plan, "mva_rate", currency, "", "", text) for _, plan, currency, text in mva_current]
    for plan, metric, currency, age, sex, text in shown:
        value = text.rstrip("%") if is_rate(text) else ""
        if newest.get((metric, plan, currency, age, sex)) != value:
            warnings += 1
            print("    MISMATCH current page %s %s %s %s %s %s vs history %s" % (metric, plan, currency, age, sex, text, newest.get((metric, plan, currency, age, sex))))
    print("  current page agrees with newest history row: %s" % ("yes" if not warnings else "NO"))
    return problems + warnings


def compare_channels_wl(product):
    """Compare current rates and the whole history of each channel with the reference one.

    Read only: only the current-rate page of each channel is saved.
    """
    site = product["rate_site"]
    plans = sorted({(s["plan"], s["plan_value"]) for s in product["rate_series"]})
    reference = None
    for ch in product.get("channels", []):
        session = Session(site["base"])
        url = "%s%s?chid=%s" % (site["base"], site["entry_path"], ch["chid"])
        body = bootstrap(session, url)
        try:
            start, end, current = parse_wl_current(text_of(body))
        except (ValueError, IndexError, StopIteration, KeyError):
            print("  %-5s no rate page (%s)" % (ch["chid"], common.clean(text_of(body))[:80]))
            continue
        save_page(SRC / "rates", "%s_current_%s" % (site["snapshot_prefix"], ch["chid"]), body)
        history = set()
        for kind, year, plan_key, page in wl_history_pages(site, ch["chid"], plans):
            if kind == "yield":
                history.update(("yield", year, plan_key) + t for t in parse_wl_history(text_of(page), plan_key))
            elif kind == "mva":
                history.update(("mva", year) + t for t in parse_wl_mva_history(text_of(page)))
        snapshot = (set(current), history)
        if reference is None:
            reference = snapshot
        same_now = snapshot[0] == reference[0]
        same_hist = snapshot[1] == reference[1]
        print(
            "  %-5s %-12s period %s to %s, current rates %s, history (%d points) %s the reference channel"
            % (ch["chid"], ch.get("name", ""), start, end, "match" if same_now else "DIFFER from", len(history), "matches" if same_hist else "DIFFERS from")
        )
        if not same_hist:
            diff = sorted(snapshot[1] ^ reference[1])
            print("        e.g. %s" % (diff[:3],))
    return 0



# ----------------------------------------------------------- pages / docs ---


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
    parser.add_argument("--channels", action="store_true", help="compare the current rate on the other channels")
    parser.add_argument("--only", help="collect a single product id")
    args = parser.parse_args()

    files = sorted((common.DATA / "products").glob("sml-*.json"))
    manifest_path = SRC / "docs" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    problems = 0
    for file in files:
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        print("%s  %s" % (product["id"], product["name_ja"]))
        snapshot_pages(product)
        kind = product.get("rate_site", {}).get("kind")
        collectors = {None: (collect_rates, compare_channels), "index_linked": (collect_index, compare_channels_index), "whole_life": (collect_wl, compare_channels_wl)}
        collect, compare = collectors[kind]
        problems += collect(product)
        if args.channels:
            problems += compare(product)
        if args.docs:
            download_documents(product, manifest)
    if args.docs:
        write_manifest(manifest_path, manifest)
    if problems:
        print("%d conflicts or mismatches: see above." % problems)
        sys.exit(1)


if __name__ == "__main__":
    main()
