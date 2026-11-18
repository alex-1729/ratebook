"""Step 1 of the brochure repaint: read one PDF page into a page model.

    .venv/bin/python scripts/repaint/extract.py SRC.pdf PAGE_INDEX OUT_DIR

Writes OUT_DIR/page.json (the page model) and OUT_DIR/page.png (150 dpi render).
The page is first normalised (crop box becomes the page, rotation removed) so every
coordinate is in points from the top-left of the page as it is displayed.
"""
import json
import re
import sys
from pathlib import Path

import fitz

IGNORE_FOR_STYLE = set("●・＊*▲►“”\"、。 　〈〉（）()")
CJK = re.compile(r"[ぁ-ゖァ-ヺ一-鿿]")


def open_page(src, page_index):
    doc = fitz.open(src)
    page = doc[page_index]
    page.set_mediabox(page.cropbox)   # drop the crop offset ...
    page.remove_rotation()            # ... and the rotation; the rendering is unchanged
    return doc, page


def weight_of(font):
    return 700 if any(k in font for k in ("Bold", "Medium", "Heavy", "Black", "-B", "W6", "W7", "W8", "W9")) else 400


def text_lines(page, version=1):
    """The page's text lines. version 1: one entry per PDF line (what older page.json files hold).
    version 2: lines holding several separate items are split, and duplicate copies are removed
    (see duplicate_lines). Always returns a list."""
    d = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    out = []

    def add(bbox, spans, vert, block):
        spans = [s for s in spans if s["text"].strip()]
        if not spans:
            return
        colours, weights = {}, {}
        for s in spans:
            n = sum(1 for ch in s["text"] if ch not in IGNORE_FOR_STYLE) or 0.01
            colours[s["color"]] = colours.get(s["color"], 0) + n
            w = weight_of(s["font"])
            weights[w] = weights.get(w, 0) + n
        out.append({
            "id": len(out), "block": block, "bbox": [round(v, 2) for v in bbox], "vert": vert,
            "text": "".join(s["text"] for s in spans).strip(),
            "size": round(max(s["size"] for s in spans), 2),
            "color": max(colours, key=colours.get), "weight": max(weights, key=weights.get),
            "spans": [{"bbox": [round(v, 2) for v in s["bbox"]], "text": s["text"], "size": round(s["size"], 2),
                       "color": s["color"], "weight": weight_of(s["font"]), "font": s["font"]} for s in spans],
        })

    for bi, b in enumerate(d["blocks"]):
        if all(l["wmode"] == 1 for l in b["lines"]) and len(b["lines"]) > 1:
            spans = [s for l in b["lines"] for s in l["spans"]]
            if version < 2:
                add(b["bbox"], spans, True, bi)                                    # vertical writing: one unit
            else:
                for column in vertical_columns(unique_spans(spans)):
                    for part in split_at_gaps(column, axis=1):
                        box = fitz.Rect(part[0]["bbox"])
                        for s in part:
                            box |= fitz.Rect(s["bbox"])
                        add(list(box), part, True, bi)
        else:
            for l in b["lines"]:
                if l["wmode"] == 1 or version < 2:
                    add(l["bbox"], l["spans"], l["wmode"] == 1, bi)
                    continue
                for part in split_at_gaps(unique_spans(l["spans"])):
                    box = fitz.Rect(part[0]["bbox"])
                    for s in part:
                        box |= fitz.Rect(s["bbox"])
                    add(list(box), part, False, bi)
    if version < 2:
        return out
    lines, dups = dedupe(out)
    _V2_DUPLICATES[id(page)] = dups
    return lines


def duplicate_lines(page):
    """Version 2 only: the copies removed by text_lines(page, 2), each with the id of the line kept."""
    if id(page) not in _V2_DUPLICATES:
        text_lines(page, 2)
    return _V2_DUPLICATES[id(page)]


_V2_DUPLICATES = {}


def unique_spans(spans):
    """Drop a span drawn twice in the same place (outline under fill), keeping the later copy."""
    out = []
    for s in spans:
        twin = next((o for o in out if o["text"] == s["text"] and max(abs(a - b) for a, b in zip(o["bbox"], s["bbox"])) < 1.0), None)
        if twin is not None:
            out[out.index(twin)] = s
        else:
            out.append(s)
    return out


