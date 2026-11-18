"""Save raw copies of the location-restricted insurer pages (Nippon Wealth, MS Primary).

These two sites only answer from some networks (e.g. a Japan connection), so
their pages are saved as raw files first and ingested by nwl.py / msp.py.

Run:  python3 scripts/snapshot_jp_rates.py [--out DIR] [--sites nwl,msp] [--no-zip]
Default output folder is ~/jp-rate-snapshots (as when run on another laptop).
Standard library only. Sends only a browser User-Agent, waits 2.5 s between
requests to the same site, and stops on a site after a 403, a block/CAPTCHA
page, or two failed tries (60 s timeout). Files are named with today's date in
JST; a second run on the same JST date overwrites that date's files, manifest
and zip.
"""
import csv, hashlib, os, re, socket, sys, time, zipfile
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urldefrag

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
JST = timezone(timedelta(hours=9))
DATE = datetime.now(JST).strftime("%Y-%m-%d")
HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "jp-rate-snapshots")
if "--out" in sys.argv:
    ROOT = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
SITES = sys.argv[sys.argv.index("--sites") + 1].split(",") if "--sites" in sys.argv else ["nwl", "msp"]
MAKE_ZIP = "--no-zip" not in sys.argv
TIMEOUT = 60
DELAY = 2.5
BLOCK_MARKERS = ["captcha", "recaptcha", "hcaptcha", "cf-chl", "incapsula incident", "request unsuccessful", "access denied",
                 "are you a robot", "unusual traffic", "bot detection", "akamai reference"]

NWL = "https://www.nw-life.co.jp"
nwl_pages = [
    (NWL + "/product/annuities/rate/gu_int_rate.html", "gu_int_rate"),
    (NWL + "/product/annuities/rate/gu_int_rate_base.html", "gu_int_rate_base"),
    (NWL + "/product/annuities/rate/gu_int_rate5_base.html", "gu_int_rate5_base"),
    (NWL + "/product/annuities/rate/gu_int_rate12_base.html", "gu_int_rate12_base"),
    (NWL + "/product/annuities/rate/gu_int_rate13_base.html", "gu_int_rate13_base"),
    (NWL + "/product/annuities/rate/gu_int_rate15_base.html", "gu_int_rate15_base"),
    (NWL + "/product/individual/annuities/yuyu_advance2/", "yuyu_advance2_product"),
    (NWL + "/product/individual/annuities/yuyu_advance2/first.html", "yuyu_advance2_first"),
]
nwl_fallback = [NWL + "/product/pamphlet/yuyu_advance2.pdf",
                NWL + "/product/pamphlet/yuyu_advance2-02.pdf"]
msp_pages = [
    ("https://msp.ms-primary.com/g/RateList.do?product_id=67", "RateList_product_id_67"),
    ("https://msp.ms-primary.com/g/RateList.do?product_id=51", "RateList_product_id_51"),
    ("https://msp.ms-primary.com/g/RateList.do?product_id=61", "RateList_product_id_61"),
    ("https://msp.ms-primary.com/g/Rate.do", "Rate"),
]

rows = []
last_req = {}
stopped = {}
saved_html = {}


def fetch(url):
    """returns (status, body, note, fatal)"""
    host = urlparse(url).netloc
    note = ""
    for attempt in (1, 2):
        wait = DELAY - (time.time() - last_req.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            last_req[host] = time.time()
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
                last_req[host] = time.time()
                final = r.geturl()
                n = "" if final == url else "redirected to " + final
                return r.status, body, n, False, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            last_req[host] = time.time()
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            return e.code, body, "HTTP error", e.code == 403, e.headers.get("Content-Type", "") if e.headers else ""
        except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
            last_req[host] = time.time()
            note = "attempt %d failed: %r" % (attempt, e)
            print("  ", note, flush=True)
    return "", b"", "network failure/timeout after two tries: " + note, True, ""


def looks_blocked(body):
    low = body[:200000].lower()
    hits = [m for m in BLOCK_MARKERS if m.encode() in low]
    if len(body) < 3000 and b"_incapsula_resource" in low:
        hits.append("incapsula challenge stub")
    return hits


def record(url, relpath, status, body, note):
    rows.append({
        "url": url, "file": relpath, "http_status": status,
        "bytes": len(body) if body else 0,
        "sha256": hashlib.sha256(body).hexdigest() if body else "",
        "retrieved_at": datetime.now(JST).isoformat(timespec="seconds"),
        "note": note,
    })
    print("%s | %s | %s | %d bytes | %s" % (url, relpath or "-", status, len(body or b""), note), flush=True)


def get(url, sub, fname, kind):
    site = urlparse(url).netloc
    if site in stopped:
        record(url, "", "", b"", "not requested: site stopped (" + stopped[site] + ")")
        return None
    status, body, note, fatal, ctype = fetch(url)
    if fatal:
        stopped[site] = "403" if status == 403 else "timeout/network failure"
        record(url, "", status, b"", note + "; stopping this site")
        return None
    if status != 200:
        record(url, "", status, body, note + "; not saved")
        return None
    if kind == "pdf":
        if not body.startswith(b"%PDF"):
            hits = looks_blocked(body)
            if hits:
                stopped[site] = "block page"
            record(url, "", status, body, "body does not start with %PDF; not kept" + (" block markers %s" % hits if hits else ""))
            return None
    else:
        hits = looks_blocked(body)
        if hits:
            # record but flag; saved for inspection, site stopped
            note = (note + "; " if note else "") + "possible block/CAPTCHA markers: %s" % hits
            stopped[site] = "possible block page at " + url
    d = os.path.join(ROOT, sub)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, fname)
    with open(path, "wb") as f:
        f.write(body)
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    record(url, rel, status, body, (note + "; " if note else "") + "content-type: " + ctype)
    return body, ctype, rel


