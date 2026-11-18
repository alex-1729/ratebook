"""Collector for insurer news: press releases, notices, regulator and association pages.

Usage:
    python3 scripts/news.py collect                    # every source, current list pages
    python3 scripts/news.py collect --only dfl,fsa     # some owners only
    python3 scripts/news.py collect --since 2024-01-01 # also walk the year archives back to that date
    python3 scripts/news.py collect --enrich           # then add English titles and summaries (news_enrich.py)
    python3 scripts/news.py probe URL                  # print what the generic reader finds on a list page
    python3 scripts/news.py add FILE.json --to press   # add researched items (press coverage, one-off finds)
    python3 scripts/news.py status                     # when each source was last checked
    python3 scripts/news.py --list                     # the owners as JSON (used by the app)

Where things live
    data/insurers/<id>.json   "news_tier" (full | watch | market) and "news_sources": the list pages to read
    data/news/sources/<id>.json  owners that are not insurers (regulator, association, parent groups):
                              {"id", "name_en", "name_ja", "insurer_ids": [...], "news_sources": [...]}
    data/news/items/*.jsonl   one item per line; <owner>.jsonl is written by `collect`, others by `add`
    data/news/state.json      last check per source, so a quiet feed can be told from a broken one
    data/news/stories/*.json  named threads (for example a scandal) that items point at with "story_ids"
    sources/<owner>/news/     the list pages as fetched (saved when they bring a new item)

A source is {"key", "label", "kind", "url"} plus, when needed:
    "archive_url"  a list page per year, with {year} (calendar) or {fy} (April-March fiscal year)
    "page_url"     further pages of one list, with {page}; "first_page" (default 2), "max_pages" (default 5;
                   with --since up to "max_pages_backfill", default 60, stopping at the first page that is too old)
    "parser"       "auto" (default), "rss", or "<module>:<function>" in scripts/news_sites/
    "include" / "exclude"   regular expressions on the item URL
    "encoding"     when the page does not decode as UTF-8, Shift_JIS or EUC-JP

Optional fields on an item: "duplicate_of" (the id of the same release collected from another site; the app
shows it once), "also_at" (other addresses of the same document), "verified" (an independent re-check).

Rules (as for rates): an item on file is never rewritten by `collect`. If a page later shows another title or
date for the same URL, that is reported as a CONFLICT. Only the title, date and link are stored, never the text
of a release or article.

Privacy rule: outbound requests carry a generic browser User-Agent and nothing else (see common.py).

Output is plain text plus one machine-readable line per state change, as collect_all.py prints:
    @@status {"insurer": "dfl", "state": "running" | "ok" | "failed" | "blocked", "message": "..."}
Exit code: 0 when every selected owner is ok, 2 when any is blocked or failed.
"""

import argparse
import datetime as dt
import hashlib
import importlib
import json
import re
import sys
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import common

NEWS = common.DATA / "news"
ITEMS = NEWS / "items"
STATE = NEWS / "state.json"
EXTRA_SOURCES = NEWS / "sources"

# Items dated before this are never stored, however far back a list page reaches (--since can only raise it).
FLOOR = dt.date(2024, 1, 1)

