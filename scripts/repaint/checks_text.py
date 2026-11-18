"""Text-layer helpers for checks.py: read a page's text with per-character geometry, tell
Japanese from English, assign rendered English lines to units, and align what was rendered
with what units.json asked for.

Nothing here trusts placements.json beyond using it as a hint for which text belongs where.
"""
import re
import unicodedata
from difflib import SequenceMatcher

import fitz

FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
# Kana, CJK ideographs, half-width katakana (・ is in the katakana block, so it counts).
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
# Full-width / Japanese punctuation and symbols: 、。「」〈〉 （）％～：！？ etc.
JP_PUNCT = re.compile(r"[　-〿！-／：-＠［-｀｛-･]")
MARKS = set("●•・■□◆◇○▶►▲△※*＊-–")
ASCENT, DESCENT = 0.80, 0.22   # glyph band around the baseline, in em


def has_japanese(s):
    return bool(CJK.search(s) or JP_PUNCT.search(s))


def japanese_chars(s):
    return "".join(ch for ch in s if CJK.match(ch) or JP_PUNCT.match(ch))


def weight_of(font, flags=0):
    if flags & 16:
        return 700
    return 700 if any(k in font for k in ("Bold", "Medium", "Heavy", "Black", "-B", "W6", "W7", "W8", "W9", "Semibold", "SemiBold", "DeBold")) else 400


def norm_char(ch):
    """Normalise one character for comparison; may return several characters or none."""
    ch = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
          "−": "-", "­": "", "•": "•"}.get(ch, ch)
    ch = unicodedata.normalize("NFKC", ch)
    return "".join(c for c in ch if not c.isspace())


def words(s):
    # letters and digits are separate words, so "③above" and "③ above" compare equal
    return re.findall(r"[a-z]+|[0-9]+", unicodedata.normalize("NFKC", s).lower().replace("’", "'"))


def glyph_box(ch, direction, wmode):
    """A tight box for one character: the em band around the baseline, not the font's full line box."""
    x0, y0, x1, y1 = ch["bbox"]
    s = ch["size"]
    dx, dy = direction
    if wmode == 1:
        return (x0, y0, x1, y1)
    ox, oy = ch["o"]
    if abs(dy) < 1e-3 and dx > 0:
        return (x0, oy - ASCENT * s, x1, oy + DESCENT * s)
    if abs(dx) < 1e-3 and dy < 0:      # reads bottom-to-top: ascenders point left
        return (ox - ASCENT * s, y0, ox + DESCENT * s, y1)
    if abs(dx) < 1e-3 and dy > 0:      # reads top-to-bottom: ascenders point right
        return (ox - DESCENT * s, y0, ox + ASCENT * s, y1)
    return (x0, y0, x1, y1)


def _line_from_spans(spans, direction, wmode, bbox, key):
    chars = []
    for s in spans:
        bold = weight_of(s["font"], s.get("flags", 0)) == 700
        for c in s["chars"]:
            chars.append({"c": c["c"], "bbox": tuple(c["bbox"]), "o": tuple(c["origin"]), "size": s["size"],
                          "color": s["color"], "font": s["font"], "bold": bold})
    for ch in chars:
        ch["g"] = glyph_box(ch, direction, wmode)
    text = "".join(ch["c"] for ch in chars)
    return {"key": key, "dir": tuple(direction), "wmode": wmode, "bbox": tuple(bbox), "chars": chars, "text": text}


def page_text_lines(page):
    """Every text line on the page with per-character geometry (rawdict), one entry per PDF line."""
    d = page.get_text("rawdict", flags=FLAGS)
    out = []
    for bi, b in enumerate(d["blocks"]):
        if b.get("type", 0) != 0:
            continue
        for li, l in enumerate(b["lines"]):
            line = _line_from_spans(l["spans"], l["dir"], l["wmode"], l["bbox"], (bi, li))
            if line["text"].strip():
                out.append(line)
    return out


