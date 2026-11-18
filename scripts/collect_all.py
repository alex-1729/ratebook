"""Collect rates for every in-scope product, one insurer after another.

Usage:
    python3 scripts/collect_all.py                 # rates for all insurers
    python3 scripts/collect_all.py --docs          # also refresh documents
    python3 scripts/collect_all.py --only dfl,tdf  # some insurers only
    python3 scripts/collect_all.py --list          # print the insurers as JSON and exit

Dai-ichi Frontier, Sumitomo Life, T&D Financial, Nippon Life, Meiji Yasuda,
ORIX Life, Credit Agricole Life and MetLife are collected directly.
Mitsui Sumitomo Primary and Nippon Wealth only answer from some networks
(for example a Japan connection): they are probed first and reported as
"blocked" when this network cannot reach them. PGF Life and Manulife load
their rate tables by script inside the browser, so their tables were read in a
browser and saved as page copies under sources/; their collectors are run with
--no-live, re-read those copies and make no request to the insurers' sites, so
a run adds nothing until a newer copy is saved. Nothing is worked around.

Output is plain text, plus one machine-readable line per state change:
    @@status {"insurer": "dfl", "state": "running" | "ok" | "failed" | "blocked", "message": "..."}
Exit code: 0 when every selected insurer is ok, 2 when any is blocked or failed.
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import common

SCRIPTS = Path(__file__).resolve().parent

# insurer id -> how it is collected
INSURERS = {
    "dfl": {"kind": "direct", "script": "dfl.py"},
    "sml": {"kind": "direct", "script": "sml.py"},
    "tdf": {"kind": "direct", "script": "tdf.py"},
    "msp": {"kind": "restricted", "probe": "https://msp.ms-primary.com/g/RateList.do?product_id=67"},
    "nwl": {"kind": "restricted", "probe": "https://www.nw-life.co.jp/product/annuities/rate/gu_int_rate.html"},
    "nli": {"kind": "direct", "script": "nli.py"},
    "mys": {"kind": "direct", "script": "mys.py"},
    "orx": {"kind": "direct", "script": "orx.py"},
    "cal": {"kind": "direct", "script": "cal.py"},
    "met": {"kind": "direct", "script": "met.py", "own_docs": False},
    "pgf": {"kind": "saved", "script": "pgf.py"},
    "mnl": {"kind": "saved", "script": "mnl.py"},
}

# Mitsui Sumitomo Primary: rate page id -> product collected from it
MSP_RATE_PAGES = {
    "67": "msp-mare-3",
    "51": "msp-broadway-world-3",
    "61": "msp-ashitano-yorokobi-2",
}

SAVED_ONLY = ("Saved copies re-read; no request was made to the insurer's site. "
              "New rates appear only after a newer page copy is saved under sources/.")


def insurer_name(insurer):
    path = common.DATA / "insurers" / (insurer + ".json")
    if path.exists():
        info = json.loads(path.read_text(encoding="utf-8"))
        return info.get("name_en") or info.get("name_ja") or insurer
    return insurer


def status(insurer, state, message=""):
    print("@@status " + json.dumps({"insurer": insurer, "state": state, "message": message}, ensure_ascii=False), flush=True)


def run(args):
    """Run a collector script, echoing its output. Returns the exit code."""
    cmd = [sys.executable, "-u"] + [str(a) for a in args]
    proc = subprocess.Popen(cmd, cwd=str(common.ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in proc.stdout:
        print(raw.decode("utf-8", "replace").rstrip("\n"), flush=True)
    return proc.wait()


def reachable(url):
    """One quick request. Returns (True, '') or (False, reason)."""
    req = urllib.request.Request(url, headers={"User-Agent": common.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read(2048)
        return True, ""
    except urllib.error.HTTPError as err:
        return False, "the site answered HTTP %d" % err.code
    except Exception as err:  # timeouts, DNS, connection resets
        return False, "no answer (%s)" % type(err).__name__


def collect_direct(insurer, docs):
    info = INSURERS[insurer]
    args = [SCRIPTS / info["script"]]
    own_docs = info.get("own_docs", True)
    if info["kind"] == "saved":
        args.append("--no-live")  # re-read saved copies only: no request to the site
    elif docs and own_docs:
        args.append("--docs")
    code = run(args)
    if code == 0 and docs and not own_docs:
        code = run([SCRIPTS / "fetch_docs.py", "--insurers", insurer])
    if code == 0:
        status(insurer, "ok", SAVED_ONLY if info["kind"] == "saved" else "")
    else:
        status(insurer, "failed", "collector exited with code %d (see the log)" % code)
    return code == 0


def collect_restricted(insurer):
    ok, reason = reachable(INSURERS[insurer]["probe"])
    if not ok:
        status(insurer, "blocked", "Not reachable from this network: %s. Connect through a Japan connection and run again." % reason)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        code = run([SCRIPTS / "snapshot_jp_rates.py", "--out", tmp, "--sites", insurer, "--no-zip"])
        manifest = Path(tmp) / "manifest.csv"
        saved = []
        if manifest.exists():
            with manifest.open(newline="", encoding="utf-8") as fh:
                saved = [r for r in csv.DictReader(fh) if r["file"]]
        if code != 0 or not saved:
            status(insurer, "failed", "could not save the insurer's pages (see the log)")
            return False
        # The snapshot script names files with the date in Japan time.
        date = saved[0]["file"].rsplit("_", 1)[1].split(".")[0]
        if insurer == "msp":
            pages = {}
            for row in saved:
                for page_id in MSP_RATE_PAGES:
                    if "RateList_product_id_%s_" % page_id in row["file"]:
                        pages[page_id] = row["file"]
            if not pages:
                status(insurer, "failed", "no rate page was saved")
                return False
            code = 0
            for page_id, page in sorted(pages.items()):
                step = run([SCRIPTS / "msp.py", "--only", MSP_RATE_PAGES[page_id], "--file", Path(tmp) / page, "--retrieved", date])
                code = code or step
        else:
            code = run([SCRIPTS / "nwl.py", "--live-dir", Path(tmp) / "nwl", "--retrieved", date])
    if code == 0:
        status(insurer, "ok")
    else:
        status(insurer, "failed", "ingest exited with code %d (see the log)" % code)
    return code == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--docs", action="store_true", help="also refresh the product documents (direct insurers; not PGF Life or Manulife)")
    parser.add_argument("--only", help="comma-separated insurer ids, e.g. dfl,tdf")
    parser.add_argument("--list", action="store_true", help="print the insurers as JSON and exit")
    args = parser.parse_args()

    if args.list:
        print(json.dumps([{"id": i, "name": insurer_name(i), "kind": INSURERS[i]["kind"]} for i in INSURERS], ensure_ascii=False))
        return
    selected = args.only.split(",") if args.only else list(INSURERS)
    unknown = [i for i in selected if i not in INSURERS]
    if unknown:
        parser.error("unknown insurer id: %s" % ", ".join(unknown))

    all_ok = True
    for insurer in selected:
        print("\n=== %s (%s) ===" % (insurer_name(insurer), insurer), flush=True)
        status(insurer, "running")
        try:
            if INSURERS[insurer]["kind"] in ("direct", "saved"):
                ok = collect_direct(insurer, args.docs)
            else:
                ok = collect_restricted(insurer)
        except Exception as err:  # keep going with the other insurers
            status(insurer, "failed", "%s: %s" % (type(err).__name__, err))
            ok = False
        all_ok = all_ok and ok
    sys.exit(0 if all_ok else 2)


if __name__ == "__main__":
    main()