TIERS = ("full", "watch", "market")
KINDS = ("official", "parent", "regulator", "association", "press")
CATEGORIES = (
    "product_launch",      # a new product or edition goes on sale
    "product_change",      # revision of terms, limits, currencies of an existing product
    "sales_suspension",    # sales stopped, paused or restarted
    "rates",               # crediting / assumed rate announcements
    "regulatory_conduct",  # administrative action, misconduct, investigations, compliance failures
    "regulation",          # rules and policy for the whole market: laws, supervisory guidelines, councils, surveys
    "distribution",        # new or changed distributors, bank and agency tie-ups
    "results",             # financial results, disclosures, ratings
    "corporate",           # M&A, capital, organisation, personnel
    "customer_notice",     # maintenance, disaster relief, phishing warnings, procedures
    "other",               # awards, sponsorship, CSR and the rest
)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 2026年11月5日 / 2026.11.05 / 2026/11/5 / 2026-11-05 (full-width digits are normalised first)
DATE_IN_TEXT = re.compile(r"(?<!\d)(20\d{2})\s*(?:年|[./-])\s*(\d{1,2})\s*(?:月|[./-])\s*(\d{1,2})\s*日?(?![\d.])")
ANCHOR = re.compile(r"<a\b([^>]*)>([\s\S]*?)</a>", re.I)
HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
FILE_SIZE = re.compile(r"\s*[（(\[]\s*(?:PDF)?\s*[:：]?\s*[\d.,]+\s*(?:KB|MB|ＫＢ|ＭＢ)\s*[）)\]]\s*$", re.I)  # "（209KB）" after a title
CHALLENGE = re.compile(rb"Pardon Our Interruption|_Incapsula_Resource|Incapsula incident|cf-challenge|Just a moment\.\.\.", re.I)
ZEN = str.maketrans("０１２３４５６７８９．／－", "0123456789./-")


# ---------------------------------------------------------------------------------------------- owners
def owners():
    """Every owner of news sources: insurers first (by tier), then data/news/sources/*.json."""
    out = {}
    for path in sorted((common.DATA / "insurers").glob("*.json")):
        info = json.loads(path.read_text(encoding="utf-8"))
        oid = info.get("id") or path.stem
        out[oid] = {
            "id": oid,
            "name": info.get("name_en") or info.get("name_ja") or oid,
            "tier": info.get("news_tier") or "market",
            "insurer_ids": [oid],
            "sources": info.get("news_sources") or [],
            "insurer": True,
        }
    for path in sorted(EXTRA_SOURCES.glob("*.json")) if EXTRA_SOURCES.exists() else []:
        info = json.loads(path.read_text(encoding="utf-8"))
        oid = info.get("id") or path.stem
        out[oid] = {
            "id": oid,
            "name": info.get("name_en") or oid,
            "tier": "",
            "insurer_ids": info.get("insurer_ids") or [],
            "sources": info.get("news_sources") or [],
            "insurer": False,
        }
    return out


def insurer_ids():
    return {p.stem for p in (common.DATA / "insurers").glob("*.json")}


# ---------------------------------------------------------------------------------------------- parsing
def norm_date(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse_auto(page, page_url):
    """Read a list page generically: every date, paired with the link it sits in or the next link after it.

    Returns [{"date", "title_ja", "url"}]. Works for the usual Japanese news list (a date, then a linked
    title, in a <li>, <dl> or table row). A date with no link before the next date is skipped.
    """
    page = re.sub(r"<!--[\s\S]*?-->|<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", page)
    flat = page.translate(ZEN)
    anchors = []
    for m in ANCHOR.finditer(flat):
        h = HREF.search(m.group(1))
        href = next((g for g in h.groups() if g), "") if h else ""
        anchors.append((m.start(), m.end(), href.strip(), m.group(2)))
    dates = [(m.start(), m.end(), norm_date(*m.groups()), m.group(0)) for m in DATE_IN_TEXT.finditer(flat)]
    dates = [d for d in dates if d[2]]
    out, seen = [], set()
    for i, (start, end, date, raw) in enumerate(dates):
        limit = dates[i + 1][0] if i + 1 < len(dates) else len(flat)
        inside = next((a for a in anchors if a[0] < start < a[1]), None)
        if inside:
            # Several dates inside one link: only the first names the item.
            if any(inside[0] < d[0] < start for d in dates):
                continue
            anchor = inside
        else:
            anchor = next((a for a in anchors if end <= a[0] < limit), None)
        if not anchor:
            continue
        href = anchor[2]
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        title = common.clean(anchor[3].replace(raw, " "))
        if not title:
            alt = re.search(r'alt\s*=\s*"([^"]+)"', anchor[3])
            title = common.clean(alt.group(1)) if alt else ""
        if len(title) < 4:
            # The link is an icon or "PDF": the title is the text between the date and the link.
            title = common.clean(flat[end:anchor[0]]) or title
        if len(title) < 4:
            continue
        title = FILE_SIZE.sub("", title)
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, href.replace("&amp;", "&")))[0]
        if url in seen:
            continue
        seen.add(url)
        out.append({"date": date, "title_ja": title[:400], "url": url})
    return out


