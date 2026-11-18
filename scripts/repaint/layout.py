"""Step 3 of the brochure repaint: set the translated units into the page.

    .venv/bin/python scripts/repaint/layout.py PAGE_DIR [--out OUT.pdf]

Reads PAGE_DIR/page.json and PAGE_DIR/units.json (and the source PDF named in page.json),
writes english.pdf (or OUT.pdf), english.png (150 dpi) and placements.json into PAGE_DIR.
`layout_page(page_dir, out_pdf=None)` does the same and returns the placements dict.

Everything is derived from the page's geometry and the units' roles and segments; there are
no per-page settings. See README notes at the end of this file for the rules.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

import fitz
import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import extract            # noqa: E402
import geometry as G      # noqa: E402
import typeset as T       # noqa: E402

REPO = HERE.parents[1]
FLOOR = 5.5
LABELISH = {"label", "table_header", "table_cell", "caption", "heading", "subheading"}
FIXED = {"heading", "subheading", "warning"}
FIXED_MIN = 0.85          # a fixed role that cannot fit its box may go down to 85% of its size
DENSE = {"label", "table_cell", "table_header", "caption"}
SMALL_FLOOR = 4.5         # dense labels that overflow at FLOOR may go down to this
WINDOW = 150.0            # how far (pt) beyond its original box a unit may ever grow


# ----------------------------------------------------------------------------- loading

def load_units(page_dir):
    try:
        import units as U   # the shared loader, when present
        return U.load_units(page_dir)
    except (ImportError, AttributeError):
        pass
    p = Path(page_dir) / "units.json"
    if not p.exists():
        raise ValueError("%s has no units.json" % page_dir)
    return json.loads(p.read_text(encoding="utf-8"))


def source_path(model, page_dir):
    src = Path(model["source"])
    for cand in (src, Path.cwd() / src, REPO / src, Path(page_dir) / src):
        if cand.exists():
            return cand
    raise ValueError("source PDF %s not found" % model["source"])


def line_style(l):
    sizes = {}
    for s in l["spans"]:
        n = sum(1 for ch in s["text"] if ch not in extract.IGNORE_FOR_STYLE)
        sizes[s["size"]] = sizes.get(s["size"], 0) + n
    size = max(sizes, key=lambda k: (sizes[k], k)) if sizes and max(sizes.values()) > 0 else l["size"]
    return T.Style(size, l["color"], l["weight"] >= 600)


def resolve_style(ref, lines):
    try:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
            return line_style(lines[int(ref)])
        if isinstance(ref, str) and ":" in ref:
            a, b = ref.split(":")
            s = lines[int(a)]["spans"][int(b)]
            return T.Style(s["size"], s["color"], s["weight"] >= 600)
    except (KeyError, IndexError, ValueError):
        return None
    return None


def rgb_int(fill):
    r, g, b = [int(round(max(0, min(1, c)) * 255)) for c in fill[:3]]
    return (r << 16) | (g << 8) | b


def union(rects):
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= fitz.Rect(x)
    return r


def area(r):
    r = fitz.Rect(r)
    return 0.0 if r.is_empty else r.width * r.height


def spread(vals):
    return max(vals) - min(vals) if vals else 0.0


# ----------------------------------------------------------------------------- units

class Unit:
    def __init__(self, raw, model, lines):
        self.raw = raw
        self.id = raw.get("id", "?")
        self.role = raw.get("role", "body")
        self.notes = []
        self.line_ids = []
        if raw.get("lines"):
            self.kind = "lines"
            self.line_ids = [int(i) for i in raw["lines"] if int(i) in lines]
            ls = [lines[i] for i in self.line_ids]
            self.O = union([l["bbox"] for l in ls])
            self.vert = all(l["vert"] for l in ls)
            self.rows = self._rows(ls)
            self.orig_sizes = [s["size"] for l in ls for s in l["spans"]]
            self.ja_text = "".join(l["text"] for l in ls)
            self.source = {"lines": self.line_ids}
        elif raw.get("outlined") is not None:
            self.kind = "outlined"
            ob = {o["id"]: o for o in model.get("outlined", [])}.get(raw["outlined"])
            if ob is None:
                raise ValueError("unit %s: outlined id %s not in page.json" % (self.id, raw["outlined"]))
            self.O = fitz.Rect(ob["bbox"])
            self.vert = False
            self.rows = [fitz.Rect(self.O)]
            self.geo_size = round(self.O.height / 0.88, 2)
            self.orig_sizes = [self.geo_size]
            self.fill = rgb_int(ob["fill"])
            self.ja_text = raw.get("ja", "")
            self.source = {"outlined": raw["outlined"]}
        elif raw.get("region"):
            self.kind = "region"
            self.O = fitz.Rect(raw["region"])
            self.vert = self.O.height > 2.5 * self.O.width
            self.rows = [fitz.Rect(self.O)]
            self.orig_sizes = []
            self.ja_text = raw.get("ja", "")
            self.source = {"region": [round(v, 2) for v in self.O]}
        else:
            raise ValueError("unit %s has no source" % self.id)
        self.paras = {}
        for key in ("en", "en_short"):
            segs = raw.get(key)
            if not segs:
                continue
            styles = []
            for seg in segs:
                st = resolve_style(seg.get("style"), lines)
                if st is None:
                    fallback = lines[self.line_ids[0]] if self.line_ids else None
                    st = line_style(fallback) if fallback else T.Style(8.0, 0, False)
                    self.notes.append("style %r not found; used the unit's first line" % (seg.get("style"),))
                if self.kind == "outlined":
                    st = T.Style(self.geo_size, self.fill, st.bold if seg.get("style") is not None else True)
                styles.append(st)
            para = T.Para(segs, styles)
            if para.text.strip():
                self.paras["short" if key == "en_short" else "en"] = para
        if self.kind == "region" and "en" in self.paras:
            self.orig_sizes = [st.size for st in self.paras["en"].styles]
        if not self.orig_sizes:
            self.orig_sizes = [8.0]
        p = self.paras.get("en")
        if p:
            counts = {}
            for ch, si in p.chars:
                counts[si] = counts.get(si, 0) + 1
            self.primary = p.styles[max(counts, key=counts.get)]
        else:
            self.primary = T.Style(max(self.orig_sizes), 0, False)
        self.size = self.primary.size
        self.fixed = self.role in FIXED
        self.floor = FLOOR
        self.fix_min = FIXED_MIN   # raised by analyse_units when the text it introduces is close in size
        # line pitch of the original, used for English leading
        if len(self.rows) >= 2 and not self.vert:
            ys = sorted(r.y0 for r in self.rows)
            pitch = statistics.median([b - a for a, b in zip(ys, ys[1:])]) / max(self.size, 1)
            self.lg = min(1.32, max(1.15, pitch))
        else:
            self.lg = T.LINE_GAP

    def restyle(self, size, color, bold, lines):
        """Outlined and region units: the look comes from the drawing itself (size from the glyph
        row height, colour from the fill or ink); a segment's style, when given, only sets weight."""
        self.geo_size = size
        self.orig_sizes = [size]
        for key in ("en", "en_short"):
            segs = self.raw.get(key)
            if not segs:
                continue
            styles = []
            for seg in segs:
                st = resolve_style(seg.get("style"), lines) if seg.get("style") is not None else None
                styles.append(T.Style(size, color, st.bold if st else bold))
            para = T.Para(segs, styles)
            if para.text.strip():
                self.paras["short" if key == "en_short" else "en"] = para
        if "en" in self.paras:
            self.primary = self.paras["en"].styles[0]
            self.size = size
        self.notes = [n for n in self.notes if not n.startswith("style")]

    def _rows(self, ls):
        rows = []
        key = (lambda l: (l["bbox"][0], l["bbox"][2])) if all(l["vert"] for l in ls) else (lambda l: (l["bbox"][1], l["bbox"][3]))
        for l in sorted(ls, key=lambda l: key(l)[0]):
            a, b = key(l)
            for r in rows:
                ra, rb = (r.x0, r.x1) if ls[0]["vert"] else (r.y0, r.y1)
                if min(b, rb) - max(a, ra) > 0.5 * min(b - a, rb - ra):
                    r |= fitz.Rect(l["bbox"])
                    break
            else:
                rows.append(fitz.Rect(l["bbox"]))
        return rows

    def floor_of(self, para):
        return lambda i: min(self.floor, para.styles[i].size)

    def s_min(self, para):
        return min(min(self.floor, st.size) / st.size for st in para.styles)


