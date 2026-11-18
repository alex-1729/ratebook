"""Translation memory and whole-page reuse.

translations/_memory/memory.jsonl  append-only; one validated model unit per line:
    {"key": [line texts...], "role", "en", "en_short", "spans": [[span texts] per line], "sizes", "vert",
     "pdf", "page", "date", "backend", "model"}
  Segment styles are stored by position: "@0" is the unit's first line, "@1:2" span 2 of its second line.
translations/_memory/pages.json    {page hash: {"units": path of a valid units.json, "pdf", "page", "date"}}

Set REPAINT_MEMORY_DIR to use another folder (tests do).

A memory entry is only used for a line (or run of consecutive lines) when
- the text of every line is exactly the stored text (whitespace normalised),
- the lines stand on their own (translate_rules.isolated: nothing above or below them in their text block),
  as they did when the entry was stored, and are written in the same direction,
- each line's size is within a third of the stored size,
- every stored entry for that text agrees on the role (otherwise it is ambiguous and not used),
- span-level styles ("@1:2") are only re-used when the lines have the same spans as when stored.
"""
import datetime
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import translate_rules as rules

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
_lock = threading.Lock()


def memory_dir():
    d = os.environ.get("REPAINT_MEMORY_DIR")
    return Path(d) if d else REPO / "translations" / "_memory"


def norm(text):
    return " ".join(str(text).split())


# ----------------------------------------------------------------------------------------------- page hash
def page_hash(page, prompt_text, image_digests=None):
    """Hash of what the translator sees: texts and rounded boxes of lines, outlined text and images, the image
    contents when known, the extract version and the translator prompt."""
    h = hashlib.sha256()

    def put(obj):
        h.update(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        h.update(b"\n")

    put({"extract_version": page.get("extract_version", 1), "w": round(page.get("width", 0)), "h": round(page.get("height", 0)),
         "prompt": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()})
    for l in page.get("lines", []):
        put([l["id"], norm(l["text"]), [round(v) for v in l["bbox"]], bool(l.get("vert")), [norm(s["text"]) for s in l.get("spans", [])]])
    for o in page.get("outlined", []):
        put([o.get("id"), [round(v) for v in o["bbox"]], o.get("glyphs")])
    for im in page.get("images", []):
        put([round(v) for v in im["bbox"]])
    put(sorted(image_digests) if image_digests is not None else "no-image-digests")
    return h.hexdigest()


def image_digests(page_model):
    """MD5 digests of the pictures on the source PDF page (None if the PDF cannot be opened)."""
    try:
        import extract
        src = Path(page_model["source"])
        if not src.is_absolute() and not src.exists():
            src = REPO / src
        doc, page = extract.open_page(str(src), page_model["page_index"])
        out = [i["digest"].hex() for i in page.get_image_info(hashes=True) if i.get("digest")]
        doc.close()
        return out
    except Exception:   # noqa: BLE001 - without digests the hash simply never matches another page
        return None


def _load_pages():
    f = memory_dir() / "pages.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_page(h):
    """A previous valid units.json for this page hash, or None."""
    with _lock:
        rec = _load_pages().get(h)
    if rec and Path(rec["units"]).is_file():
        return rec
    return None


def record_page(h, units_path, pdf, page_number):
    with _lock:
        d = memory_dir()
        d.mkdir(parents=True, exist_ok=True)
        idx = _load_pages()
        idx[h] = {"units": str(Path(units_path).resolve()), "pdf": pdf, "page": page_number,
                  "date": datetime.date.today().isoformat()}
        tmp = d / ("pages.json.%d.part" % os.getpid())
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(d / "pages.json"))


# ----------------------------------------------------------------------------------------------- storing
_STYLE = re.compile(r"\s*(\d+)\s*(?::\s*(\d+)\s*)?")


def _rel_segments(segs, pos):
    """Segments with styles rewritten by position in the unit; None if a style points outside the unit."""
    out = []
    for s in segs:
        s2 = dict(s)
        st = s.get("style")
        if st is not None:
            m = _STYLE.fullmatch(str(st))
            if not m or int(m.group(1)) not in pos:
                return None
            s2["style"] = "@%d" % pos[int(m.group(1))] + (":" + m.group(2) if m.group(2) is not None else "")
        out.append(s2)
    return out