def parse_rss(body, page_url):
    """RSS 2.0, RSS 1.0 (RDF) and Atom."""
    root = ET.fromstring(body)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    out = []
    for node in root.iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        fields = {}
        for child in node:
            name = local(child.tag)
            if name == "link":
                fields.setdefault("link", (child.text or "").strip() or child.attrib.get("href", ""))
            elif name in ("title", "pubDate", "date", "updated", "published"):
                fields.setdefault(name, (child.text or "").strip())
        when = fields.get("pubDate") or fields.get("date") or fields.get("published") or fields.get("updated") or ""
        date = None
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", when)
        if m:
            date = norm_date(*m.groups())
        else:
            try:
                from email.utils import parsedate_to_datetime
                date = parsedate_to_datetime(when).date().isoformat()
            except (TypeError, ValueError):
                date = None
        if date and fields.get("title") and fields.get("link"):
            out.append({"date": date, "title_ja": common.clean(fields["title"])[:400],
                        "url": urllib.parse.urljoin(page_url, fields["link"])})
    return out


def custom(spec):
    """'<module>:<function>' in scripts/news_sites/."""
    module, _, func = spec.partition(":")
    if not re.fullmatch(r"[a-z0-9_]+", module) or not re.fullmatch(r"[a-z0-9_]+", func or "parse"):
        raise ValueError("bad parser name %r" % spec)
    return getattr(importlib.import_module("news_sites." + module), func or "parse")


def decode(body, source):
    if source.get("encoding"):
        return body.decode(source["encoding"], "replace")
    m = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", body[:3000])
    if m:
        try:
            return body.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    return common.decode(body)


def parse_page(body, source, page_url):
    parser = source.get("parser") or "auto"
    if parser == "auto":
        items = parse_auto(decode(body, source), page_url)
    elif parser == "rss":
        items = parse_rss(body, page_url)
    else:
        items = custom(parser)(body, source, page_url)
    inc = re.compile(source["include"]) if source.get("include") else None
    exc = re.compile(source["exclude"]) if source.get("exclude") else None
    return [i for i in items if (not inc or inc.search(i["url"])) and not (exc and exc.search(i["url"]))]


def fiscal_year(date):
    return date.year if date.month >= 4 else date.year - 1


def page_urls(source, since):
    """(name, url) for every list page of a source to read on this run."""
    today = dt.date.today()
    start = since or (today - dt.timedelta(days=45))  # overlap the turn of a year
    pages = []
    if source.get("url"):
        pages.append(("list", source["url"]))
    tpl = source.get("archive_url")
    if tpl and "{fy}" in tpl:
        for fy in range(fiscal_year(today), fiscal_year(start) - 1, -1):
            pages.append(("fy%d" % fy, tpl.format(fy=fy, fy2="%02d" % (fy % 100), reiwa=fy - 2018)))
    elif tpl:
        for year in range(today.year, start.year - 1, -1):
            pages.append(("y%d" % year, tpl.format(year=year, yy="%02d" % (year % 100), reiwa=year - 2018)))
    if source.get("page_url"):
        first = int(source.get("first_page", 2))
        count = int(source.get("max_pages_backfill", 60)) if since else int(source.get("max_pages", 5))
        for n in range(first, first + count - 1):
            pages.append(("p%d" % n, source["page_url"].format(page=n)))
    seen, out = set(), []
    for name, url in pages:
        if url not in seen:
            seen.add(url)
            out.append((name, url))
    return out


