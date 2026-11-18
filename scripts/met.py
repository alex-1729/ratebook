"""Collector for MetLife Japan (メットライフ生命).

The bank-window rate pages (https://www.metlife.co.jp/lf1/ahp805/08.html and
ahp750/08.html) are a React app. The numbers are not in the HTML: the app
POSTs a small JSON request to

    https://www.metlife.co.jp/annuityproduct/v1/applications/newpolicyinfo

and renders the answer. This collector sends the same public requests (no
cookies, no personal data, generic User-Agent) and parses the JSON:

  - main page request  {"apId": "ahp805", "pageName": "08.html",
                        "tabName": "baseexchangerate"}
      -> lfCreditRate15Days / lfCreditRate: the CURRENT half-month rates
  - history popup request  {..., "popup": "true", "startYear": "<year>",
                            "subPageName": "spwl26_1_creditrate_list.do"}
      -> lfCreditRateList: one record per half-month (or month) since launch
  - agency request  {"apId": ..., "pageName": "agency_search_list.do"}
      -> distributor list (with --agents)

Reads every data/products/met-*.json. The mapping from JSON field names to
the CSV grid is in SPECS below. effective_date is the first day of the
contract-date window the rate applies to.

Usage:
    python3 scripts/met.py                 # current rates and full history
    python3 scripts/met.py --agents        # also print the distributor lists
    python3 scripts/met.py --only met-sunny-garden-prime

Raw responses are saved (volatile timestamp fields removed, otherwise
byte-for-byte) under sources/met/rates/. Re-running is safe: existing data
points are never rewritten, and a changed published value is reported as a
conflict. Edition pages (ahp814, ahp764) are fetched only to check that they
show the same numbers as the main page; they add no rows.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

import common

SRC = common.SOURCES / "met"
API = "https://www.metlife.co.jp/annuityproduct/v1/applications/newpolicyinfo"
PAGE = "https://www.metlife.co.jp/lf1/%s/08.html"
VOLATILE = {"currentDateTime", "currentDate"}


def build_specs_sunny():
    """サニーガーデン プライム (ahp805). Keys are spwl26<course>_<ccy?>_<term><h?>.

    course b = 定期支払コース/任意受取コース, a = 積立金増加コース.
    A trailing h is the 上乗せ rate for large premiums. spwl26c / spwl26c_y is
    the 基準利率 of the 利率更改なし型 (b course only, lifetime, no term).
    """
    specs = []
    popups = {("b", "USD"): "spwl26_1", ("b", "JPY"): "spwl26_2", ("b", "AUD"): "spwl26_3",
              ("a", "USD"): "spwl26_4", ("a", "JPY"): "spwl26_5", ("a", "AUD"): "spwl26_6"}
    plan_of = {"b": "teiki", "a": "zoukai"}
    for course in ("b", "a"):
        for ccy, infix, terms in (("USD", "", (5, 10, 20)), ("JPY", "y_", (5, 10)), ("AUD", "g_", (10,))):
            for boost in ("", "h"):
                plan = plan_of[course] + "_reset" + ("_boost" if boost else "")
                for t in terms:
                    specs.append(dict(metric="crediting_rate", plan=plan, currency=ccy, term=str(t), age="",
                                      key="spwl26%s_%s%d%s" % (course, infix, t, boost),
                                      popup=popups[(course, ccy)]))
    for ccy, key in (("USD", "spwl26c"), ("JPY", "spwl26c_y")):
        specs.append(dict(metric="base_rate", plan="teiki_noreset", currency=ccy, term="", age="",
                          key=key, popup=popups[("b", ccy)]))
    return specs


def build_specs_bwyou():
    """ビー ウィズ ユー プラスⅡ (ahp750): 基準利率 of the 利率変動型 forms."""
    specs = []
    for suffix, band in (("50", "50-59"), ("60", "60-69"), ("70", "70-80"), ("81", "81-90")):
        specs.append(dict(metric="base_rate", plan="usd_no_declaration", currency="USD", term="", age=band,
                          key="spwl25nd_" + suffix, popup="spiswl25"))
    specs.append(dict(metric="base_rate", plan="usd_declaration", currency="USD", term="20", age="0-85",
                      key="spwl16yu", popup="spiswl16_2"))
    # contracts concluded up to 2025-09: monthly windows by contract date. The guarantee period is per window
    # in 'gurantee' (30 years until 2023, 20 years later), so term comes from the record. The API's
    # startYear=2000 answer also carries 2026 windows that the page never shows (its year list ends at 2025)
    # and that contradict the list's own heading, so windows after 2025-09 are skipped and reported.
    specs.append(dict(metric="base_rate", plan="usd_declaration_to_2025_09", currency="USD", term="", age="",
                      key="baseInterestRate", popup="spiswl16", popup_only=True,
                      term_field="gurantee", until="2025-09-30"))
    specs.append(dict(metric="base_rate", plan="jpy_type1", currency="JPY", term="30", age="0-75",
                      key="baseInterestRate", main="spiswl24_30_type1", popup="spiswl24_1"))
    specs.append(dict(metric="base_rate", plan="jpy_type1", currency="JPY", term="15", age="76-85",
                      key="baseInterestRate1", main="spiswl24_15_type1", popup="spiswl24_1"))
    specs.append(dict(metric="base_rate", plan="jpy_type2", currency="JPY", term="30", age="40-75",
                      key="baseInterestRate1", main="spiswl24_30_type2", popup="spiswl24_2"))
    specs.append(dict(metric="base_rate", plan="jpy_type2", currency="JPY", term="15", age="76-85",
                      key="baseInterestRate2", main="spiswl24_15_type2", popup="spiswl24_2"))
    return specs


PRODUCTS = {
    "met-sunny-garden-prime": dict(ap="ahp805", specs=build_specs_sunny, edition_checks=[("ahp814", "main")]),
    "met-be-with-you-plus-2": dict(ap="ahp750", specs=build_specs_bwyou, edition_checks=[("ahp764", "main")]),
}


def post(body):
    """POST a JSON request to the rate API. Returns the parsed answer and its bytes."""
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                API, data=data, headers={"User-Agent": common.USER_AGENT, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
            time.sleep(1.5)
            return json.loads(raw)
        except urllib.error.HTTPError as err:
            if err.code < 500 and attempt >= 2:  # the API sometimes answers 400 once under load
                raise RuntimeError("MetLife rate API refused the request (HTTP %d): %s" % (err.code, body))
            last = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("failed to reach the MetLife rate API: %s" % last)


def post_complete(body, complete, tries=6):
    """POST until the answer passes complete(answer).

    The API now and then answers with whole columns missing from every record
    (seen 2026-11-17 for ahp805 spwl26_2: only spwl26c_y left, twice in a row).
    Such an answer must not be parsed, nor saved over the day's snapshot that
    existing rows cite, so it is requested again after a pause.
    """
    for attempt in range(tries):
        answer = post(body)
        if complete(answer):
            return answer
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("MetLife rate API kept answering with columns missing: %s" % body)


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def save(name, answer):
    body = json.dumps(strip_volatile(answer), ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    return common.save_snapshot(SRC / "rates", name, "json", body)


def request_label(body):
    return "%s POST %s" % (API, json.dumps(body, ensure_ascii=False, sort_keys=True))


def current_records(answer):
    """The main page answer -> (start_date, record). Merges lfCreditRate and lfCreditRate15Days."""
    rec = {}
    for k in ("lfCreditRate", "lfCreditRate15Days"):
        rec.update(answer.get(k) or {})
    if not rec.get("date1Year"):
        raise ValueError("main page answer has no 適用期間")
    start = "%s-%02d-%02d" % (rec["date1Year"], int(rec["date1Month"]), int(rec["date1Day"]))
    return start, rec


def popup_years(ap, sub, keys):
    """Ask with an old start year to learn the first year with data, then request every year up to now.

    Every record of a complete answer carries every key in keys (blank when not offered)."""
    def complete(answer):
        return all(set(keys) <= set(r) for r in answer.get("lfCreditRateList") or [])
    base = {"apId": ap, "pageName": "08.html", "tabName": "baseexchangerate", "popup": "true",
            "subPageName": sub + "_creditrate_list.do"}
    first = dict(base, startYear="2000")
    probe = post_complete(first, complete)
    lo, hi = int(probe.get("startYear") or 2000), int(probe.get("endYear") or int(common.today()[:4]))
    hi = max(hi, int(common.today()[:4]))
    results = [(first, probe)]
    for year in range(lo, hi + 1):
        body = dict(base, startYear=str(year))
        results.append((body, post_complete(body, complete)))
    return results


def period_start(date1):
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", date1.strip())
    if not m:
        raise ValueError("unexpected period %r" % date1)
    return "%s-%s-%s" % m.groups()


def make_row(product, spec, date, value, source_url, snapshot):
    value = (value or "").strip()
    offered = value not in ("", "-")
    if offered and not re.fullmatch(r"\d+(\.\d+)?", value):
        raise ValueError("odd value %r for %s" % (value, spec["key"]))
    return {
        "product_id": product["id"], "metric": spec["metric"], "plan": spec["plan"], "currency": spec["currency"],
        "term_years": spec["term"], "age_band": spec["age"], "sex": "", "effective_date": date,
        "value_pct": value if offered else "", "status": "published" if offered else "not_offered",
        "source_url": source_url, "retrieved_date": common.today(), "snapshot": common.rel(snapshot),
        "via_wayback": "false",
    }


def csv_for(product, spec):
    for s in product["rate_series"]:
        if s["metric"] == spec["metric"] and s["plan"] == spec["plan"] and spec["currency"] in s["currencies"]:
            return s["csv"]
    raise ValueError("no rate_series for %s/%s/%s" % (spec["metric"], spec["plan"], spec["currency"]))


def merge(grouped):
    added_total, conflicts = 0, 0
    for name, rows in sorted(grouped.items()):
        added, bad = common.merge_rates(common.DATA / "rates" / name, rows)
        added_total += added
        conflicts += len(bad)
        for key, old, new in bad:
            print("    CONFLICT %s: on file %s, source shows %s" % (" ".join(k for k in key if k), old, new))
    return added_total, conflicts


def collect_product(product, cfg):
    ap, specs = cfg["ap"], cfg["specs"]()
    grouped, read = {}, 0

    def add(spec, date, value, url, snap):
        nonlocal read
        grouped.setdefault(csv_for(product, spec), []).append(make_row(product, spec, date, value, url, snap))
        read += 1

    # current half-month window
    body = {"apId": ap, "pageName": "08.html", "tabName": "baseexchangerate"}
    main_keys = [spec.get("main", spec["key"]) for spec in specs if not spec.get("popup_only")]
    answer = post_complete(body, lambda a: all(k in current_records(a)[1] for k in main_keys))
    snap = save("%s_main" % ap, answer)
    date, rec = current_records(answer)
    n_cur = 0
    for spec in specs:
        if spec.get("popup_only"):
            continue
        key = spec.get("main", spec["key"])
        if key in rec:
            add(spec, date, rec[key], request_label(body), snap)
            n_cur += 1
    print("  current window from %s: %d points" % (date, n_cur))

    # history
    by_popup = {}
    for spec in specs:
        by_popup.setdefault(spec["popup"], []).append(spec)
    for sub, sub_specs in by_popup.items():
        seen = {}
        for body, answer in popup_years(ap, sub, [spec["key"] for spec in sub_specs]):
            snap = save("%s_%s_%s" % (ap, sub, body["startYear"]), answer)
            for record in answer.get("lfCreditRateList") or []:
                period = period_start(record["date1"])
                if period in seen and seen[period][0] != record:
                    # the same window reported twice with different content: surface it, never pick one
                    raise ValueError("%s %s: window %s differs between yearly requests" % (ap, sub, period))
                seen.setdefault(period, (record, body, snap))
        count, skipped = 0, 0
        for period, (record, body, snap) in sorted(seen.items()):
            for spec in sub_specs:
                if spec["key"] in record:
                    if spec.get("until") and period > spec["until"]:
                        skipped += 1
                        continue
                    if spec.get("term_field"):
                        term = (record.get(spec["term_field"]) or "").strip()
                        if not term.isdigit():
                            raise ValueError("%s %s: no guarantee period for %s" % (ap, sub, period))
                        spec = dict(spec, term=term)
                    add(spec, period, record[spec["key"]], request_label(body), snap)
                    count += 1
        if skipped:
            print("  history %-11s skipped %d windows after the series' last contract date" % (sub, skipped))
        print("  history %-11s %3d windows, %4d points" % (sub, len(seen), count))
    added, conflicts = merge(grouped)
    print("  %d points read, %d new" % (read, added))
    return conflicts, answer


def check_editions(product, cfg):
    """An edition page must show the same current numbers as the main page wherever both show one."""
    base_key = cfg["ap"]
    base = current_records(post({"apId": base_key, "pageName": "08.html", "tabName": "baseexchangerate"}))[1]
    for ap, _ in cfg["edition_checks"]:
        other = current_records(post({"apId": ap, "pageName": "08.html", "tabName": "baseexchangerate"}))[1]
        shared = [k for k in base if k in other and k not in VOLATILE and not k.startswith("date")
                  and base[k] not in ("", "-") and other[k] not in ("", "-")]
        diff = [k for k in shared if base[k] != other[k]]
        only_base = [k for k in base if k in other and base[k] not in ("", "-") and other[k] in ("", "-")
                     and not k.startswith("date") and k not in VOLATILE]
        print("  edition %s: %d shared values, %d differ%s%s" % (
            ap, len(shared), len(diff), " (WARNING " + ",".join(diff) + ")" if diff else "",
            "; blank there but published on %s: %s" % (base_key, ",".join(only_base)) if only_base else ""))


def print_agents(cfg_ap, label):
    answer = post({"apId": cfg_ap, "pageName": "agency_search_list.do"})
    names = [a["bankName"] for a in (answer.get("agencies") or {}).get("agenciesList", [])]
    print("  %s %s: %d agencies: %s" % (label, cfg_ap, len(names), "、".join(names)))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agents", action="store_true", help="print the agency lists of the tracked pages")
    parser.add_argument("--only", help="collect a single product id")
    args = parser.parse_args()

    # the product list page (static fragment): which names share which rate page
    try:
        body = common.fetch("https://www.metlife.co.jp/lf1/item/plf1/cr_LIST.htm")
        print("rate list fragment %s" % common.rel(common.save_snapshot(SRC / "pages", "cr_LIST", "html", body)))
    except Exception as err:  # the fragment is only evidence for the edition list
        print("rate list fragment not saved: %s" % err)

    conflicts = 0
    for file in sorted((common.DATA / "products").glob("met-*.json")):
        product = json.loads(file.read_text(encoding="utf-8"))
        if args.only and product["id"] != args.only:
            continue
        cfg = PRODUCTS[product["id"]]
        print("%s  %s" % (product["id"], product["name_ja"]))
        c, _ = collect_product(product, cfg)
        conflicts += c
        check_editions(product, cfg)
        if args.agents:
            print_agents(cfg["ap"], "agents")
            for ap, _ in cfg["edition_checks"]:
                print_agents(ap, "agents")
    if conflicts:
        print("%d conflicts: the source shows a different value from the one on file." % conflicts)
        sys.exit(1)


if __name__ == "__main__":
    main()