def original_lines(page, page_json_lines):
    """Per-character geometry for every page.json line, keyed by line id.

    Works for any extract version: each line takes the source characters whose centres fall inside
    its spans' boxes (a character drawn twice in the same place counts once)."""
    d = page.get_text("rawdict", flags=FLAGS)
    pool = []     # (centre x, centre y, char dict, line dir, wmode, span)
    for b in d["blocks"]:
        if b.get("type", 0) != 0:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                for c in s["chars"]:
                    if not c["c"].strip():
                        continue
                    x0, y0, x1, y1 = c["bbox"]
                    pool.append(((x0 + x1) / 2, (y0 + y1) / 2, c, tuple(l["dir"]), l["wmode"], s))
    by_id = {}
    for pj in page_json_lines:
        spans = pj.get("spans") or [{"bbox": pj["bbox"], "text": pj["text"]}]
        seen, chars, dirs = set(), [], []
        for cx, cy, c, direction, wmode, s in pool:
            # inside one of the line's spans, and a character that span actually holds (a badge or
            # number drawn inside the gap of a sentence belongs to its own line)
            if not any(sp["bbox"][0] - 0.5 <= cx <= sp["bbox"][2] + 0.5 and sp["bbox"][1] - 0.5 <= cy <= sp["bbox"][3] + 0.5
                       and c["c"] in sp.get("text", "") for sp in spans):
                continue
            key = (c["c"], round(c["origin"][0], 1), round(c["origin"][1], 1))
            if key in seen:
                continue
            seen.add(key)
            bold = weight_of(s["font"], s.get("flags", 0)) == 700
            ch = {"c": c["c"], "bbox": tuple(c["bbox"]), "o": tuple(c["origin"]), "size": s["size"], "color": s["color"],
                  "font": s["font"], "bold": bold}
            wm = 1 if pj.get("vert") and wmode == 1 else 0
            ch["g"] = glyph_box(ch, direction, wm)
            chars.append(ch)
            dirs.append((direction, wm))
        if not chars:
            continue
        direction, wm = max(set(dirs), key=dirs.count)
        by_id[pj["id"]] = {"key": pj["id"], "dir": direction, "wmode": wm, "bbox": tuple(pj["bbox"]), "chars": chars,
                           "text": "".join(c["c"] for c in chars)}
    return by_id


# ---------------------------------------------------------------- units

def parse_style(style):
    if isinstance(style, int) and not isinstance(style, bool):
        return style, None
    m = re.fullmatch(r"\s*(\d+)\s*(?::\s*(\d+)\s*)?", str(style))
    if not m:
        return None, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) is not None else None)


def style_of(style, page_lines_by_id):
    """(size, colour, weight) the segment is meant to take."""
    li, si = parse_style(style)
    line = page_lines_by_id.get(li)
    if line is None:
        return None
    if si is not None and si < len(line["spans"]):
        s = line["spans"][si]
        return {"size": s["size"], "color": s["color"], "weight": s["weight"], "line": li, "span": si}
    return {"size": line["size"], "color": line["color"], "weight": line["weight"], "line": li, "span": None}


def unit_source_lines(u):
    return list(u.get("lines") or [])


def rects_of(v):
    if not v:
        return []
    if isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v):
        return [fitz.Rect(v)]
    out = []
    for r in v:
        if isinstance(r, dict):
            r = r.get("bbox") or r.get("rect")
        if isinstance(r, (list, tuple)) and len(r) == 4:
            out.append(fitz.Rect(r))
    return out