# ---------------------------------------------------------------------------------------------- storage
def item_id(owner, date, url):
    return "%s-%s-%s" % (owner, date, hashlib.sha1(url.encode("utf-8")).hexdigest()[:8])


def read_items(path):
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as err:
                raise ValueError("%s line %d is not JSON: %s" % (common.rel(path), n, err))
    return out


def write_items(path, items):
    items = sorted(items, key=lambda i: (i.get("date", ""), i.get("id", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False, sort_keys=False) + "\n" for i in items), encoding="utf-8")
    tmp.replace(path)


def all_items():
    out = []
    for path in sorted(ITEMS.glob("*.jsonl")) if ITEMS.exists() else []:
        out += read_items(path)
    return out


def url_key(url):
    """Two spellings of one address compare equal (scheme, www, trailing slash)."""
    u = urllib.parse.urlsplit(url.strip())
    host = u.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (u.path.rstrip("/") or "/") + ("?" + u.query if u.query else "")


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}}


def update_state(changes):
    """Merge per-source records into data/news/state.json (re-read first: several runs may overlap)."""
    state = read_state()
    state.setdefault("sources", {}).update(changes)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def title_key(title):
    return re.sub(r"[\s\u3000「」『』（）()・、。,.\-‐－~～:：\"'“”]", "", title or "")[:24]


def mark_duplicates():
    """A parent group's list often repeats a subsidiary's release. Where a group item has the same date and the
    same title as an item from one of that group's insurers, give it "duplicate_of" so it is shown once.
    Returns the number of items newly marked."""
    table = owners()
    own = {}
    for oid, owner in table.items():
        if owner["insurer"]:
            for i in read_items(ITEMS / (oid + ".jsonl")):
                if len(title_key(i.get("title_ja"))) >= 8:
                    own.setdefault((oid, i["date"], title_key(i["title_ja"])), i["id"])
    marked = 0
    for oid, owner in table.items():
        if owner["insurer"] or not owner["insurer_ids"]:
            continue
        path = ITEMS / (oid + ".jsonl")
        items = read_items(path)
        changed = False
        for i in items:
            if i.get("duplicate_of"):
                continue
            for ins in owner["insurer_ids"]:
                hit = own.get((ins, i["date"], title_key(i.get("title_ja"))))
                if hit:
                    i["duplicate_of"] = hit
                    marked += 1
                    changed = True
                    break
        if changed:
            write_items(path, items)
    return marked


# ---------------------------------------------------------------------------------------------- collect
def status(owner, state, message=""):
    print("@@status " + json.dumps({"insurer": owner, "state": state, "message": message}, ensure_ascii=False), flush=True)