def decode(body, ctype=""):
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I) or \
        re.search(rb"charset=[\"']?([\w-]+)", body[:4000], re.I)
    encs = []
    if m:
        g = m.group(1)
        encs.append(g.decode() if isinstance(g, bytes) else g)
    encs += ["utf-8", "cp932", "euc_jp"]
    for e in encs:
        try:
            return body.decode(e)
        except Exception:
            continue
    return body.decode("utf-8", "replace")


os.makedirs(ROOT, exist_ok=True)

# NWL pages
pdf_links = []  # (url, found_on)
for url, short in (nwl_pages if "nwl" in SITES else []):
    res = get(url, "nwl", "%s_%s.html" % (short, DATE), "html")
    if res:
        saved_html[url] = res
        if "yuyu_advance2" in short:
            text = decode(res[0], res[1])
            for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", text, re.I):
                full = urldefrag(urljoin(url, href.strip()))[0]
                if urlparse(full).path.lower().endswith(".pdf"):
                    if full not in [p[0] for p in pdf_links]:
                        pdf_links.append((full, short))

print("\nPDF links found on product pages:")
for u, src in pdf_links:
    print("  ", u, "(on %s)" % src)

targets = [u for u, _ in pdf_links]
for fb in (nwl_fallback if "nwl" in SITES else []):
    if fb not in targets:
        targets.append(fb)
        print("   fallback added:", fb)

for u in targets:
    host = urlparse(u).netloc
    if host != "www.nw-life.co.jp":
        record(u, "", "", b"", "not requested: PDF link on a different host than the listed site")
        continue
    name = os.path.basename(urlparse(u).path)
    base, ext = os.path.splitext(name)
    get(u, "nwl", "%s_%s.pdf" % (base, DATE), "pdf")

# MSP
for url, short in (msp_pages if "msp" in SITES else []):
    res = get(url, "msp", "%s_%s.html" % (short, DATE), "html")
    if res:
        saved_html[url] = res

with open(os.path.join(ROOT, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["url", "file", "http_status", "bytes", "sha256", "retrieved_at", "note"])
    w.writeheader()
    w.writerows(rows)

# Checks
def lines_with(text, kw, limit=6):
    out = []
    for line in text.splitlines():
        if kw in line:
            s = re.sub(r"<[^>]+>", " ", line)
            s = re.sub(r"\s+", " ", s).strip()
            if len(s) > 400:
                i = s.find(kw)
                s = "..." + s[max(0, i - 150): i + 250] + "..."
            out.append(s)
            if len(out) >= limit:
                break
    return out

print("\n==== CHECKS ====")
for url, (body, ctype, rel) in saved_html.items():
    text = decode(body, ctype)
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    pct = len(re.findall(r"\d+\.\d+\s*[%％]", plain))
    print("\n--", rel, "| title:", (title.group(1).strip() if title else None),
          "| numeric %% values: %d | iframes: %s" % (pct, re.findall(r"<iframe[^>]+src=[\"']([^\"']+)", text, re.I)))
    for kw in ("適用期間", "契約日", "MARE"):
        for l in lines_with(text, kw):
            print("   [%s] %s" % (kw, l))

if MAKE_ZIP:
    zpath = os.path.join(HOME, "jp-rate-snapshots_%s.zip" % DATE)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(ROOT):
            for name in sorted(fn):
                p = os.path.join(dp, name)
                z.write(p, os.path.join("jp-rate-snapshots", os.path.relpath(p, ROOT)))
    print("\nZIP:", zpath, os.path.getsize(zpath), "bytes")
print("Stopped sites:", stopped)
