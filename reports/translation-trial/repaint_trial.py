"""Trial: repaint one PDF page from Japanese to English, keeping the layout.

  repaint.py extract SRC PAGE OUT.json          -> text lines with stable ids, bboxes and styles
  repaint.py apply   SRC PAGE LINES.json EN.json OUT.pdf OUT.png REPORT.json

EN.json is a list of units: {"ids": [line ids], "en": "...", optional "align", "html": true,
"valign", "size"} plus {"outline": [x0,y0,x1,y1], "en": ...} for Japanese drawn as shapes.
"""
import html as htmllib
import json
import re
import statistics
import sys

import fitz

FLOOR = 5.5            # smallest English type size we accept (pt)
GAP = 1.6              # clearance kept from neighbouring text and shapes (pt)
CJK = re.compile(r"[ぁ-ゖァ-ヺ一-鿿]")
IGNORE_FOR_COLOUR = set("●・＊*▲►“”\"、。 　〈〉（）()")


def open_page(src, pageno):
    doc = fitz.open(src)
    page = doc[pageno]
    page.set_mediabox(page.cropbox)   # drop the crop offset ...
    page.remove_rotation()            # ... and the rotation; the rendering is unchanged
    return doc, page


def weight_of(font):
    return 700 if any(k in font for k in ("Bold", "Medium", "Heavy", "Black")) else 400


def extract(page):
    d = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    out = []

    def unit(bbox, spans, vert):
        spans = [s for s in spans if s["text"].strip()]
        if not spans:
            return
        colours, weights = {}, {}
        for s in spans:
            n = sum(1 for ch in s["text"] if ch not in IGNORE_FOR_COLOUR) or 0.01
            colours[s["color"]] = colours.get(s["color"], 0) + n
            w = weight_of(s["font"])
            weights[w] = weights.get(w, 0) + n
        out.append({
            "id": len(out), "bbox": [round(v, 2) for v in bbox], "vert": vert,
            "size": round(max(s["size"] for s in spans), 2),
            "color": max(colours, key=colours.get), "weight": max(weights, key=weights.get),
            "font": spans[0]["font"],
            "spans": [[round(v, 2) for v in s["bbox"]] for s in spans],
            "text": "".join(s["text"] for s in spans).strip(),
        })

    for b in d["blocks"]:
        if all(l["wmode"] == 1 for l in b["lines"]) and len(b["lines"]) > 1:
            unit(b["bbox"], [s for l in b["lines"] for s in l["spans"]], True)   # vertical writing: one unit
        else:
            for l in b["lines"]:
                unit(l["bbox"], l["spans"], l["wmode"] == 1)
    return out


def find_outlined_text(page):
    """Rows of small, curve-heavy filled shapes with one fill colour: likely text drawn as outlines."""
    cands = []
    for dr in page.get_drawings():
        r = dr["rect"]
        if dr["fill"] is None or r.width > 16 or r.height > 16 or r.width < 2.5 or r.height < 2.5:
            continue
        if len(dr["items"]) < 8:      # glyph outlines are complex paths; plain boxes and bullets are not
            continue
        cands.append((r, tuple(round(c, 2) for c in dr["fill"])))
    cands.sort(key=lambda c: (round(c[0].y0 / 6), c[0].x0))
    rows, cur, box = [], [], None
    for r, fill in cands:
        # glyphs are often several overlapping paths, so allow overlap as well as a small gap
        if cur and fill == cur[-1][1] and abs(r.y0 - box.y0) < 5 and -16 < r.x0 - box.x1 < 6:
            cur.append((r, fill)); box |= r
        else:
            if len(cur) >= 3 and box.width > 2.5 * box.height:
                rows.append(cur)
            cur, box = [(r, fill)], fitz.Rect(r)
    if len(cur) >= 3 and box.width > 2.5 * box.height:
        rows.append(cur)
    out = []
    for row in rows:
        box = fitz.Rect(row[0][0])
        for r, _ in row:
            box |= r
        out.append({"bbox": [round(v, 1) for v in box], "glyphs": len(row), "fill": row[0][1]})
    return out


# ---------------------------------------------------------------- layout helpers

def area(r):
    return abs(r.width * r.height)


def contains(outer, inner, tol=1.0):
    return outer.x0 - tol <= inner.x0 and outer.y0 - tol <= inner.y0 and outer.x1 + tol >= inner.x1 and outer.y1 + tol >= inner.y1


def container_of(o, shapes, page_rect):
    best = None
    for r in shapes:
        if contains(r, o, 2.5) and area(r) > area(o) * 0.9 and area(r) < area(page_rect) * 0.6:
            if best is None or area(r) < area(best):
                best = r
    return best