def collect_source(owner, source, since, known, files):
    """Read one source. Returns (new_items, conflicts, state_record). `files` maps a page name to a saved copy."""
    key = source["key"]
    kind = source.get("kind") or "official"
    record = {"last_checked": common.today(), "state": "ok", "message": "", "url": source.get("url") or source.get("archive_url") or ""}
    new, conflicts, read, errors = [], [], 0, []
    folder = common.SOURCES / owner["id"] / "news"
    for name, url in page_urls(source, since):
        saved = files.get("%s_%s" % (key, name)) or (files.get(key) if name == "list" else None)
        try:
            body = Path(saved).read_bytes() if saved else common.fetch(url, retries=2 if name != "list" else 3)
        except urllib.error.HTTPError as err:
            if name != "list" and err.code == 404:
                continue  # no archive page for that year or page number
            errors.append("%s: HTTP %d" % (url, err.code))
            if err.code in (401, 403):
                record["state"] = "blocked"
            continue
        except Exception as err:  # timeouts, DNS, resets
            errors.append("%s: %s" % (url, str(err)[:120]))
            continue
        if CHALLENGE.search(body[:20000]):
            # A bot-challenge page served with HTTP 200 in place of the list.
            errors.append("%s: the site answered with a bot challenge instead of the page" % url)
            record["state"] = "blocked"
            continue
        ext = "xml" if (source.get("parser") == "rss") else "json" if body.lstrip()[:1] in (b"{", b"[") else "html"
        snapshot = None  # saved only when the page brings a new item, so unchanged days leave no files
        try:
            found = parse_page(body, source, url)
        except Exception as err:
            errors.append("%s: could not be parsed (%s: %s)" % (url, type(err).__name__, str(err)[:120]))
            continue
        read += len(found)
        page_new = 0
        oldest = max(since, FLOOR).isoformat() if since else FLOOR.isoformat()
        for f in found:
            if f["date"] < oldest:
                continue
            k = url_key(f["url"])
            old = known.get(k)
            if old is not None:
                if old.get("source_key") == key and (old["title_ja"], old["date"]) != (f["title_ja"], f["date"]):
                    conflicts.append((old["id"], "%s %s" % (old["date"], old["title_ja"]), "%s %s" % (f["date"], f["title_ja"])))
                continue
            if snapshot is None:
                snapshot = Path(saved) if saved and common.ROOT in Path(saved).resolve().parents else \
                    common.save_snapshot(folder, "%s_%s" % (key, name), ext, body)
            item = {
                "id": item_id(owner["id"], f["date"], f["url"]),
                "insurer_ids": list(owner["insurer_ids"]),
                "date": f["date"],
                "title_ja": f["title_ja"],
                "url": f["url"],
                "source_owner": owner["id"],
                "source_key": key,
                "source_kind": kind,
                "retrieved_date": common.today(),
                "snapshot": common.rel(Path(snapshot).resolve()),
            }
            known[k] = item
            new.append(item)
            page_new += 1
        # Further pages of a paged list only matter while they still bring something new.
        if name.startswith("p") and not page_new and not since:
            break
        if name.startswith("p") and since and (not found or all(f["date"] < since.isoformat() for f in found)):
            break
    if errors and not read:
        record["state"] = "blocked" if record["state"] == "blocked" else "failed"
    if errors:
        record["message"] = "; ".join(errors)[:600]
    elif not read:
        record["state"] = "failed"
        record["message"] = "the list page was read but no dated items were found on it"
    if record["state"] == "ok":
        record["last_ok"] = common.today()
    record["items_on_page"] = read
    return new, conflicts, record


def collect(args):
    table = owners()
    selected = args.only.split(",") if args.only else [o for o in table if table[o]["sources"]]
    unknown = [o for o in selected if o not in table]
    if unknown:
        sys.exit("unknown owner id: %s" % ", ".join(unknown))
    since = max(dt.date.fromisoformat(args.since), FLOOR) if args.since else None
    files = dict(f.split("=", 1) for f in args.file or [])
    all_ok = True
    for oid in selected:
        owner = table[oid]
        print("\n=== %s (%s) ===" % (owner["name"], oid), flush=True)
        if not owner["sources"]:
            print("  no news sources configured")
            continue
        status(oid, "running")
        path = ITEMS / (oid + ".jsonl")
        try:
            items = read_items(path)
            known = {url_key(i["url"]): i for i in items}
            states, added, n_conf, worst, notes = {}, 0, 0, "ok", []
            for source in owner["sources"]:
                new, conflicts, record = collect_source(owner, source, since, known, files)
                prev = read_state()["sources"].get("%s/%s" % (oid, source["key"]), {})
                if "last_ok" not in record and prev.get("last_ok"):
                    record["last_ok"] = prev["last_ok"]
                if new:
                    record["last_new"] = max(i["date"] for i in new)
                elif prev.get("last_new"):
                    record["last_new"] = prev["last_new"]
                states["%s/%s" % (oid, source["key"])] = record
                items += new
                added += len(new)
                n_conf += len(conflicts)
                dates = sorted(i["date"] for i in new)
                print("  %-22s %4d on page, %4d new%s%s" % (
                    source["key"], record["items_on_page"], len(new),
                    (", %s to %s" % (dates[0], dates[-1])) if dates else "",
                    ("  [%s: %s]" % (record["state"], record["message"])) if record["state"] != "ok" or record["message"] else ""))
                for iid, old, now in conflicts:
                    print("    CONFLICT %s: on file %s, site now shows %s" % (iid, old, now))
                if record["state"] != "ok":
                    worst = "blocked" if record["state"] == "blocked" and worst != "failed" else "failed"
                    notes.append("%s: %s" % (source["key"], record["message"]))
            if added:
                write_items(path, items)
            update_state(states)
            if worst == "blocked":
                status(oid, "blocked", "Not reachable from this network (%s). Connect through a Japan connection and run again." % "; ".join(notes)[:300])
            elif worst == "failed":
                status(oid, "failed", "; ".join(notes)[:400])
            else:
                status(oid, "ok")
            all_ok = all_ok and worst == "ok"
        except Exception as err:  # keep going with the other owners
            status(oid, "failed", "%s: %s" % (type(err).__name__, err))
            all_ok = False
    marked = mark_duplicates()
    if marked:
        print("\n%d parent-group items repeat a subsidiary's release and are marked as duplicates" % marked)
    if args.enrich:
        import news_enrich  # imported here: it imports this module
        all_ok = news_enrich.run(only=set(selected)) and all_ok
    sys.exit(0 if all_ok else 2)