def _rect_dist(a, b):
    dx = max(0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return (dx * dx + dy * dy) ** 0.5


def assign_lines(new_lines, units, zones, placements_by_id):
    """Give each rendered English line to the unit it belongs to.
    zones: unit id -> Rect of the original Japanese. Returns (unit id -> [lines], unassigned)."""
    exp_words = {}
    for u in units:
        ws = set()
        for key in ("en", "en_short"):
            for seg in u.get(key) or []:
                ws.update(words(seg.get("t", "")))
        exp_words[u["id"]] = ws
    placed = {uid: rects_of(p.get("placed")) for uid, p in placements_by_id.items()}
    out = {u["id"]: [] for u in units}
    unassigned = []
    for ln in new_lines:
        r = fitz.Rect(ln["bbox"])
        lw = [w for w in words(ln["text"])]
        best, best_score = None, -1e9
        for u in units:
            uid = u["id"]
            ts = (sum(1 for w in lw if w in exp_words[uid]) / len(lw)) if lw else 0.0
            cands = placed.get(uid) or []
            if cands:
                d = min(_rect_dist(r, fitz.Rect(c) + (-2, -2, 2, 2)) for c in cands)
                score = ts + (1.0 if d == 0 else 0) - d / 100.0
            else:
                z = zones.get(uid)
                d = _rect_dist(r, z) if z is not None else 500
                score = ts - d / 150.0
            if lw and ts == 0 and d > 2:      # wording shares nothing and it is not where the unit was
                continue
            if score > best_score:
                best, best_score = uid, score
        if best is not None and best_score > -0.2:
            out[best].append(ln)
        else:
            unassigned.append(ln)
    return out, unassigned


# ---------------------------------------------------------------- rendered unit text

def unit_chars(lines):
    """Characters of a unit in reading order, each tagged with its visual line index.
    Visual lines are ordered across the text direction; characters along it."""
    chars = []
    for ln in lines:
        dx, dy = ln["dir"]
        down = (-dy, dx)
        for ch in ln["chars"]:
            c = dict(ch)
            c["dir"] = (dx, dy)
            c["wmode"] = ln["wmode"]
            c["along"] = ch["o"][0] * dx + ch["o"][1] * dy
            c["cross"] = ch["o"][0] * down[0] + ch["o"][1] * down[1]
            chars.append(c)
    if not chars:
        return [], []
    chars.sort(key=lambda c: c["cross"])
    vlines, cur = [], [chars[0]]
    for c in chars[1:]:
        if abs(c["cross"] - cur[-1]["cross"]) <= 0.35 * max(c["size"], cur[-1]["size"]) and c["dir"] == cur[-1]["dir"]:
            cur.append(c)
        else:
            vlines.append(cur)
            cur = [c]
    vlines.append(cur)
    ordered = []
    for i, vl in enumerate(vlines):
        vl.sort(key=lambda c: c["along"])
        for c in vl:
            c["vline"] = i
            ordered.append(c)
    return ordered, vlines


def vline_text(vl):
    """Text of one visual line; a wide gap between two characters counts as a space (text set
    around an obstacle, or a mark followed by text, often has no space character)."""
    out = []
    prev = None
    for c in vl:
        if prev is not None and not c["c"].isspace() and not prev["c"].isspace():
            gap = (c["g"][0] - prev["g"][2]) if abs(c["dir"][1]) < 1e-3 else abs(c["along"] - prev["along"]) - c["size"]
            if gap > 0.18 * c["size"]:
                out.append(" ")
        out.append(c["c"])
        prev = c
    return "".join(out).strip()


def rendered_text(vlines):
    parts = []
    for vl in vlines:
        t = vline_text(vl)
        if parts and parts[-1].endswith("-") and t[:1].islower():
            parts[-1] = parts[-1] + t
        else:
            parts.append(t)
    return " ".join(parts)


def align(segments, chars):
    """Map each segment to the rendered characters it became.
    Returns a list (one per segment) of lists of rendered char indices."""
    exp, exp_seg = [], []
    for si, seg in enumerate(segments):
        for ch in seg.get("t", ""):
            for n in norm_char(ch):
                exp.append(n)
                exp_seg.append(si)
    ren, ren_idx = [], []
    for ci, c in enumerate(chars):
        for n in norm_char(c["c"]):
            ren.append(n)
            ren_idx.append(ci)
    out = [[] for _ in segments]
    if not exp or not ren:
        return out, 0.0
    sm = SequenceMatcher(None, "".join(exp), "".join(ren), autojunk=False)
    matched = 0
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            out[exp_seg[a + k]].append(ren_idx[b + k])
        matched += n
    for lst in out:
        lst[:] = sorted(set(lst))
    return out, matched / max(1, len(exp))