def allowed_region(o, cont, blockers, page_rect, align, vert):
    """Grow the original text box into free space of its container, stopping at neighbours."""
    lim = fitz.Rect(cont) if cont else fitz.Rect(page_rect)
    pad = min(3.0, max(0.3, (lim.width - o.width) / 4)) if cont else 12.0
    lim = fitz.Rect(lim.x0 + pad, lim.y0 + 0.4, lim.x1 - pad, lim.y1 - 0.4)
    lim &= fitz.Rect(page_rect.x0 + 12, page_rect.y0 + 4, page_rect.x1 - 12, page_rect.y1 - 4)
    lim |= o                                      # never less room than the original text had
    r = fitz.Rect(o)
    max_w = max(o.width * (1.3 if vert else 2.6), o.width + (6 if vert else 30))
    max_h = max(o.height * (2.6 if vert else 3.2), o.height + (30 if vert else 8))
    if not cont:                                  # free-standing text: be conservative
        max_w = max(o.width * 2.2, o.width + 20)
        max_h = o.height * 2.4 + 2

    def clear(rect, side):
        v = {"l": lim.x0, "r": lim.x1, "u": lim.y0, "d": lim.y1}[side]
        for b in blockers:
            if side in "lr":
                if b.y1 <= rect.y0 + 0.3 or b.y0 >= rect.y1 - 0.3:
                    continue
                if side == "r" and b.x0 >= rect.x1 - 0.5:
                    v = min(v, b.x0 - GAP)
                if side == "l" and b.x1 <= rect.x0 + 0.5:
                    v = max(v, b.x1 + GAP)
            else:
                if b.x1 <= rect.x0 + 0.3 or b.x0 >= rect.x1 - 0.3:
                    continue
                if side == "d" and b.y0 >= rect.y1 - 0.5:
                    v = min(v, b.y0 - GAP)
                if side == "u" and b.y1 <= rect.y0 + 0.5:
                    v = max(v, b.y1 + GAP)
        return v

    # horizontal
    left, right = max(clear(r, "l"), r.x0 - (max_w - o.width)), min(clear(r, "r"), r.x1 + (max_w - o.width))
    left, right = min(left, r.x0), max(right, r.x1)
    if align == "center":
        half = min(o.x0 - left, right - o.x1, (max_w - o.width) / 2)
        r.x0, r.x1 = o.x0 - half, o.x1 + half
    elif align == "right":
        r.x0 = max(left, o.x1 - max_w)
    else:
        r.x1 = min(right, o.x0 + max_w)
    # vertical
    up, down = max(clear(r, "u"), r.y0 - (max_h - o.height)), min(clear(r, "d"), r.y1 + (max_h - o.height))
    up, down = min(up, r.y0), max(down, r.y1)
    if align == "center" or vert:
        half = min(o.y0 - up, down - o.y1, (max_h - o.height) / 2)
        r.y0, r.y1 = o.y0 - half, o.y1 + half
    else:
        r.y1 = min(down, o.y0 + max_h)
        r.y0 = max(up, o.y0 - 1.0)
    return r


class Fitter:
    def __init__(self, css_base, archive):
        self.css_base, self.archive = css_base, archive
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=3000, height=3000)

    def css(self, u, size):
        return self.css_base + "body {margin: 0; padding: 0;} p {margin: 0; padding: 0;} * {font-family: en; font-size: %.2fpt; line-height: 1.14; color: #%06x; text-align: %s; font-weight: %s; margin: 0; padding: 0;}" % (
            size, u["color"], u["align"], "bold" if u["weight"] >= 600 else "normal")

    def measure(self, u, size, w, h):
        """Return the height the text needs in a w x h box at this size, or None if it does not fit."""
        slack = 0.12 * size
        spare, scale = self.page.insert_htmlbox(fitz.Rect(10, 10, 10 + w, 10 + h + slack), u["body"], css=self.css(u, size),
                                                scale_low=1, archive=self.archive)
        if spare < 0:
            return None
        need = h + slack - spare
        if u.get("lines") == 1 and need > size * 1.14 * 1.4:
            return None
        return need

    def best_size(self, u, w, h):
        floor = min(FLOOR, u["size"])
        size = u["size"]
        while size >= floor - 1e-6:
            if self.measure(u, size, w, h) is not None:
                return size
            size -= 0.25
        return None