# ---------------------------------------------------------------------------------------------- add
def check_item(raw, known_insurers):
    """Validate one researched item. Returns (item, error)."""
    if not isinstance(raw, dict):
        return None, "not an object"
    date, url = raw.get("date"), raw.get("url")
    if not isinstance(date, str) or not DATE_RE.match(date) or not norm_date(*date.split("-")):
        return None, "date must be YYYY-MM-DD"
    if not isinstance(url, str) or not re.match(r"https?://[^\s]+$", url):
        return None, "url must be an http(s) address"
    if not (raw.get("title_ja") or raw.get("title_en")):
        return None, "needs title_ja or title_en"
    kind = raw.get("source_kind")
    if kind not in KINDS:
        return None, "source_kind must be one of %s" % ", ".join(KINDS)
    if kind == "press" and not raw.get("publisher"):
        return None, "a press item needs the publisher's name"
    ids = raw.get("insurer_ids")
    if not isinstance(ids, list) or any(i not in known_insurers for i in ids):
        return None, "insurer_ids must be a list of ids from data/insurers/"
    if raw.get("category") and raw["category"] not in CATEGORIES:
        return None, "category must be one of %s" % ", ".join(CATEGORIES)
    if len(raw.get("summary_en") or "") > 600:
        return None, "summary_en is limited to 600 characters (a summary in your own words, not the article's text)"
    item = {"id": "", "insurer_ids": ids, "date": date}
    for k in ("title_ja", "title_en", "url", "publisher", "source_kind", "category", "importance", "summary_en",
              "product_ids", "story_ids", "note"):
        if raw.get(k) not in (None, "", []):
            item[k] = raw[k]
    item["retrieved_date"] = raw.get("retrieved_date") or common.today()
    return item, None


