"""Offline check: the collectors' own parsers, fed the saved snapshots, reproduce the rate CSVs.

No network. For every product handled here, each snapshot named in the product's
rate CSVs is parsed again with the collector's parsing function, and every CSV
row that names that snapshot must come back with the same key (product, metric,
plan, currency, term, age band, sex, date), value and status. Parsed points that
are not on file are listed too (a collector that emits keys the CSVs do not use
would duplicate rows on the next run).

Usage:
    python3 scripts/check_offline_parse.py            # every product handled
    python3 scripts/check_offline_parse.py cal-jibun-nenkin-kaigo-plus

Exit status 1 if any row is not reproduced.
"""

import csv
import json
import re
import sys

import common

KEY = common.RATE_KEY


def load(pid):
    return json.loads((common.DATA / "products" / (pid + ".json")).read_text(encoding="utf-8"))


def csv_rows(product):
    out = []
    for name in dict.fromkeys(s["csv"] for s in product["rate_series"]):
        with (common.DATA / "rates" / name).open(newline="", encoding="utf-8") as fh:
            out += list(csv.DictReader(fh))
    return out


# ---- per-collector adapters: snapshot path -> parsed rows (dicts with KEY, value_pct, status) ----

def parse_cal(product, snapshot, rows_on_file):
    import cal

    page = common.decode((common.ROOT / snapshot).read_bytes())
    sample = rows_on_file[0]
    _, grouped = cal.rows_from_page(product, page, sample["source_url"], sample["retrieved_date"],
                                    common.ROOT / snapshot, sample["via_wayback"] == "true")
    return [r for rows in grouped.values() for r in rows]


def parse_dfl(product, snapshot, rows_on_file):
    import dfl

    page = common.decode((common.ROOT / snapshot).read_bytes())
    out = []
    name = snapshot.rsplit("/", 1)[-1]
    for series in product["rate_series"]:
        if not re.fullmatch(r"history_%s_\d{4}-\d{2}-\d{2}\.html" % re.escape(series["param"]), name):
            continue
        out += dfl.rows_for_series(product, series, page, rows_on_file[0]["source_url"],
                                   rows_on_file[0]["retrieved_date"], common.ROOT / snapshot)
    return out


def parse_msp(product, snapshot, rows_on_file):
    import msp

    page = common.decode((common.ROOT / snapshot).read_bytes())
    effective, _, points = msp.parse_page(product, page)
    sample = rows_on_file[0]
    rows = msp.build_rows(product, effective, points, sample["source_url"], common.ROOT / snapshot,
                          sample["via_wayback"] == "true", sample["retrieved_date"])
    return [r for plan_rows in rows.values() for r in plan_rows]


ADAPTERS = {}
for _file in sorted((common.DATA / "products").glob("*.json")):
    _prefix = _file.stem.split("-", 1)[0]
    if _prefix in ("cal", "dfl", "msp"):
        ADAPTERS[_file.stem] = {"cal": parse_cal, "dfl": parse_dfl, "msp": parse_msp}[_prefix]


def check(pid):
    product = load(pid)
    on_file = csv_rows(product)
    index = {tuple(r[k] for k in KEY): r for r in on_file}
    by_snapshot = {}
    for r in on_file:
        by_snapshot.setdefault(r["snapshot"], []).append(r)
    reproduced, differs, missing, extra = 0, 0, 0, 0
    latest = max(r["effective_date"] for r in on_file)
    latest_rows = sum(1 for r in on_file if r["effective_date"] == latest)
    latest_ok = 0
    for snapshot, rows in sorted(by_snapshot.items()):
        parsed = ADAPTERS[pid](product, snapshot, rows)
        got = {tuple(r[k] for k in KEY): r for r in parsed}
        for r in rows:
            key = tuple(r[k] for k in KEY)
            p = got.get(key)
            if p is None:
                missing += 1
                if missing <= 5:
                    print("  NOT REPRODUCED %s (%s)" % ("|".join(key), snapshot))
            elif (p["value_pct"], p["status"]) != (r["value_pct"], r["status"]):
                differs += 1
                print("  VALUE DIFFERS %s: file %s, parse %s" % ("|".join(key), r["value_pct"], p["value_pct"]))
            else:
                reproduced += 1
                if r["effective_date"] == latest:
                    latest_ok += 1
        for key in got:
            if key not in index:
                extra += 1
                if extra <= 5:
                    print("  PARSED BUT NOT ON FILE %s (%s)" % ("|".join(key), snapshot))
    unlisted = []
    if pid.startswith("dfl-"):
        import dfl

        cache = {}
        for snapshot in by_snapshot:
            m = re.fullmatch(r"history_(.+)_\d{4}-\d{2}-\d{2}\.html", snapshot.rsplit("/", 1)[-1])
            cache[m.group(1)] = ("", common.decode((common.ROOT / snapshot).read_bytes()), snapshot)
        unlisted = dfl.unlisted_columns(product, cache)
        for metric, plan in unlisted:
            print("  COLUMN WITHOUT A SERIES %s %s" % (metric, plan))
    ok = not (differs or missing or extra or unlisted)
    print("%-32s %5d rows on file from %3d snapshots: %5d reproduced, %d differ, %d not reproduced, "
          "%d parsed but not on file; latest period %s: %d/%d  %s"
          % (pid, len(on_file), len(by_snapshot), reproduced, differs, missing, extra, latest, latest_ok,
             latest_rows, "OK" if ok else "FAIL"))
    return ok


def main():
    pids = sys.argv[1:] or sorted(ADAPTERS)
    results = [check(pid) for pid in pids]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