def apply(src, pageno, lines_file, en_file, out_pdf, out_png, report_file):
    doc, page = open_page(src, pageno)
    lines = {l["id"]: l for l in extract(page)}
    saved = {l["id"]: l for l in json.load(open(lines_file))}
    for i, l in saved.items():                    # units are tied to positions: refuse to run on a different extraction
        cur = lines.get(i)
        if cur is None or cur["text"] != l["text"] or max(abs(a - b) for a, b in zip(cur["bbox"], l["bbox"])) > 0.6:
            raise SystemExit("line %s no longer matches the extraction the translation was made from" % i)
    units = json.load(open(en_file))
    used = set()
    for u in units:
        for i in u.get("ids", []):
            if i in used:
                raise SystemExit("line %s is used by two units" % i)
            used.add(i)
    for i, l in lines.items():                    # mechanical conversions
        if i in used:
            continue
        m = re.fullmatch(r"(\d+)\s*年", l["text"])
        if m:
            units.append({"ids": [i], "en": "%s yrs" % m.group(1), "align": "center"})
        m = re.fullmatch(r"(\d+)～(\d+)歳", l["text"])
        if m:
            units.append({"ids": [i], "en": "%s–%s" % m.groups(), "align": "center"})
    used = {i for u in units for i in u.get("ids", [])}

    page_rect = fitz.Rect(page.rect)
    drawings = page.get_drawings()
    shapes = [fitz.Rect(d["rect"]) for d in drawings if d["rect"].width > 3 and d["rect"].height > 3]
    hairlines = [fitz.Rect(d["rect"]) for d in drawings if (d["rect"].width <= 3) != (d["rect"].height <= 3)]
    images = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]

    # geometry of every unit
    for u in units:
        if "outline" in u:
            u["o"] = fitz.Rect(u["outline"]); u["vert"] = False
            u.setdefault("weight", 700); u.setdefault("align", "left"); u["color"] = u.get("colour", 0xFFFFFF)
            u["size"] = u.get("size", u["o"].height * 0.95)
        else:
            ls = [lines[i] for i in u["ids"]]
            o = fitz.Rect(ls[0]["bbox"])
            for l in ls:
                o |= fitz.Rect(l["bbox"])
            u["o"], u["vert"] = o, ls[0]["vert"]
            u["size"] = u.get("size", min(l["size"] for l in ls) if not u.get("html") else max(l["size"] for l in ls))
            chars = {}
            for l in ls:
                n = len(l["text"])
                chars[(l["color"], l["weight"])] = chars.get((l["color"], l["weight"]), 0) + n
            (u["color"], u["weight"]) = max(chars, key=chars.get)
            u["color"] = u.get("colour", u["color"])
        u["body"] = "<p>%s</p>" % (u["en"] if u.get("html") else htmllib.escape(u["en"]))
        u["cont"] = container_of(u["o"], shapes + images, page_rect)
        if "align" not in u:
            c = u["cont"]
            centred = c is not None and abs((u["o"].x0 + u["o"].x1) / 2 - (c.x0 + c.x1) / 2) < max(2.0, c.width * 0.06)
            u["align"] = "center" if centred or u["vert"] else "left"

    text_boxes = {id(u): u["o"] for u in units}
    untouched = [fitz.Rect(l["bbox"]) for i, l in lines.items() if i not in used]

    # 1. remove Japanese drawn as shapes, then the text itself (graphics and images are left alone)
    for u in units:
        if "outline" in u:
            page.add_redact_annot(u["o"] + (-0.3, -0.3, 0.3, 0.3), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
    for u in units:
        for i in u.get("ids", []):
            if lines[i]["vert"]:
                for s in lines[i]["spans"]:
                    page.add_redact_annot(fitz.Rect(s) + (-0.6, -0.6, 0.6, 0.6), fill=False)
                continue
            for s in lines[i]["spans"]:
                r = fitz.Rect(s)
                page.add_redact_annot(fitz.Rect(r.x0 + 0.2, r.y0 + 0.2, r.x1 - 0.2, r.y1 - 0.2), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)

    archive = fitz.Archive()
    css_base = fitz.css_for_pymupdf_font("notos", archive=archive, name="en")
    fit = Fitter(css_base, archive)

    # 2. room available to each unit, and the largest size that fits there
    live = [u for u in units if u["en"]]
    for u in live:
        others = [b for k, b in text_boxes.items() if k != id(u)] + untouched
        blockers = others + [r for r in images if not contains(r, u["o"], 2.5)]
        cont = u["cont"]
        for r in shapes + hairlines:
            if contains(r, u["o"], 2.5):
                continue
            if cont is not None and not r.intersects(cont):
                continue
            if area(r & u["o"]) > 0.2 * area(u["o"]):
                continue
            blockers.append(r)
        a = allowed_region(u["o"], cont, blockers, page_rect, u["align"], u["vert"])
        u["a"] = a
        u["w"], u["h"] = (a.height, a.width) if u["vert"] else (a.width, a.height)
        u["fit"] = fit.best_size(u, u["w"], u["h"])

    # 3. one size per style group (same original size, colour, weight, direction)
    groups = {}
    for u in live:
        groups.setdefault((round(u["size"], 1), u["color"], u["weight"], u["vert"]), []).append(u)
    for key, members in groups.items():
        fits = [u["fit"] for u in members if u["fit"] is not None]
        if not fits:
            for u in members:
                u["final"] = min(FLOOR, u["size"])
            continue
        med = statistics.median(fits)
        common = min(f for f in fits if f >= med * 0.8)
        for u in members:
            u["final"] = min(FLOOR, u["size"]) if u["fit"] is None else (common if u["fit"] >= med * 0.8 else u["fit"])

    # 4. place
    report = {"units": [], "too_long": [], "min_size": None}
    placed = []
    for u in live:
        a, o, size = u["a"], u["o"], u["final"]
        need = fit.measure(u, size, u["w"], u["h"])
        overflow = need is None
        if overflow:
            report["too_long"].append({"en": u["en"], "size": size, "box": [round(v, 1) for v in a]})
            grow = 0
            while need is None and grow < 60:
                grow += 3
                need = fit.measure(u, size, u["w"] + (grow if u["align"] != "center" else 2 * grow), u["h"] + grow)
            if u["vert"]:
                a = fitz.Rect(a.x0 - grow / 2, a.y0 - grow, a.x1 + grow / 2, a.y1 + grow)
            elif u["align"] == "center":
                a = fitz.Rect(a.x0 - grow, a.y0 - grow / 2, a.x1 + grow, a.y1 + grow / 2)
            elif u["align"] == "right":
                a = fitz.Rect(a.x0 - grow, a.y0, a.x1, a.y1 + grow)
            else:
                a = fitz.Rect(a.x0, a.y0, a.x1 + grow, a.y1 + grow)
            need = need if need is not None else u["h"] + grow
        if u["vert"]:
            cx = (o.x0 + o.x1) / 2
            x0 = min(max(cx - need / 2, a.x0), a.x1 - need)
            rect = fitz.Rect(x0, a.y0, x0 + need + 0.4, a.y1)
        else:
            if u["align"] == "center" or u.get("valign") == "middle":
                cy = (o.y0 + o.y1) / 2
                y0 = min(max(cy - need / 2, a.y0), a.y1 - need)
            else:
                y0 = min(max(o.y0 - 0.6, a.y0), a.y1 - need)
            rect = fitz.Rect(a.x0, y0, a.x1, y0 + need + 0.4)
        spare, scale = page.insert_htmlbox(rect, u["body"], css=fit.css(u, size), scale_low=0.9,
                                           rotate=90 if u["vert"] else 0, archive=archive)
        eff = size * scale
        placed.append((u, rect, eff))
        report["units"].append({"en": u["en"][:60], "orig_size": u["size"], "size": round(eff, 2), "align": u["align"],
                                "box": [round(v, 1) for v in rect]})
    report["min_size"] = min(e for _, _, e in placed)
    report["below_floor"] = [x["en"] for x in report["units"] if x["size"] < min(FLOOR, x["orig_size"]) - 0.05]

    # 5. checks on the finished page
    out = extract(page)
    report["japanese_left"] = [l["text"] for l in out if CJK.search(l["text"])]
    report["outlined_text_left"] = find_outlined_text(page)
    boxes = [(fitz.Rect(l["bbox"]), l["text"]) for l in out]
    clashes = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            inter = boxes[i][0] & boxes[j][0]
            if inter.width > 1.2 and inter.height > 1.2 and area(inter) > 0.5 * min(area(boxes[i][0]), area(boxes[j][0])):
                clashes.append([boxes[i][1][:40], boxes[j][1][:40]])
    report["text_overlaps"] = clashes

    doc.select([pageno])
    doc.subset_fonts()
    doc.save(out_pdf, garbage=3, deflate=True)
    fitz.open(out_pdf)[0].get_pixmap(dpi=150).save(out_png)
    json.dump(report, open(report_file, "w"), ensure_ascii=False, indent=1)
    sizes = sorted(x["size"] for x in report["units"])
    print("units placed:", len(placed), "| smallest size: %.2f pt" % sizes[0], "| under 5.5pt:", sum(s < 5.5 for s in sizes),
          "| too long for their box:", len(report["too_long"]), "| Japanese left:", report["japanese_left"],
          "| shape-drawn text left:", len(report["outlined_text_left"]), "| overlaps:", len(clashes))


if __name__ == "__main__":
    if sys.argv[1] == "extract":
        _, _, src, pageno, out = sys.argv
        doc, page = open_page(src, int(pageno))
        lines = extract(page)
        json.dump(lines, open(out, "w"), ensure_ascii=False)
        print(len(lines), "lines;", "shape-drawn text candidates:", find_outlined_text(page))
    else:
        _, _, src, pageno, lines_file, en_file, out_pdf, out_png, report_file = sys.argv
        apply(src, int(pageno), lines_file, en_file, out_pdf, out_png, report_file)