def add(args):
    if not ID_RE.match(args.to):
        sys.exit("--to must be a short lower-case name, e.g. press")
    table = owners()
    if args.to in table and table[args.to]["sources"]:
        sys.exit("%s.jsonl is written by `collect`; choose another name, e.g. press or press-%s" % (args.to, args.to))
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    raw = raw if isinstance(raw, list) else [raw]
    known = {url_key(i["url"]): i for i in all_items()}
    path = ITEMS / (args.to + ".jsonl")
    items = read_items(path)
    added = skipped = bad = 0
    for n, r in enumerate(raw, 1):
        item, error = check_item(r, insurer_ids())
        if error:
            print("  item %d rejected: %s" % (n, error))
            bad += 1
            continue
        k = url_key(item["url"])
        if k in known:
            print("  item %d already on file as %s" % (n, known[k]["id"]))
            skipped += 1
            continue
        item["id"] = item_id(args.to, item["date"], item["url"])
        item["source_owner"] = args.to
        item["added_by"] = "research"
        known[k] = item
        items.append(item)
        added += 1
    if added:
        write_items(path, items)
    print("%d new, %d already on file, %d rejected -> %s" % (added, skipped, bad, common.rel(path)))
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------------------------------------- probe, status
def probe(args):
    source = {"key": "probe", "parser": args.parser, "url": args.url}
    if args.encoding:
        source["encoding"] = args.encoding
    body = Path(args.file).read_bytes() if args.file else common.fetch(args.url, retries=2)
    items = parse_page(body, source, args.url)
    for i in items[: args.show]:
        print("%s  %s\n            %s" % (i["date"], i["title_ja"][:90], i["url"]))
    dates = sorted(i["date"] for i in items)
    print("%d items%s (%d bytes)" % (len(items), (", %s to %s" % (dates[0], dates[-1])) if dates else "", len(body)))


def show_status(_args):
    table, state = owners(), read_state()["sources"]
    counts = {}
    for i in all_items():
        counts[i.get("source_owner", "")] = counts.get(i.get("source_owner", ""), 0) + 1
    for oid, owner in table.items():
        if not owner["sources"]:
            continue
        print("%-18s %-6s %5d items" % (oid, owner["tier"], counts.get(oid, 0)))
        for s in owner["sources"]:
            r = state.get("%s/%s" % (oid, s["key"]), {})
            print("    %-20s %-8s checked %-10s ok %-10s newest %-10s %s" % (
                s["key"], r.get("state", "never"), r.get("last_checked", "-"), r.get("last_ok", "-"),
                r.get("last_new", "-"), r.get("message", "")[:80]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="print the owners with sources as JSON and exit")
    sub = parser.add_subparsers(dest="cmd")
    c = sub.add_parser("collect", help="read the list pages and add new items")
    c.add_argument("--only", help="comma-separated owner ids, e.g. dfl,fsa")
    c.add_argument("--since", help="YYYY-MM-DD: walk the archives back to this date (not before %s, the "
                                    "tracker's start); without it only the current pages are read" % FLOOR.isoformat())
    c.add_argument("--file", action="append", metavar="KEY=PATH",
                   help="read a saved copy of a source's list page instead of fetching it (for sites that only "
                        "answer from some networks); KEY is the source key, or key_y2025 / key_p2 for an archive page")
    c.add_argument("--enrich", action="store_true",
                   help="afterwards add English titles, categories and summaries to the new items "
                        "(scripts/news_enrich.py; runs Claude Code with your sign-in)")
    c.set_defaults(func=collect)
    a = sub.add_parser("add", help="add researched items from a JSON file")
    a.add_argument("file")
    a.add_argument("--to", required=True, help="items file to add to (data/news/items/<name>.jsonl), e.g. press")
    a.set_defaults(func=add)
    p = sub.add_parser("probe", help="show what the reader finds on a list page")
    p.add_argument("url")
    p.add_argument("--parser", default="auto")
    p.add_argument("--encoding")
    p.add_argument("--file", help="a saved copy of the page")
    p.add_argument("--show", type=int, default=12)
    p.set_defaults(func=probe)
    s = sub.add_parser("status", help="when each source was last checked")
    s.set_defaults(func=show_status)
    args = parser.parse_args()
    if args.list:
        table = owners()
        print(json.dumps([{"id": o["id"], "name": o["name"], "kind": o["tier"] or "market-wide"}
                          for o in table.values() if o["sources"]], ensure_ascii=False))
        return
    if not args.cmd:
        parser.error("choose a command: collect, add, probe or status")
    args.func(args)


if __name__ == "__main__":
    main()