# ----------------------------------------------------------------------------- page context

class Ctx:
    pass


def build_context(src, page_index, model, data, page_dir):
    ctx = Ctx()
    ctx.model = model
    ctx.lines = {l["id"]: l for l in model["lines"]}
    ctx.units = []
    errors = []
    for raw in data.get("units", []):
        try:
            u = Unit(raw, model, ctx.lines)
        except ValueError as e:
            errors.append(str(e))
            continue
        ctx.units.append(u)
    ctx.keep = [int(i) for i in data.get("keep", []) if int(i) in ctx.lines]
    ctx.drop = [int(i) for i in data.get("drop", []) if int(i) in ctx.lines]
    used = {i for u in ctx.units for i in u.line_ids} | set(ctx.keep) | set(ctx.drop)
    ctx.unassigned = [i for i in ctx.lines if i not in used]
    ctx.keep += ctx.unassigned            # never remove text nobody translated
    ctx.errors = errors

    # graphics-only rendering: all text removed, outlined units removed, region patches applied
    gdoc, gpage = extract.open_page(src, page_index)
    ctx.glyph_shapes = glyph_shapes(gpage, removed_lines(ctx))
    remove_glyph_shapes(gpage, ctx.glyph_shapes)
    outlined = [u for u in ctx.units if u.kind == "outlined"]
    if outlined:
        for u in outlined:
            gpage.add_redact_annot(u.O + (-0.3, -0.3, 0.3, 0.3), fill=False)
        gpage.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                               text=fitz.PDF_REDACT_TEXT_NONE)
    for l in model["lines"]:
        for s in l["spans"]:
            gpage.add_redact_annot(fitz.Rect(s["bbox"]) + (-0.3, -0.3, 0.3, 0.3), fill=False)
    gpage.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    ctx.pg = G.PageGraphics(gpage)
    odoc, opage = extract.open_page(src, page_index)
    ctx.orig_img = G.render(opage, G.S)
    ctx.page_rect = fitz.Rect(gpage.rect)
    ctx.images = [fitz.Rect(i["bbox"]) for i in model.get("images", [])]
    # graphics that are part of the page's content (not bleeding off the edge, not a backdrop)
    pr = fitz.Rect(gpage.rect)
    ctx.graphics = []
    for d in gpage.get_drawings():
        r = fitz.Rect(d["rect"])
        if r.is_empty and (r.width < 0.1 and r.height < 0.1):
            continue
        if r.x0 <= pr.x0 + 2 or r.y0 <= pr.y0 + 2 or r.x1 >= pr.x1 - 2 or r.y1 >= pr.y1 - 2:
            continue
        if r.width * r.height > 0.5 * pr.width * pr.height:
            continue
        ctx.graphics.append(r)
    for r in ctx.images:
        if r.x0 > pr.x0 + 2 and r.y0 > pr.y0 + 2 and r.x1 < pr.x1 - 2 and r.y1 < pr.y1 - 2:
            ctx.graphics.append(fitz.Rect(r))
    for u in ctx.units:
        if u.kind in ("outlined", "region"):
            ring = ctx.pg.inside_colour(u.O) if u.kind == "region" else None
            size, colour, bold = ink_style(ctx, u, ring)
            u.restyle(size, colour, bold, ctx.lines)
        if u.kind == "region":
            # the patch takes the region's own dominant colour (a badge stays a badge); the ring
            # around it is used only when the region is mostly ink
            inside = ctx.pg.inside_colour(u.O)
            u.patch = inside
            r0, r1, c0, c1 = G.rect_px(u.O, ctx.pg.img.shape)
            ctx.pg.img[r0:r1, c0:c1] = u.patch
    ctx.pg.low = ctx.pg.img[::G.LOW, ::G.LOW]
    return ctx