def store(units, page, meta):
    """Append each storable model unit of a validated page to memory.jsonl. Returns the number stored."""
    lines = {l["id"]: l for l in page["lines"]}
    blocks = rules.block_index(page["lines"])
    dropped = set(units.get("drop", []))
    entries = []
    for u in units["units"]:
        if u.get("origin", "model") != "model" or "lines" not in u:
            continue
        ids = u["lines"]
        if any(i not in lines or lines[i].get("done") for i in ids):
            continue
        if ids != list(range(ids[0], ids[0] + len(ids))):          # only runs of consecutive lines can be matched
            continue
        if not rules.isolated(ids, lines, blocks):
            continue
        # a unit that absorbed a dropped particle beside it would read wrong where that particle is not dropped
        if any(d in lines and (lines[d].get("block") in {lines[i].get("block") for i in ids} or abs(d - ids[0]) <= 1
                               or abs(d - ids[-1]) <= 1) for d in dropped):
            continue
        pos = {lid: k for k, lid in enumerate(ids)}
        en = _rel_segments(u["en"], pos)
        short = _rel_segments(u["en_short"], pos) if "en_short" in u else None
        if en is None or ("en_short" in u and short is None):
            continue
        entries.append({"key": [norm(lines[i]["text"]) for i in ids], "role": u["role"], "en": en,
                        **({"en_short": short} if short else {}),
                        "spans": [[norm(s["text"]) for s in lines[i].get("spans", [])] for i in ids],
                        "sizes": [lines[i].get("size") for i in ids], "vert": bool(lines[ids[0]].get("vert")),
                        **meta, "date": datetime.date.today().isoformat()})
    if entries:
        with _lock:
            d = memory_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "memory.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    return len(entries)


# ----------------------------------------------------------------------------------------------- looking up
def load_index():
    idx = {}
    f = memory_dir() / "memory.jsonl"
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return idx
    for row in text.splitlines():
        try:
            e = json.loads(row)
        except ValueError:
            continue
        idx.setdefault(tuple(e["key"]), []).append(e)
    return idx


def _choose(entries):
    """The entry to use for one text, or None when the stored ones disagree on the role."""
    if len({e["role"] for e in entries}) != 1:
        return None
    counts = {}
    for k, e in enumerate(entries):
        sig = json.dumps([e["en"], e.get("en_short")], ensure_ascii=False, sort_keys=True)
        c = counts.setdefault(sig, [0, 0, e])
        c[0] += 1
        c[1], c[2] = k, e                      # latest wins a tie
    return max(counts.values(), key=lambda c: (c[0], c[1]))[2]


def _abs_segments(segs, ids, lines, spans_same):
    out = []
    for s in segs:
        s2 = dict(s)
        st = s.get("style")
        if isinstance(st, str) and st.startswith("@"):
            p, _, sp = st[1:].partition(":")
            lid = ids[int(p)]
            if sp:
                if not spans_same:
                    return None
                s2["style"] = "%d:%s" % (lid, sp)
            else:
                s2["style"] = lid
        out.append(s2)
    return out


def apply(page, index=None):
    """Mark lines found in memory as done in `page` (in place). Returns the list of groups used:
    {"lines": [...], "role", "en", "en_short"?, "pdf", "page"}."""
    index = load_index() if index is None else index
    if not index:
        return []
    lines = page["lines"]
    by_id = {l["id"]: l for l in lines}
    blocks = rules.block_index(lines)
    maxlen = max(len(k) for k in index)
    ids_sorted = sorted(by_id)
    used = []
    i = 0
    while i < len(ids_sorted):
        hit = None
        for n in range(min(maxlen, len(ids_sorted) - i), 0, -1):
            ids = ids_sorted[i:i + n]
            if ids[-1] - ids[0] != n - 1 or any(by_id[j].get("done") for j in ids):
                continue
            entries = index.get(tuple(norm(by_id[j]["text"]) for j in ids))
            if not entries:
                continue
            e = _choose(entries)
            if e is None:
                continue
            if bool(by_id[ids[0]].get("vert")) != e.get("vert", False) or any(bool(by_id[j].get("vert")) != e.get("vert", False) for j in ids):
                continue
            if any(not s or not by_id[j].get("size") or not (0.75 <= by_id[j]["size"] / s <= 1.34) for j, s in zip(ids, e["sizes"])):
                continue
            if not rules.isolated(ids, by_id, blocks):
                continue
            spans_same = [[norm(s["text"]) for s in by_id[j].get("spans", [])] for j in ids] == e.get("spans")
            en = _abs_segments(e["en"], ids, by_id, spans_same)
            short = _abs_segments(e["en_short"], ids, by_id, spans_same) if e.get("en_short") else None
            if en is None or (e.get("en_short") and short is None):
                continue
            hit = (ids, e, en, short)
            break
        if not hit:
            i += 1
            continue
        ids, e, en, short = hit
        gid = "m%d" % (len(used) + 1)
        for j in ids:
            l = by_id[j]
            l["done"], l["origin"], l["group"] = True, "memory", gid
            l["role"] = e["role"]
            l["en"] = [s for s in en if str(s.get("style", "")).split(":")[0] == str(j)] or (en if j == ids[0] else [])
        g = {"id": gid, "lines": ids, "role": e["role"], "en": en, "pdf": e.get("pdf"), "page": e.get("page")}
        if short:
            g["en_short"] = short
        used.append(g)
        i += len(ids)
    return used