def vertical_columns(spans):
    """Vertical writing: group characters into columns, right-most column first."""
    cols = []
    for s in sorted((s for s in spans if s["text"].strip()), key=lambda s: -(s["bbox"][0] + s["bbox"][2])):
        cx = (s["bbox"][0] + s["bbox"][2]) / 2
        for col in cols:
            if abs(col["cx"] - cx) < 0.6 * max(s["size"], col["size"]):
                col["spans"].append(s)
                break
        else:
            cols.append({"cx": cx, "size": s["size"], "spans": [s]})
    return [c["spans"] for c in cols]


def split_at_gaps(spans, axis=0):
    """One PDF line can hold several separate items (tabs, table cells, list items side by side).
    Split it wherever two neighbouring pieces are far apart."""
    spans = sorted((s for s in spans if s["text"].strip()), key=lambda s: s["bbox"][axis])
    parts = []
    for s in spans:
        if parts:
            prev = parts[-1][-1]
            gap = s["bbox"][axis] - prev["bbox"][axis + 2]
            if gap > max(2.5 * max(s["size"], prev["size"]), 14):
                parts.append([s])
                continue
            parts[-1].append(s)
        else:
            parts.append([s])
    return parts


def dedupe(lines):
    """Text drawn twice in the same place (an outline or shadow under the fill) is one piece of text.
    Keep the copy drawn last; list the others so they are removed with it."""
    keep, dups = [], []
    for l in lines:
        slot = next((i for i, k in enumerate(keep) if k["text"] == l["text"] and k["vert"] == l["vert"]
                     and max(abs(a - b) for a, b in zip(k["bbox"], l["bbox"])) < 1.5), None)
        if slot is None:
            keep.append(l)
        else:
            twin = keep[slot]
            dups.append({"bbox": twin["bbox"], "spans": [sp["bbox"] for sp in twin["spans"]], "vert": twin["vert"],
                         "text": twin["text"], "of": slot})      # position in keep == final line id
            keep[slot] = l
    for i, l in enumerate(keep):
        l["id"] = i
    lines[:] = keep
    return keep, dups


def outlined_text(page):
    """Rows of small complex filled shapes in one colour: probably text converted to outlines."""
    cands = []
    for dr in page.get_drawings():
        r = dr["rect"]
        if dr["fill"] is None or r.width > 16 or r.height > 16 or r.width < 2.5 or r.height < 2.5 or len(dr["items"]) < 8:
            continue
        cands.append((fitz.Rect(r), tuple(round(c, 2) for c in dr["fill"])))
    cands.sort(key=lambda c: (round(c[0].y0 / 6), c[0].x0))
    rows, cur, box = [], [], None

    def close():
        if len(cur) >= 3 and box.width > 2.5 * box.height:
            rows.append((fitz.Rect(box), len(cur), cur[0][1]))

    for r, fill in cands:
        if cur and fill == cur[-1][1] and abs(r.y0 - box.y0) < 5 and -16 < r.x0 - box.x1 < 6:
            cur.append((r, fill)); box |= r
        else:
            close()
            cur, box = [(r, fill)], fitz.Rect(r)
    close()
    return [{"id": "o%d" % i, "bbox": [round(v, 1) for v in b], "glyphs": n, "fill": list(fill)} for i, (b, n, fill) in enumerate(rows)]


def page_model(src, page_index):
    doc, page = open_page(src, page_index)
    lines = text_lines(page, 2)
    duplicates = duplicate_lines(page)
    return doc, page, {
        "extract_version": 2,
        "source": str(src), "page_index": page_index,
        "width": round(page.rect.width, 2), "height": round(page.rect.height, 2),
        "lines": lines,
        "duplicates": duplicates,
        "outlined": outlined_text(page),
        "images": [{"bbox": [round(v, 1) for v in i["bbox"]]} for i in page.get_image_info()],
    }


if __name__ == "__main__":
    src, page_index, out_dir = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    doc, page, model = page_model(src, page_index)
    page.get_pixmap(dpi=150).save(str(out_dir / "page.png"))
    model["render"] = "page.png"
    (out_dir / "page.json").write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    jp = sum(1 for l in model["lines"] if CJK.search(l["text"]))
    print("%s page %d: %d lines (%d with Japanese), %d duplicate copies, %d outlined-text candidates, %d images, %.0f x %.0f pt"
          % (Path(src).name, page_index, len(model["lines"]), jp, len(model["duplicates"]), len(model["outlined"]), len(model["images"]), model["width"], model["height"]))