def ink_style(ctx, u, ring):
    """Size, colour and weight of text drawn as shapes or inside an image, read from the pixels:
    rows of ink give the glyph height (CJK glyphs fill ~0.88 em), the ink's dominant colour (or
    the outline fill) the colour, and the share of ink in the glyph rows the weight."""
    img = ctx.orig_img
    r0, r1, c0, c1 = G.rect_px(u.O, img.shape)
    crop = img[r0:r1, c0:c1]
    if crop.size == 0:
        return max(5.0, u.O.height * 0.8), 0, True
    bg = ring if ring is not None else ctx.pg.seed(u.O)
    ink = G.colour_dist(crop, bg) > 60
    rows = ink.any(axis=1)
    runs, start = [], None
    for i, v in enumerate(list(rows) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    # merge runs split by small gaps (strokes of one glyph row)
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < 0.12 * (b - a + merged[-1][1] - merged[-1][0]):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    merged = [m for m in merged if (m[1] - m[0]) / G.S >= 2.0]
    if merged:
        h = statistics.median([(b - a) / G.S for a, b in merged])
        size = round(min(40.0, max(5.0, h / 0.88)), 2)
        cover = sum(ink[a:b].sum() for a, b in merged) / max(1, sum((b - a) * ink.shape[1] for a, b in merged))
        cols = ink.any(axis=0)
        width = max(1, cols.sum())
        cover = sum(ink[a:b].sum() for a, b in merged) / max(1, sum((b - a) for a, b in merged) * width)
    else:
        size, cover = round(max(5.0, u.O.height * 0.8), 2), 0.3
    if u.kind == "outlined":
        colour = u.fill
    else:
        px = crop[ink]
        if px.size:
            q = (px // 16).astype(np.int32)
            keys = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
            vals, counts = np.unique(keys, return_counts=True)
            sel = px[keys == vals[np.argmax(counts)]].mean(axis=0).round().astype(int)
            colour = (int(sel[0]) << 16) | (int(sel[1]) << 8) | int(sel[2])
        else:
            colour = 0
    if cover >= 0.34:
        bold = True
    elif cover <= 0.2:
        bold = False
    else:   # undecided: the nearest text line's weight
        cx, cy = (u.O.x0 + u.O.x1) / 2, (u.O.y0 + u.O.y1) / 2
        near = min(ctx.lines.values(), default=None,
                   key=lambda l: abs((l["bbox"][0] + l["bbox"][2]) / 2 - cx) + abs((l["bbox"][1] + l["bbox"][3]) / 2 - cy))
        bold = bool(near and near["weight"] >= 600)
    return size, colour, bold


def ink_box(ctx, rect, exclude=()):
    """Visible ink of original text inside rect (original render against the text-free render),
    ignoring pixels inside the `exclude` rects (other text overlapping the box)."""
    pg = ctx.pg
    r0, r1, c0, c1 = G.rect_px(fitz.Rect(rect), pg.img.shape)
    diff = np.abs(ctx.orig_img[r0:r1, c0:c1].astype(np.int16) - pg.img[r0:r1, c0:c1].astype(np.int16)).max(axis=2) > 40
    if exclude:
        ob = fitz.Rect(c0 / G.S, r0 / G.S, c1 / G.S, r1 / G.S)
        for e in exclude:
            G.mask_rect(diff, ob, e, False)
    ys, xs = np.nonzero(diff)
    if not ys.size:
        return fitz.Rect(rect)
    return fitz.Rect(c0 / G.S + xs.min() / G.S, r0 / G.S + ys.min() / G.S,
                     c0 / G.S + (xs.max() + 1) / G.S, r0 / G.S + (ys.max() + 1) / G.S) & fitz.Rect(rect)


def analyse_units(ctx):
    """Container, alignment and anchoring of every unit, from the graphics raster."""
    pg = ctx.pg
    # kept text is an obstacle by its ink (a ▲'s box is three times the triangle)
    keepset = set(ctx.keep)
    ctx.keep_ink = {}
    for i in ctx.keep:
        kb = fitz.Rect(ctx.lines[i]["bbox"])
        others = [fitz.Rect(sp["bbox"]) for l in ctx.lines.values() if l["id"] not in keepset and kb.intersects(fitz.Rect(l["bbox"]))
                  for sp in l["spans"]]
        ctx.keep_ink[i] = ink_box(ctx, kb, others)
    all_lines = list(ctx.lines.values())
    for u in ctx.units:
        u.seed = pg.seed(u.O)
        low = pg.low_region(u.seed, u.O)
        u.win = pg.window(u.O, WINDOW)
        # an image that contains the text and is not a page background acts as its box
        page_area = area(ctx.page_rect)
        # images that carry the text (one, or adjacent ones the text runs across) act as its box
        hits = [im for im in ctx.images if area(im & u.O) > 0.05 * area(u.O) and area(im) < 0.5 * page_area]
        u.clip = None
        if hits:
            ub = union(hits)
            if (ub + (-1, -1, 1, 1)).contains(u.O) and area(ub) < 0.5 * page_area:
                u.clip = ub
        if u.clip is not None:
            low = {"area_frac": 0.0, "bbox": fitz.Rect(u.clip) & low["bbox"] if low["bbox"].intersects(u.clip) else fitz.Rect(u.clip),
                   "touches_edge": False}
        u.lowbox = fitz.Rect(low["bbox"])      # the container as a whole-page component (window independent)
        u.open = low["area_frac"] > 0.22
        u.bleed = low["touches_edge"] and not u.open
        R = pg.region_mask(u.seed, u.O, u.win, u.clip)
        Rf = ndimage.binary_fill_holes(R)
        G.mask_rect(Rf, u.win, u.O, True)
        cx, cy = (u.O.x0 + u.O.x1) / 2, (u.O.y0 + u.O.y1) / 2
        u.cext_x = None if u.open else G.run_extent(Rf, u.win, cy, cx)
        u.cext_y = None if u.open else G.col_extent(Rf, u.win, cx, cy)
        ys, xs = np.nonzero(R)
        if u.open or ys.size == 0:
            u.cbox = fitz.Rect(low["bbox"]) if not u.open else fitz.Rect(ctx.page_rect)
        else:
            u.cbox = fitz.Rect(u.win.x0 + xs.min() / G.S, u.win.y0 + ys.min() / G.S,
                               u.win.x0 + (xs.max() + 1) / G.S, u.win.y0 + (ys.max() + 1) / G.S)
            # a region cut by the analysis window: report the whole-page component's box
            if low["bbox"].contains(u.cbox) and (u.cbox.width >= u.win.width - 1 or u.cbox.height >= u.win.height - 1):
                u.cbox = fitz.Rect(low["bbox"])
        if u.open:
            u.ckind = "page"
        elif u.clip is not None or any(im.contains(u.cbox + (2, 2, -2, -2)) for im in ctx.images):
            u.ckind = "image"
        elif u.role.startswith("table"):
            u.ckind = "cell"
        else:
            u.ckind = "shape"
        # visible ink of the original text (a full-width bracket's box is half empty)
        r0, r1, c0, c1 = G.rect_px(u.O, pg.img.shape)
        diff = np.abs(ctx.orig_img[r0:r1, c0:c1].astype(np.int16) - pg.img[r0:r1, c0:c1].astype(np.int16)).max(axis=2) > 40
        ys, xs = np.nonzero(diff)
        if u.kind == "lines" and ys.size:
            u.O_ink = fitz.Rect(c0 / G.S + xs.min() / G.S, r0 / G.S + ys.min() / G.S,
                                c0 / G.S + (xs.max() + 1) / G.S, r0 / G.S + (ys.max() + 1) / G.S) & u.O
        else:
            u.O_ink = fitz.Rect(u.O)
        u.B = fitz.Rect(u.O_ink) if not u.O_ink.is_empty else fitz.Rect(u.O)
        u.align, u.ax = infer_align(u, all_lines)
        u.valign, u.ay = infer_valign(u)
        if u.valign == "middle" and not u.vert:
            # several texts stacked in one container (items in a cell, lines in a panel): each is top-anchored
            for v in ctx.units:
                if v is u:
                    continue
                c = fitz.Point((v.O.x0 + v.O.x1) / 2, (v.O.y0 + v.O.y1) / 2)
                if u.cbox.contains(c) and (v.O.y1 <= u.O.y0 + 0.5 or v.O.y0 >= u.O.y1 - 0.5) and \
                        min(v.O.x1, u.O.x1) - max(v.O.x0, u.O.x0) > 0:
                    u.valign, u.ay = "top", u.O.y0
                    break
        if not u.vert:
            if u.align == "left" and abs(u.ax - u.O.x0) < 0.01:
                u.ax = u.O_ink.x0
            elif u.align == "right" and abs(u.ax - u.O.x1) < 0.01:
                u.ax = u.O_ink.x1
            elif u.align == "center" and abs(u.ax - (u.O.x0 + u.O.x1) / 2) < 0.01:
                u.ax = (u.O_ink.x0 + u.O_ink.x1) / 2
            if u.valign == "top":
                u.ay = u.O_ink.y0

    # content margins: where the translated text sits, excluding panels that bleed off the page
    # and the graphics that are not bleeds
    body = [u.O for u in ctx.units if not u.bleed] or [u.O for u in ctx.units]
    ctx.content = union(body + ctx.graphics) if body else fitz.Rect(ctx.page_rect)
    ctx.gutter = None
    W, H = ctx.page_rect.width, ctx.page_rect.height
    if W > 1.2 * H:
        c = W / 2
        boxes = [u.O for u in ctx.units if not u.bleed]
        if boxes and not any(b.x0 < c < b.x1 for b in boxes):
            left = [b.x1 for b in boxes if b.x1 <= c]
            right = [b.x0 for b in boxes if b.x0 >= c]
            if left and right:
                ctx.gutter = fitz.Rect(max(left), ctx.page_rect.y0, min(right), ctx.page_rect.y1)


def infer_align(u, all_lines):
    """left / center / right from the original geometry; returns (align, anchor_x)."""
    O = u.O
    cx = (O.x0 + O.x1) / 2
    if u.vert:
        if u.cext_x and abs((O.x0 - u.cext_x[0]) - (u.cext_x[1] - O.x1)) <= max(1.5, 0.3 * (u.cext_x[1] - u.cext_x[0] - O.width)):
            return "center", sum(u.cext_x) / 2
        return "center", cx
    tol = max(1.0, 0.15 * u.size)
    centred_in_container = False
    if u.cext_x:
        cl, cr = u.cext_x
        gl, gr = O.x0 - cl, cr - O.x1
        slack = gl + gr
        centred_in_container = abs(gl - gr) <= max(1.5, 0.2 * slack)
    if len(u.rows) >= 2:
        L = [r.x0 for r in u.rows]
        C = [(r.x0 + r.x1) / 2 for r in u.rows]
        R = [r.x1 for r in u.rows]
        mark = u.ja_text[:1] in T.MARK_CHARS or u.ja_text[:1] in "（(①②③④⑤⑥⑦⑧⑨"
        hanging = mark and spread(L[1:]) <= tol and L[1] > L[0] + tol
        if hanging:
            return "left", O.x0
        tol_c = max(1.5, 0.5 * u.size)    # Japanese trailing punctuation shifts a line's centre
        if spread(L) <= tol:
            if spread(C) <= tol and centred_in_container and u.role in LABELISH:
                return "center", sum(u.cext_x) / 2
            return "left", O.x0
        if spread(C) <= tol_c and spread(C) < 0.5 * spread(L):
            return "center", (sum(u.cext_x) / 2) if centred_in_container else cx
        if spread(R) <= tol:
            return "right", O.x1
        return "left", O.x0
    # single line: what does it share with neighbouring lines in the same container?
    votes = {"left": 0, "center": 0, "right": 0}
    own = set(u.line_ids)
    for l in all_lines:
        if l["id"] in own or l["vert"]:
            continue
        b = fitz.Rect(l["bbox"])
        vgap = max(b.y0 - O.y1, O.y0 - b.y1, 0)
        if vgap > 3 * u.size:
            continue
        if not u.open and not u.cbox.contains(fitz.Point((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)):
            continue
        if abs(b.x0 - O.x0) <= 0.9:
            votes["left"] += 1
        if abs((b.x0 + b.x1) / 2 - cx) <= 0.9:
            votes["center"] += 1
        if abs(b.x1 - O.x1) <= 0.9:
            votes["right"] += 1
    top = max(votes.values())
    if u.cext_x:
        cl, cr = u.cext_x
        gl, gr = O.x0 - cl, cr - O.x1
        slack = gl + gr
        geo = "left"
        if slack < 1.5 * u.size or centred_in_container:
            geo = "center"
        elif gr < 0.35 * gl and gr < 2.5 * u.size:
            geo = "right"
        best = geo if (top == 0 or votes[geo] == top) else max(votes, key=lambda k: (votes[k], k == "left"))
        if best == "center":
            return "center", (cl + cr) / 2 if centred_in_container or slack < 1.5 * u.size else cx
        return best, O.x0 if best == "left" else O.x1
    best = max(votes, key=lambda k: (votes[k], k == "left"))
    if votes[best] == 0:
        best = "left"
    return best, {"left": O.x0, "center": cx, "right": O.x1}[best]


def infer_valign(u):
    O = u.O
    cy = (O.y0 + O.y1) / 2
    if u.vert:
        if u.cext_y:
            ct, cb = u.cext_y
            if abs((O.y0 - ct) - (cb - O.y1)) <= max(1.5, 0.25 * (cb - ct - O.height)):
                return "middle", (ct + cb) / 2
        return "middle", cy
    if u.cext_y:
        ct, cb = u.cext_y
        gt, gb = O.y0 - ct, cb - O.y1
        slack = gt + gb
        if slack < 1.2 * u.size or abs(gt - gb) <= max(1.5, 0.25 * slack):
            return "middle", (ct + cb) / 2
    return "top", O.y0


# ----------------------------------------------------------------------------- free space

def build_grid(ctx, u, placed, ignore=()):
    """Blocked mask for unit u: outside its container, outside the content margins, other text
    (placed English or original boxes), with u's own original box cleared."""
    win = u.win
    R = ctx.pg.region_mask(u.seed, u.O, win, u.clip)
    blocked = ~R
    content = fitz.Rect(ctx.content) | u.O
    outside = np.ones_like(blocked)
    G.mask_rect(outside, win, content, False)
    blocked |= outside
    if ctx.gutter is not None and not (u.O.x0 < ctx.gutter.x1 and u.O.x1 > ctx.gutter.x0):
        G.mask_rect(blocked, win, ctx.gutter, True)
    gap = max(1.5, 0.3 * u.size)
    inline = []
    O = u.B                     # the original text's visible extent: always available
    # graphics inside that box but away from the Japanese glyphs (a badge, a brace's curl, a
    # pill's border) are flowed around; what lay under the glyphs (a shadow, a tint) is not
    i0, i1, j0, j1 = G.rect_px(O, ctx.pg.img.shape)
    w0, _, wc0, _ = G.rect_px(win, ctx.pg.img.shape)
    inner = (~R)[max(0, i0 - w0):max(0, i1 - w0), max(0, j0 - wc0):max(0, j1 - wc0)].copy()
    if inner.size:
        ob = fitz.Rect(j0 / G.S, i0 / G.S, j1 / G.S, i1 / G.S)
        near = np.zeros_like(inner)
        for i in u.line_ids:
            for sp in ctx.lines[i]["spans"]:
                G.mask_rect(near, ob, fitz.Rect(sp["bbox"]) + (-1.5, -1.5, 1.5, 1.5), True)
        if not u.line_ids:
            near[:] = True
        rest = inner & ~near
        lab, nlab = ndimage.label(rest)
        for k, sl in enumerate(ndimage.find_objects(lab), 1):
            if sl is None or (lab[sl] == k).sum() < 6:
                continue
            r = fitz.Rect(ob.x0 + sl[1].start / G.S, ob.y0 + sl[0].start / G.S,
                          ob.x0 + sl[1].stop / G.S, ob.y0 + sl[0].stop / G.S)
            inline.append(r + (-0.8, -0.8, 0.8, 0.8))
    hard = []
    soft = np.zeros_like(blocked)          # original boxes of units not placed yet (they can move)
    for v in ctx.units:
        if v is u or v.id in ignore:
            continue
        if v.id in placed:
            hard.extend(placed[v.id]["ink_lines"])
            continue
        r = v.B
        if area(r & u.O) > 0.15 * min(area(r), area(u.O)):
            inline.append(r + (-gap, -gap, gap, gap))
        else:
            G.mask_rect(soft, win, r + (-gap, -gap, gap, gap), True)
    edge_keeps = []
    for i in ctx.keep:
        r = ctx.keep_ink[i]
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if u.O.intersects(r) and u.O.contains(c):
            inline.append(r + (-gap, -0.5, gap, 0.5))
        else:
            # kept text outside, or only grazing the box (a marker above a label): a hard obstacle
            G.mask_rect(blocked, win, r + (-gap, -gap, gap, gap), True)
            if O.intersects(r + (-gap, -gap, gap, gap)):
                edge_keeps.append(r)
    G.mask_rect(blocked, win, O, False)
    G.mask_rect(soft, win, O, False)
    for r in edge_keeps:
        G.mask_rect(blocked, win, r + (-gap, -gap, gap, gap), True)
    # anchor box: pushed below English already placed over the original box, or clear of a kept
    # mark that grazes it
    A = fitz.Rect(O)
    for r in edge_keeps:
        rr = r + (-gap, -gap, gap, gap)
        if not u.vert and rr.intersects(A):
            if (r.y0 + r.y1) / 2 < (A.y0 + A.y1) / 2:
                A = A + (0, rr.y1 + 0.5 - A.y0, 0, rr.y1 + 0.5 - A.y0)
            else:
                A = A + (0, rr.y0 - 0.5 - A.y1, 0, rr.y0 - 0.5 - A.y1)
    for r in hard:
        rr = r + (-gap, -gap, gap, gap)
        if rr.intersects(A) and min(rr.x1, A.x1) - max(rr.x0, A.x0) > 0.5:
            if u.vert:
                continue
            dy = rr.y1 + 0.5 - A.y0
            if dy > 0:
                A = A + (0, dy, 0, dy)
    pmask = np.zeros_like(blocked)
    for r in hard:
        G.mask_rect(pmask, win, r + (-gap, -gap, gap, gap), True)
    grid = G.Grid(blocked | soft | pmask, win)
    grid.hard = blocked
    grid.soft = soft
    grid.placed = pmask
    return grid, A, inline


def ext_ok(grid, F, A, padx, pady):
    Gr = fitz.Rect(F)
    if F.x0 < A.x0 - 0.01:
        Gr.x0 -= padx
    if F.x1 > A.x1 + 0.01:
        Gr.x1 += padx
    if F.y0 < A.y0 - 0.01:
        Gr.y0 -= pady
    if F.y1 > A.y1 + 0.01:
        Gr.y1 += pady
    return grid.count(Gr) == 0


def bsearch(f, lo, hi, it=12):
    """Largest v in [lo, hi] with f(v), assuming f(lo) and monotone."""
    if hi <= lo or f(hi):
        return max(lo, hi)
    for _ in range(it):
        mid = (lo + hi) / 2
        if f(mid):
            lo = mid
        else:
            hi = mid
    return lo


def frames(grid, A, align, valign, ax, ay, size, nsteps=7):
    """Candidate rectangles (in the grid's space) that contain A and are free."""
    padx = min(4.0, max(1.5, 0.4 * size))
    pady = min(3.0, max(1.0, 0.25 * size))
    ok = lambda F: ext_ok(grid, F, A, padx, pady)
    if not ok(A):
        return []
    grow_x = WINDOW - 2
    out = []
    # horizontal room on the anchor rows
    if align == "left":
        xmax = bsearch(lambda x: ok(fitz.Rect(A.x0, A.y0, x, A.y1)), A.x1, A.x1 + grow_x)
        xr = [(A.x0, A.x1 + (xmax - A.x1) * k / nsteps) for k in range(nsteps + 1)]
    elif align == "right":
        xmin = A.x0 - bsearch(lambda d: ok(fitz.Rect(A.x0 - d, A.y0, A.x1, A.y1)), 0, grow_x)
        xr = [(A.x0 - (A.x0 - xmin) * k / nsteps, A.x1) for k in range(nsteps + 1)]
    else:
        h0 = max(ax - A.x0, A.x1 - ax)
        if not ok(fitz.Rect(ax - h0, A.y0, ax + h0, A.y1)):
            ax = (A.x0 + A.x1) / 2
            h0 = A.width / 2
        hmax = bsearch(lambda h: ok(fitz.Rect(ax - h, A.y0, ax + h, A.y1)), h0, h0 + grow_x)
        xr = [(ax - (h0 + (hmax - h0) * k / nsteps), ax + (h0 + (hmax - h0) * k / nsteps)) for k in range(nsteps + 1)]
    grow_y = max(4 * A.height, 14 * size)
    seen = set()
    for x0, x1 in xr:
        if valign == "top":
            y1 = bsearch(lambda y: ok(fitz.Rect(x0, A.y0, x1, y)), A.y1, A.y1 + grow_y)
            up = bsearch(lambda d: ok(fitz.Rect(x0, A.y0 - d, x1, y1)), 0, 0.6 * size, 6)
            F = fitz.Rect(x0, A.y0 - up, x1, y1)
            fy = ay
        else:
            c = ay
            h0 = max(c - A.y0, A.y1 - c)
            if not ok(fitz.Rect(x0, c - h0, x1, c + h0)):
                c = (A.y0 + A.y1) / 2
                h0 = A.height / 2
            h = bsearch(lambda h: ok(fitz.Rect(x0, c - h, x1, c + h)), h0, h0 + grow_y / 2)
            F = fitz.Rect(x0, c - h, x1, c + h)
            fy = c
        key = tuple(round(v, 1) for v in F)
        if key in seen:
            continue
        seen.add(key)
        out.append({"F": F, "ax": ax, "ay": fy})
    return out


def candidates(ctx, u, grid, A, inline):
    """All candidate placements: horizontal, and for vertical units also rotated along the bar."""
    cands = []
    align, valign = u.align, u.valign
    ax, ay = u.ax, u.ay
    if A != u.B:   # pushed down: anchor moves with the box
        ay = ay + (A.y0 - u.B.y0)
    for f in frames(grid, A, align, valign, ax, ay, u.size):
        F = f["F"]
        obst = [r for r in inline if r.intersects(F)]
        lf = fitz.Rect(F)
        # the text starts where the original ink started, not at the (padded) box edge
        if align == "left" and F.x0 <= f["ax"] < F.x1 - 1:
            lf.x0 = f["ax"]
        elif align == "right" and F.x0 + 1 < f["ax"] <= F.x1:
            lf.x1 = f["ax"]
        cands.append({"orient": "h", "page_frame": F, "frame": lf, "align": align, "valign": valign,
                      "ax": f["ax"], "ay": f["ay"], "obst": obst, "pen": 0.0, "A": (A.width, A.height),
                      "target": 2 if u.vert else None})
    if u.vert or (u.kind == "region" and u.O.height > 2.5 * u.O.width):
        gt = grid.transposed()
        At = fitz.Rect(A.y0, A.x0, A.y1, A.x1)
        for f in frames(gt, At, "center", "middle", ay, ax, u.size):
            Ft = f["F"]
            Fp = fitz.Rect(Ft.y0, Ft.x0, Ft.y1, Ft.x1)
            local = fitz.Rect(0, 0, Fp.height, Fp.width)
            lax = Fp.y1 - f["ax"]
            lay = f["ay"] - Fp.x0
            obst = [fitz.Rect(Fp.y1 - r.y1, r.x0 - Fp.x0, Fp.y1 - r.y0, r.x1 - Fp.x0) for r in inline if r.intersects(Fp)]
            cands.append({"orient": "r", "page_frame": Fp, "frame": local, "align": "center", "valign": "middle",
                          "ax": lax, "ay": lay, "obst": obst, "pen": 3.0, "A": (A.height, A.width), "target": None})
    return cands


def to_page(c, r):
    """Local rect -> page rect for a candidate."""
    if c["orient"] == "h":
        return fitz.Rect(r)
    Fp = c["page_frame"]
    return fitz.Rect(Fp.x0 + r.y0, Fp.y1 - r.x1, Fp.x0 + r.y1, Fp.y1 - r.x0)


# ----------------------------------------------------------------------------- fitting

def cost(u, c, blk, para):
    size = max(blk.sizes) if blk.sizes else u.size
    pen = c["pen"]
    target = c["target"]
    if para.structured:
        target = len(para.breaks) + 1
    elif target is None and u.role in LABELISH:
        target = len(u.rows)
    if target:
        pen += max(0, blk.nlines - target) * (4.0 if u.role in ("heading", "subheading") else 6.0)
    if blk.bracket_broken:
        pen += 15
    if blk.word_split:
        pen += 25
    Aw, Ah = c["A"]
    ew = max(0.0, blk.ink.width - Aw) / size
    eh = max(0.0, blk.ink.height - Ah) / size
    if u.role in LABELISH:
        pen += 0.15 * ew + 0.6 * eh
    else:
        pen += 0.6 * ew + 0.4 * eh
    return pen


def attempt(u, para, s, cands, lg, strict=True, keep_lines=False):
    best = None
    floor = u.floor_of(para)
    kl = keep_lines and para.structured and u.role in LABELISH
    for c in cands:
        blk = T.set_para(para, s, floor, c["frame"], c["align"], c["ax"], c["ay"], c["valign"], lg, c["obst"], strict,
                         keep_lines=kl)
        if not blk.ok:
            continue
        k = cost(u, c, blk, para)
        if best is None or k < best[0]:
            best = (k, c, blk)
    return best


def shift_block(blk, dy):
    for ln in blk.lines:
        ln["top"] += dy
        ln["base"] += dy
    blk.ink = blk.ink + (0, dy, 0, dy)


def harm_of(grid, r, pad):
    """How bad it is to put ink at page rect r: English already placed counts most (text over text is the worst outcome), graphics and
    margins fully, text that has not been placed yet (and can still move) a little, and leaving
    the analysis window is prohibitive."""
    if not hasattr(grid, "_hii"):
        grid._hii = G.Grid(grid.hard, grid.win)
        grid._pii = G.Grid(grid.placed, grid.win)
        grid._sii = G.Grid(grid.soft, grid.win)
    rr = r + (-pad, -pad, pad, pad)
    h = grid._hii.count(rr)
    if h >= 10 ** 7:
        return 10 ** 7 + area(r)
    return h + 50 * grid._pii.count(rr) + 0.15 * grid._sii.count(rr)


def overflow_layout(u, para, s, cands, lg, grid):
    """Keep the size; let the text run out of its frame where it does least harm: downward,
    upward, centred, or wider than the frame on one line."""
    best = None
    floor = u.floor_of(para)
    pad = min(2.0, 0.25 * u.size)
    for c in cands:
        F0 = c["frame"]
        for wf in (1.0, 1.3, 1.7, 2.3):
            extra = F0.width * (wf - 1)
            if c["align"] == "left":
                F = fitz.Rect(F0.x0, F0.y0, F0.x1 + extra, F0.y1)
            elif c["align"] == "right":
                F = fitz.Rect(F0.x0 - extra, F0.y0, F0.x1, F0.y1)
            else:
                F = fitz.Rect(F0.x0 - extra / 2, F0.y0, F0.x1 + extra / 2, F0.y1)
            big = fitz.Rect(F.x0, F.y0, F.x1, F.y0 + 5000)
            blk = T.set_para(para, s, floor, big, c["align"], c["ax"], F.y0, "top", lg, c["obst"], strict=False,
                             allow_overflow=True)
            if not blk.lines:
                continue
            h = blk.ink.height
            want = c["ay"] if c["valign"] == "top" else c["ay"] - h / 2
            options = [want - blk.ink.y0, F0.y0 - blk.ink.y0, (F0.y1 - h) - blk.ink.y0,
                       ((F0.y0 + F0.y1) / 2 - h / 2) - blk.ink.y0]
            for dy in options:
                pr = to_page(c, blk.ink + (0, dy, 0, dy))
                # running far out of the box is worse than running a little out along a long edge
                cb = u.cbox
                out = max(cb.x0 - pr.x0, pr.x1 - cb.x1, cb.y0 - pr.y0, pr.y1 - cb.y1, 0.0)
                k = (round(harm_of(grid, pr, pad) + 10 * (G.S * out) ** 2, 1), cost(u, c, blk, para))
                if best is None or k < best[0]:
                    best = (k, c, blk, dy)
    if best is None:
        return None
    k, c, blk, dy = best
    shift_block(blk, dy)
    return (k[1], c, blk)


SHORT_PENALTY = 2.5


def solve(u, ctx, placed, s_cap, exact=False, ignore=()):
    """Choose variant, size and frame for unit u.

    Order for shrinkable roles: full English at s_cap keeping a label's line structure (en, or
    en_short when the full text needs extra lines); a structured label slightly smaller with its
    structure intact; free wrapping at s_cap; shrinking towards the floor; breaking a bracketed
    phrase or long word; overflow. Headings and warnings never go below s_cap = 1.
    With exact=True only s_cap is tried and None is returned when nothing fits."""
    grid, A, inline = build_grid(ctx, u, placed, ignore)
    cands = candidates(ctx, u, grid, A, inline)
    variants = [(k, u.paras[k]) for k in ("en", "short") if k in u.paras]
    lgs = [u.lg] + ([1.1] if u.lg > 1.13 else [])
    flags = []

    def best_at(s, keep_lines, names=None, strict=True):
        found = None
        for name, para in variants:
            if names and name not in names:
                continue
            for lg in lgs:
                r = attempt(u, para, s, cands, lg, strict, keep_lines)
                if r:
                    k = r[0] + (SHORT_PENALTY if name == "short" else 0) + (0.5 if lg != lgs[0] else 0)
                    if found is None or k < found[0]:
                        found = (k, (name, s, lg, r))
                    break
        return found[1] if found else None

    def shrink(lo, hi, keep_lines):
        name, para = variants[-1]
        lg = lgs[-1]
        lo = max(lo, u.s_min(para))
        lo0 = lo
        if lo >= hi or not attempt(u, para, lo, cands, lg, True, keep_lines):
            return None
        for _ in range(8):
            mid = (lo + hi) / 2
            if attempt(u, para, mid, cands, lg, True, keep_lines):
                lo = mid
            else:
                hi = mid
        prim = max(st.size for st in para.styles)
        s = max(lo0, np.floor(prim * lo / 0.05 + 1e-6) * 0.05 / prim)
        r = attempt(u, para, s, cands, lg, True, keep_lines)
        if not r:
            s, r = lo, attempt(u, para, lo, cands, lg, True, keep_lines)
        return (name, s, lg, r)

    structured = any(p.structured for _, p in variants) and u.role in LABELISH
    res = best_at(s_cap, keep_lines=True)
    if res is None and structured and not u.fixed and not exact:
        res = shrink(0.85 * s_cap, s_cap, True)
    if res is None:
        res = best_at(s_cap, keep_lines=False)
    # a heading or warning that does not fit at its size: down to 85% (never below the floor, nor
    # below the text it introduces), largest size that fits
    fix_lo = s_cap * u.fix_min
    if res is None and u.fixed:
        res = shrink(fix_lo, s_cap, False)
    if res is None and exact:
        return None
    if res is None and not u.fixed:
        res = shrink(0.0, s_cap, False)
    if res is None:
        name, para = variants[-1]
        s = max(fix_lo, u.s_min(para)) if u.fixed else u.s_min(para)
        r = attempt(u, para, s, cands, lgs[-1], strict=False)
        if r:
            res = (name, s, lgs[-1], r)
            flags.append("relaxed_breaking")
    # a dense label that still overflows at the floor: down to 4.5pt (after breaking a
    # bracket or long word at the normal floor has failed too)
    if res is None and u.role in DENSE:
        u.floor = SMALL_FLOOR
        res = shrink(0.0, s_cap, False)
        if res is None:
            u.floor = FLOOR
        else:
            flags.append("small_type")
    if res is None:
        name, para = variants[-1]
        s = max(fix_lo, u.s_min(para)) if u.fixed else u.s_min(para)
        if not cands:
            # no free room at all (e.g. pushed into other content): use the original box as frame
            cands = [{"orient": "h", "page_frame": fitz.Rect(A), "frame": fitz.Rect(A), "align": u.align,
                      "valign": u.valign, "ax": u.ax if u.align == "center" else (A.x0 if u.align == "left" else A.x1),
                      "ay": A.y0 if u.valign == "top" else (A.y0 + A.y1) / 2, "obst": inline, "pen": 0,
                      "A": (A.width, A.height), "target": None}]
        r = overflow_layout(u, para, s, cands, lgs[-1], grid)
        res = (name, s, lgs[-1], r)
        flags.append("overflow")
    name, s, lg, (k, c, blk) = res
    u.floor = FLOOR
    if u.fixed and s < s_cap - 1e-3 and any(z < st.size - 1e-3 for z, st in zip(blk.sizes, u.paras[name].styles)):
        flags.append("fixed_role_shrunk")
    if blk.bracket_broken and "relaxed_breaking" not in flags and u.paras[name].structured:
        flags.append("bracket_broken")
    if blk.word_split:
        flags.append("word_split")
    para = u.paras[name]
    target = len(para.breaks) + 1 if para.structured else None
    if target and blk.nlines > target and u.role in LABELISH:
        flags.append("extra_line_breaks")
    ink = to_page(c, blk.ink)
    ink_lines = [to_page(c, fitz.Rect(ln["x0"], ln["base"] - T.INK_UP * ln["size"], ln["x1"],
                                     ln["base"] + T.INK_DOWN * ln["size"])) for ln in blk.lines]
    return {"ink_lines": ink_lines, "variant": name, "scale": s, "lg": lg, "cand": c, "blk": blk, "para": para, "ink": ink,
            "flags": flags, "pushed": A != u.B, "eff_primary": max(blk.sizes) if blk.sizes else u.size,
            "s_cap": s_cap}


# ----------------------------------------------------------------------------- flows

def find_flows(units):
    """Chains of top-anchored horizontal units stacked in one column of one container with nothing
    between them (paragraphs, bullets, a heading and its bullets, items in a table cell). A flow
    is sized and placed together so that a longer English unit pushes the next one down."""
    nxt = {}
    taken = set()
    hor = [u for u in units if not u.vert and u.valign == "top"]
    for u in sorted(hor, key=lambda u: u.O.y0):
        best = None
        for v in hor:
            if v is u or v.id in taken:
                continue
            if not (u.open == v.open and (u.open or abs(u.lowbox.x0 - v.lowbox.x0) + abs(u.lowbox.y0 - v.lowbox.y0)
                                          + abs(u.lowbox.x1 - v.lowbox.x1) + abs(u.lowbox.y1 - v.lowbox.y1) < 6)):
                continue
            gap = v.O.y0 - u.O.y1
            if gap < -0.3 * u.size or gap > 1.6 * max(u.size, v.size):
                continue
            ov = min(u.O.x1, v.O.x1) - max(u.O.x0, v.O.x0)
            if ov < 0.5 * min(u.O.width, v.O.width):
                continue
            if abs(u.O.x0 - v.O.x0) > 3 * u.size and abs(u.O_ink.x0 - v.O_ink.x0) > 3 * u.size:
                continue
            between = [w for w in units if w is not u and w is not v and w.O.y0 >= u.O.y1 - 0.5 and w.O.y1 <= v.O.y0 + 0.5
                       and min(w.O.x1, v.O.x1) - max(w.O.x0, v.O.x0) > 0]
            if between:
                continue
            if best is None or v.O.y0 < best.O.y0:
                best = v
        if best is not None:
            nxt[u.id] = best
            taken.add(best.id)
    flows = []
    heads = [u for u in hor if u.id in nxt and u.id not in taken]
    for h in heads:
        chain = [h]
        while chain[-1].id in nxt:
            chain.append(nxt[chain[-1].id])
        flows.append(chain)
    return flows


def place_flow(ctx, members, placed, caps):
    """Find the largest common scale at which every member of the flow fits in turn (each pushing
    the next down). Fixed roles stay at 1. Returns {id: result} or None."""
    def run(s):
        trial = dict(placed)
        out = {}
        for i, m in enumerate(members):
            sc = 1.0 if m.fixed else max(min(s, caps[m.id]), m.s_min(m.paras.get("short") or m.paras["en"]))
            later = {x.id for x in members[i + 1:]}
            r = solve(m, ctx, trial, sc, exact=True, ignore=later)
            if r is None:
                return None
            trial[m.id] = r
            out[m.id] = r
        return out
    top = max(caps[m.id] for m in members)
    res = run(top)
    if res:
        return res
    lo = min(m.s_min(m.paras.get("short") or m.paras["en"]) for m in members)
    best = run(lo)
    if best is None:
        return None
    hi = top
    for _ in range(7):
        mid = (lo + hi) / 2
        r = run(mid)
        if r:
            lo, best = mid, r
        else:
            hi = mid
    return best


def limit_fixed_shrink(units, flows):
    """A heading may shrink to 85% only while it stays larger than the text it introduces: the
    members after it in its flow (up to the next heading), or else the nearest unit right below
    it in the same container."""
    for u in units:
        if not u.fixed:
            continue
        intro = []
        flow = next((f for f in flows if u in f), None)
        if flow:
            for v in flow[flow.index(u) + 1:]:
                if v.fixed:
                    break
                intro.append(v)
        else:
            below = [v for v in units if v is not u and not v.fixed and v.O.y0 >= u.O.y1 - 0.5
                     and v.O.y0 - u.O.y1 < 2 * u.size and min(v.O.x1, u.O.x1) - max(v.O.x0, u.O.x0) > 0
                     and u.open == v.open and (u.open or sum(abs(a - b) for a, b in zip(u.lowbox, v.lowbox)) < 6)]
            if below:
                intro.append(min(below, key=lambda v: v.O.y0))
        sizes = [v.size for v in intro if v.size < u.size]
        if sizes:
            u.fix_min = max(FIXED_MIN, min(1.0, (max(sizes) + 0.05) / u.size))


# ----------------------------------------------------------------------------- siblings

def sibling_groups(units):
    """Units of the same role and original style that sit in one row or column near each other."""
    parent = {u.id: u.id for u in units}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def key(u):
        p = u.primary
        return (u.role, round(p.size, 1), p.color, p.bold, u.vert)

    for i, a in enumerate(units):
        for b in units[i + 1:]:
            if key(a) != key(b):
                continue
            A, B = a.O, b.O
            sz = a.size
            row = abs((A.y0 + A.y1) / 2 - (B.y0 + B.y1) / 2) < 0.6 * sz
            col = min(abs(A.x0 - B.x0), abs((A.x0 + A.x1) / 2 - (B.x0 + B.x1) / 2), abs(A.x1 - B.x1),
                      min(abs(ra.x0 - rb.x0) for ra in a.rows for rb in b.rows)) < 1.5
            dist = max(B.x0 - A.x1, A.x0 - B.x1, B.y0 - A.y1, A.y0 - B.y1, 0)
            if (row or col) and dist < 25 * sz:
                parent[find(a.id)] = find(b.id)
    groups = {}
    for u in units:
        groups.setdefault(find(u.id), []).append(u)
    out = {}
    n = 0
    for members in groups.values():
        if len(members) > 1:
            n += 1
            for u in members:
                out[u.id] = "g%d" % n
    return out


# ----------------------------------------------------------------------------- main

def glyph_shapes(page, lines):
    """Shapes that copy a text line's glyphs (a heading's white or coloured outline drawn as
    filled paths under the text): one complex filled path per character, about the size of the
    type, lying on the line. Only lines where such a copy covers a fair share of the characters
    count, so a lone icon on a line is left alone. Returns rects to remove."""
    out = []
    drawings = [d for d in page.get_drawings() if d.get("fill") is not None and len(d["items"]) >= 12]
    for l in lines:
        b = fitz.Rect(l["bbox"])
        s = l["size"]
        e = b + (-0.4 * s, -0.4 * s, 0.4 * s, 0.4 * s)
        hits = []
        for d in drawings:
            r = fitz.Rect(d["rect"])
            if not (0.3 * s <= r.width <= 1.5 * s and 0.3 * s <= r.height <= 1.5 * s):
                continue
            c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if e.contains(r) and b.contains(c):
                hits.append(r)
        nch = sum(1 for ch in l["text"] if ch.strip() and ch not in extract.IGNORE_FOR_STYLE)
        if len(hits) >= max(2, 0.4 * nch):
            out.extend(hits)
    return out


def remove_glyph_shapes(page, rects):
    # MuPDF keeps a path that one redaction rectangle overlaps without covering, even when another
    # rectangle covers it; neighbouring glyphs overlap, so redact in passes of disjoint rectangles
    passes = []
    for r in rects:
        r = r + (-0.2, -0.2, 0.2, 0.2)
        for p in passes:
            if not any(r.intersects(q) for q in p):
                p.append(r)
                break
        else:
            passes.append([r])
    for p in passes:
        for r in p:
            page.add_redact_annot(r, fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_NONE)


def removed_lines(ctx):
    ids = {i for u in ctx.units for i in u.line_ids} | set(ctx.drop)
    return [ctx.lines[i] for i in sorted(ids)]


def remove_japanese(page, ctx):
    remove_glyph_shapes(page, glyph_shapes(page, removed_lines(ctx)))
    outlined = [u for u in ctx.units if u.kind == "outlined"]
    if outlined:
        for u in outlined:
            page.add_redact_annot(u.O + (-0.3, -0.3, 0.3, 0.3), fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_NONE)
    ids = [i for u in ctx.units for i in u.line_ids] + ctx.drop
    # character boxes of the page, by span: removing character by character keeps any kept text
    # (a page badge, a number) that sits inside the box of a Japanese span
    allchars = []
    d = page.get_text("rawdict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    for b in d["blocks"]:
        for l in b.get("lines", []):
            for sp in l["spans"]:
                for c in sp["chars"]:
                    if c["c"].strip():
                        allchars.append((fitz.Rect(c["bbox"]), c["c"]))
    # assign each character to the page.json span that holds it (the smallest span containing its
    # centre whose text has that character), so a badge inside a Japanese span stays untouched
    spans = [(fitz.Rect(sp["bbox"]) + (-0.5, -0.5, 0.5, 0.5), sp["text"], l["id"]) for l in ctx.lines.values()
             for sp in l["spans"]]
    # extraction v2: second copies of a line drawn in the same place go with the line they copy
    # (a copy without "of" goes with any removed line it overlaps)
    removed = {i for u in ctx.units for i in u.line_ids} | set(ctx.drop)
    for dup in ctx.model.get("duplicates", []):
        of = dup.get("of")
        if of is None:
            db = fitz.Rect(dup["bbox"])
            of = next((i for i in sorted(removed) if fitz.Rect(ctx.lines[i]["bbox"]).intersects(db)), None)
            if of is None:
                continue
        for b in dup.get("spans", []):
            spans.append((fitz.Rect(b) + (-0.5, -0.5, 0.5, 0.5), dup.get("text", ""), of))
    owner = {}
    for k, (r, ch) in enumerate(allchars):
        p = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        cands = [(area(sb), lid) for sb, txt, lid in spans if ch in txt and sb.contains(p)]
        if cands:
            owner[k] = min(cands)[1]
    targets = set(ids)
    keep_set = set(ctx.keep)
    keep_chars = [allchars[k][0] for k, lid in owner.items() if lid in keep_set]
    n = 0
    by_line = {}
    for k, lid in owner.items():
        by_line.setdefault(lid, []).append(allchars[k][0])
    for i in ids:
        l = ctx.lines[i]
        for s in l["spans"][:1]:
            boxes = by_line.get(i) or [fitz.Rect(sp["bbox"]) for sp in l["spans"]]
            for r in boxes:
                if l["vert"]:
                    r = r + (-0.6, -0.6, 0.6, 0.6)
                else:
                    dx, dy = min(0.2, r.width / 4), min(0.2, r.height / 4)
                    r = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 - dx, r.y1 - dy)
                # a redaction removes every glyph its rectangle touches: keep clear of kept glyphs
                for kb in keep_chars:
                    if not r.intersects(kb):
                        continue
                    opts = [fitz.Rect(r.x0, r.y0, r.x1, kb.y0 - 0.05), fitz.Rect(r.x0, kb.y1 + 0.05, r.x1, r.y1),
                            fitz.Rect(r.x0, r.y0, kb.x0 - 0.05, r.y1), fitz.Rect(kb.x1 + 0.05, r.y0, r.x1, r.y1)]
                    opts = [o for o in opts if o.width > 0.2 and o.height > 0.2]
                    if not opts:
                        r = None
                        break
                    r = max(opts, key=area)
                if r is None:
                    continue
                page.add_redact_annot(r, fill=False)
                n += 1
    if n:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    for u in ctx.units:
        if u.kind == "region":
            page.draw_rect(u.O, color=None, fill=tuple(v / 255 for v in u.patch), overlay=True)


def layout_page(page_dir, out_pdf=None, png=True):
    page_dir = Path(page_dir)
    model = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
    data = load_units(page_dir)
    src = source_path(model, page_dir)
    page_index = model["page_index"]
    doc, page = extract.open_page(src, page_index)
    # the units refer to line ids: refuse to run on a different extraction
    try:
        cur = extract.text_lines(page, version=model.get("extract_version", 1))
    except TypeError:
        cur = extract.text_lines(page)
    if len(cur) != len(model["lines"]) or any(a["text"] != b["text"] for a, b in zip(cur, model["lines"])):
        raise ValueError("page.json no longer matches %s page %d; re-run extract.py" % (src, page_index))

    ctx = build_context(src, page_index, model, data, page_dir)
    analyse_units(ctx)
    live = [u for u in ctx.units if u.paras.get("en")]
    flows = find_flows(live)
    limit_fixed_shrink(live, flows)

    # pass 1: each unit on its own, against the original boxes of the others
    best = {}
    pass1_small = set()
    for u in live:
        r = solve(u, ctx, {}, 1.0)
        best[u.id] = r["eff_primary"] if "overflow" not in r["flags"] else u.s_min(r["para"]) * u.size
        if "small_type" in r["flags"]:
            pass1_small.add(u.id)
    groups = sibling_groups(live)
    gsize, small = {}, {}
    for u in live:
        g = groups.get(u.id)
        if g and not u.fixed:
            # a sibling set in small type does not drag the others below the normal floor;
            # the reduced size is shared only among the siblings that needed it
            gsize[g] = min(gsize.get(g, 1e9), max(best[u.id], min(FLOOR, u.size)))
            if u.id in pass1_small:
                small[g] = min(small.get(g, 1e9), best[u.id])

    # pass 2: place in reading order, each unit seeing the English already placed;
    # stacked units (flows) are sized together
    caps = {}
    for u in live:
        g = groups.get(u.id)
        caps[u.id] = min(1.0, gsize[g] / u.size) if (g and not u.fixed) else 1.0
        if g in small and u.id in pass1_small:
            caps[u.id] = min(caps[u.id], small[g] / u.size)
    head = {f[0].id: f for f in flows}
    in_flow = {m.id: f[0].id for f in flows for m in f}
    placed = {}
    order = sorted(live, key=lambda u: (round(u.O.y0, 1), u.O.x0))
    for u in order:
        if u.id in placed:
            continue
        if u.id in head:
            res = place_flow(ctx, head[u.id], placed, caps)
            if res:
                for m in head[u.id]:
                    res[m.id]["flow"] = "f%d" % (flows.index(head[u.id]) + 1)
                placed.update(res)
                continue
        if u.id in in_flow and u.id not in head:
            pass   # a flow that could not be sized jointly: its members are placed one by one
        r = solve(u, ctx, placed, caps[u.id])
        if not u.fixed and r["scale"] < caps[u.id] - 0.005 and "overflow" not in r["flags"] and groups.get(u.id):
            r["flags"].append("below_group_size")
        if u.id in in_flow:
            r["flow"] = "f%d" % (flows.index([f for f in flows if f[0].id == in_flow[u.id]][0]) + 1)
            r["flags"].append("flow_not_joint")
        placed[u.id] = r

    # draw
    remove_japanese(page, ctx)
    for u in order:
        r = placed[u.id]
        c = r["cand"]
        origin = fitz.Point(c["page_frame"].x0, c["page_frame"].y1) if c["orient"] == "r" else None
        T.draw_block(page, r["blk"], origin)

    out_pdf = Path(out_pdf) if out_pdf else page_dir / "english.pdf"
    doc.select([page_index])
    doc.subset_fonts()
    doc.save(str(out_pdf), garbage=3, deflate=True)
    if png:
        fitz.open(str(out_pdf))[0].get_pixmap(dpi=150).save(str(page_dir / "english.png"))
    placements = make_placements(ctx, placed, groups)
    (page_dir / "placements.json").write_text(json.dumps(placements, ensure_ascii=False, indent=1), encoding="utf-8")
    return placements


def rr(r):
    return [round(v, 2) for v in r]


def make_placements(ctx, placed, groups):
    out = {"content_margins": rr(ctx.content), "gutter": rr(ctx.gutter) if ctx.gutter else None,
           "units": [], "removed_only": list(ctx.drop), "kept": sorted(set(ctx.keep)),
           "unassigned_lines": ctx.unassigned, "errors": ctx.errors}
    for u in ctx.units:
        rec = {"id": u.id, "role": u.role, "source": u.source,
               "container": {"kind": u.ckind, "bbox": rr(u.cbox)},
               "orig_bbox": rr(u.O), "align": u.align, "valign": u.valign,
               "orig_size_max": round(max(u.orig_sizes), 2), "orig_size_min": round(min(u.orig_sizes), 2),
               "sibling_group": groups.get(u.id), "notes": list(u.notes)}
        r = placed.get(u.id)
        if r is None:
            rec.update({"allowed": None, "placed": None, "rotate": 0, "scale": None, "runs": [], "used_short": False,
                        "overflow": False})
            rec["notes"].append("no English given: removed only")
            out["units"].append(rec)
            continue
        c, para, blk = r["cand"], r["para"], r["blk"]
        runs = []
        segs = u.raw["en_short" if r["variant"] == "short" else "en"]
        for i, (seg, st) in enumerate(zip(segs, para.styles)):
            runs.append({"t": seg.get("t", ""), "size": round(blk.sizes[i], 2), "orig_size": round(st.size, 2),
                         "color": st.color, "weight": 700 if st.bold else 400})
        notes = rec["notes"]
        if r["pushed"]:
            notes.append("moved down below English placed above it")
        if para.replacements:
            notes.append("normalised characters: " + ", ".join("%s->%s" % p for p in sorted(set(para.replacements))))
        if para.missing:
            notes.append("characters with no glyph: " + "".join(sorted(set(para.missing))))
        if u.kind == "region":
            notes.append("raster text covered with a patch sampled from the surrounding colour")
        rec.update({
            "allowed": rr(r["cand"]["page_frame"]), "placed": rr(r["ink"]),
            "rotate": 90 if c["orient"] == "r" else 0,
            "align": c["align"] if c["orient"] == "h" else "center",
            "valign": c["valign"] if c["orient"] == "h" else "middle",
            "scale": round(r["scale"], 4), "line_gap": round(r["lg"], 3), "lines": blk.nlines,
            "runs": runs, "used_short": r["variant"] == "short",
            "overflow": "overflow" in r["flags"], "flags": r["flags"], "flow": r.get("flow"),
            "glyph_lines": [{"text": "".join(g.text for g in ln["pieces"]),
                             "box": rr(to_page(c, fitz.Rect(ln["x0"], ln["base"] - T.INK_UP * ln["size"], ln["x1"],
                                                            ln["base"] + T.INK_DOWN * ln["size"])))}
                            for ln in blk.lines],
        })
        out["units"].append(rec)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("page_dir")
    ap.add_argument("--out", help="output PDF (default PAGE_DIR/english.pdf)")
    a = ap.parse_args()
    try:
        p = layout_page(a.page_dir, a.out)
    except ValueError as e:
        print("layout failed: %s" % e, file=sys.stderr)
        return 2
    us = [u for u in p["units"] if u["placed"]]
    shrunk = [u["id"] for u in us if u["scale"] < 0.999]
    short = [u["id"] for u in us if u["used_short"]]
    over = [u["id"] for u in us if u["overflow"]]
    print("%s: %d units placed, %d shrunk %s, %d shortened %s, %d overflow %s, rotated %s" % (
        a.page_dir, len(us), len(shrunk), shrunk, len(short), short, len(over), over,
        [u["id"] for u in us if u["rotate"]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
