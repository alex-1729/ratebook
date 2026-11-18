"""Shared helpers for the rate collectors.

Standard library only, so the scripts run unchanged on macOS and Windows.

Privacy rule: outbound requests carry a generic browser User-Agent and
nothing else. No cookies, names, emails or contact details are sent.
"""

import csv
import datetime as dt
import hashlib
import html
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
DATA = ROOT / "data"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

RATE_COLUMNS = [
    "product_id",
    "metric",
    "plan",
    "currency",
    "term_years",
    "age_band",
    "sex",
    "effective_date",
    "value_pct",
    "status",
    "source_url",
    "retrieved_date",
    "snapshot",
    "via_wayback",
]
# Everything except the value and provenance identifies one data point.
RATE_KEY = RATE_COLUMNS[:8]

CURRENCIES = {"米ドル": "USD", "豪ドル": "AUD", "円貨": "JPY", "円": "JPY"}


def today():
    return dt.date.today().isoformat()


def fetch(url, retries=4, timeout=40, pause=1.0):
    """GET a URL and return the body bytes. Retries on timeouts and 5xx."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            time.sleep(pause)  # be polite to the insurer's server
            return body
        except urllib.error.HTTPError as err:
            if err.code < 500:
                raise
            last = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def save_snapshot(folder, name, ext, body, date=None):
    """Save raw bytes as <folder>/<name>_<date>.<ext> and return the path.

    If the newest existing snapshot of the same name has identical content,
    nothing is written and that earlier file is returned, so unchanged pages
    do not pile up.
    """
    date = date or today()
    folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(re.escape(name) + r"_\d{4}-\d{2}-\d{2}\." + re.escape(ext) + "$")
    earlier = sorted(p for p in folder.iterdir() if pattern.match(p.name))
    if earlier and sha256(earlier[-1].read_bytes()) == sha256(body):
        return earlier[-1]
    path = folder / ("%s_%s.%s" % (name, date, ext))
    path.write_bytes(body)
    return path


def rel(path):
    return path.relative_to(ROOT).as_posix()


def decode(body):
    for enc in ("utf-8", "shift_jis", "euc_jp"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def clean(fragment):
    """Strip tags and collapse whitespace in an HTML fragment."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def table_rows(page):
    """Return every <tr> as a list of (text, colspan, rowspan) cells."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", page):
        cells = []
        for attrs, inner in re.findall(r"<t[dh]([^>]*)>([\s\S]*?)</t[dh]>", tr):
            col = re.search(r'colspan="?(\d+)', attrs)
            row = re.search(r'rowspan="?(\d+)', attrs)
            cells.append((clean(inner), int(col.group(1)) if col else 1, int(row.group(1)) if row else 1))
        if cells:
            rows.append(cells)
    return rows


def expand_header(header_rows):
    """Expand colspans and rowspans into a grid: one list of labels per column."""
    grid = []  # grid[r][c]
    pending = {}  # column -> (rows_left, text) from a rowspan above
    for cells in header_rows:
        line = []
        queue = list(cells)
        col = 0
        while queue or col in pending:
            if col in pending:
                left, text = pending[col]
                line.append(text)
                if left > 1:
                    pending[col] = (left - 1, text)
                else:
                    del pending[col]
                col += 1
                continue
            text, colspan, rowspan = queue.pop(0)
            for _ in range(colspan):
                line.append(text)
                if rowspan > 1:
                    pending[col] = (rowspan - 1, text)
                col += 1
        grid.append(line)
    width = max(len(line) for line in grid)
    return [[line[c] if c < len(line) else "" for line in grid] for c in range(width)]


def column_dims(labels):
    """Turn one column's header labels into currency / term / age band."""
    dims = {"currency": "", "term_years": "", "age_band": ""}
    for label in labels:
        if label in CURRENCIES:
            dims["currency"] = CURRENCIES[label]
        elif "歳" in label:
            dims["age_band"] = label.replace("歳", "").replace("～", "-")
        elif re.fullmatch(r"\d+年", label):
            dims["term_years"] = label[:-1]
    return dims


def parse_history_table(page):
    """Parse a 'rates since launch' table into (effective_date, dims, text) tuples.

    The table has header rows (currency, then term or age band, optionally a
    guarantee period) followed by one row per effective date (yy/mm/dd).
    """
    rows = table_rows(page)
    is_date = lambda cells: re.fullmatch(r"\d{2}/\d{2}/\d{2}", cells[0][0]) is not None
    first = next(i for i, cells in enumerate(rows) if is_date(cells))
    start = first
    while start > 0 and not is_date(rows[start - 1]) and len(rows[start - 1]) > 1:
        start -= 1
    columns = [column_dims(labels) for labels in expand_header(rows[start:first])][1:]
    out = []
    for cells in rows[first:]:
        if not is_date(cells):
            continue
        values = [c[0] for c in cells[1:]]
        if len(values) != len(columns):
            raise ValueError("row %s has %d values for %d columns" % (cells[0][0], len(values), len(columns)))
        yy, mm, dd = cells[0][0].split("/")
        date = "20%s-%s-%s" % (yy, mm, dd)
        for dims, value in zip(columns, values):
            if not dims["currency"]:
                raise ValueError("column without a currency in header")
            out.append((date, dims, value))
    return out


def merge_rates(csv_path, new_rows):
    """Add new data points to a rate CSV. Existing points are never changed.

    Returns (added, conflicts) where conflicts lists points whose published
    value differs from the one already on file.
    """
    existing = []
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    index = {tuple(r[k] for k in RATE_KEY): r for r in existing}
    added, conflicts = 0, []
    for row in new_rows:
        key = tuple(row[k] for k in RATE_KEY)
        old = index.get(key)
        if old is None:
            index[key] = row
            existing.append(row)
            added += 1
        elif (old["value_pct"], old["status"]) != (row["value_pct"], row["status"]):
            conflicts.append((key, old["value_pct"] or old["status"], row["value_pct"] or row["status"]))
    existing.sort(key=lambda r: (r["effective_date"], r["currency"], r["age_band"], int(r["term_years"] or 0), r["sex"]))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RATE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(existing)
    return added, conflicts
