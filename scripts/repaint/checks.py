"""Step 4 of the brochure repaint: judge the English page against the original.

    .venv/bin/python scripts/repaint/checks.py PAGE_DIR [--pdf english.pdf]

Reads PAGE_DIR/page.json, units.json, english.pdf and (if present) placements.json, and writes
PAGE_DIR/checks.json, review.png (English page with flagged units outlined and numbered) and
compare.png (original | English at the same scale).

From Python:
    check_page(page_dir) -> dict        the same dict as checks.json

Every check works from the RENDERED English page: its pixels and its text layer.
placements.json is only used to know which rendered text belongs to which unit and what the
layout engine intended; nothing it claims is taken as true. Without placements.json the checks
fall back to matching rendered text to units by wording and position.
"""
import io
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import fitz
import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract                                   # noqa: E402
import checks_raster as R                        # noqa: E402
import checks_text as T                          # noqa: E402

STRICT_ROLES = {"warning", "heading", "subheading"}
WIDOW_ROLES = {"heading", "subheading", "label", "warning", "table_header", "caption"}
SIZE_FLOOR = 5.5
# Layout-engine flags (placements.json units[].flags) that select a looser size threshold. The size
# is always the one measured on the render; a flag never excuses a size it does not cover.
FIXED_ROLE_MIN = 0.845     # `fixed_role_shrunk`: warning/heading/subheading kept at >= 85% (0.5% tolerance) -> warn
SMALL_TYPE_MIN = 4.45      # `small_type`: label/table cell/table header/caption at >= 4.5pt (0.05 tolerance) -> warn
SMALL_TYPE_ROLES = {"label", "table_cell", "table_header", "caption"}
SEV_RANK = {"error": 3, "warn": 2, "info": 1}

# thresholds (points unless stated)
MIN_PIXELS = 4             # ink pixels (at 200 dpi) before a collision counts
OVERFLOW_DEPTH = 0.8       # solid ink this far outside the container
OVERFLOW_DEPTH_BOX = 2.5   # glyph boxes this far outside (catches invisible text, e.g. white on white)
PAD_TIGHT = 1.5            # clearance to the container edge that counts as tight
MARGIN_TOL = 1.0
EFFECT_DENSITY = 0.12      # share of edge pixels under the original glyphs that means "letters with a drawn outline"
EXEMPT_DILATE = 3          # px around the original Japanese ink that new ink may share with graphics


def fmt(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def hexcol(c):
    return "#%06x" % int(c)


def rgb(c):
    c = int(c)
    return ((c >> 16) & 255, (c >> 8) & 255, c & 255)


def lum(c):
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(c1, c2):
    a, b = lum(c1), lum(c2)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def snippet(s, n=40):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def area(r):
    """Area of a fitz.Rect (0 when empty or invalid); PyMuPDF 1.26 Rect has no get_area()."""
    r = fitz.Rect(r)
    return 0.0 if (r.is_empty or r.is_infinite) else abs(r.width * r.height)


def guard(F, name, fn, *args, uid=None, **kw):
    """Run one check; a crash becomes a page-level error naming the check instead of aborting the page."""
    try:
        return fn(*args, **kw)
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        F.add(None, "internal:" + name, "error",
              "The '%s' check failed to run%s (%s: %s); its results are missing, so review the page by eye." % (
                  name, " for unit %s" % uid if uid else "", type(e).__name__, e),
              where="%s:%d" % (Path(tb.filename).name, tb.lineno))
        return None


def rect_list(r):
    return [round(v, 1) for v in r] if r is not None else None


class Findings:
    def __init__(self):
        self.units = defaultdict(list)
        self.page = []

    def add(self, uid, check, severity, message, **evidence):
        f = {"check": check, "severity": severity, "message": message, "evidence": evidence}
        (self.page if uid is None else self.units[uid]).append(f)


# ====================================================================== loading

def _open_normalised(path, index=0):
    return extract.open_page(str(path), index)


def _open_output(path):
    """The English page as written. PyMuPDF coordinates are already relative to the crop box, so only
    a rotation is removed; re-applying set_mediabox to a page that was normalised once can shift it."""
    doc = fitz.open(str(path))
    page = doc[0]
    if page.rotation:
        page.remove_rotation()
    return doc, page


def load_context(page_dir, english_pdf=None):
    page_dir = Path(page_dir)
    pj = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
    up = page_dir / "units.json"
    uj = json.loads(up.read_text(encoding="utf-8")) if up.exists() else {"units": [], "keep": [], "drop": []}
    pp = page_dir / "placements.json"
    placements = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else None
    src = Path(pj["source"])
    if not src.is_absolute():
        root = Path(__file__).resolve().parents[2]
        src = root / src
    return page_dir, pj, uj, placements, src, Path(english_pdf) if english_pdf else page_dir / "english.pdf"


# ====================================================================== ink vs graphics

class Rasters:
    def __init__(self, src_full, src_gfx, out_full, out_gfx):
        self.src_full, self.src_gfx, self.out_full, self.out_gfx = src_full, src_gfx, out_full, out_gfx
        od = R.diff(out_full, out_gfx)
        self.out_ink, self.out_core = od > R.INK, od > R.CORE
        self.src_ink = R.diff(src_full, src_gfx) > R.INK
        self.gfx_changed = R.diff(src_gfx, out_gfx) > R.INK
        self.edges = R.edges(out_gfx)
        self.shape = out_full.shape


def unit_geometry(rs, glyphs, orig_boxes, orig_ink_boxes, zone, outlined_bbox=None, exempt=True):
    """Measure one unit's rendered ink against the graphics around it.

    glyphs: tight per-character boxes of the rendered text (points).
    orig_boxes: boxes where the original Japanese sat (seeds the container).
    orig_ink_boxes: boxes in which the original's ink is taken from the source render.
    Returns a dict of measurements (no judgements)."""
    allb = list(glyphs) + [zone]
    ux0 = min(b[0] for b in allb); uy0 = min(b[1] for b in allb)
    ux1 = max(b[2] for b in allb); uy1 = max(b[3] for b in allb)
    win = R.px((ux0, uy0, ux1, uy1), rs.shape, pad=30)
    sl = (slice(win[1], win[3]), slice(win[0], win[2]))
    G = R.box_mask(glyphs, rs.shape, win)
    ink = rs.out_ink[sl] & G
    core = rs.out_core[sl] & G
    # original Japanese ink
    oink = rs.src_ink[sl] & R.box_mask(orig_ink_boxes, rs.shape, win)
    if outlined_bbox is not None:
        oink |= rs.gfx_changed[sl] & R.box_mask([outlined_bbox], rs.shape, win, inset=-0.5)
    oink_dil = ndi.binary_dilation(oink, iterations=EXEMPT_DILATE) if (exempt and oink.any()) else np.zeros_like(oink)
    gfx = rs.out_gfx[sl]
    seed = R.box_mask(orig_boxes, rs.shape, win) & ~rs.edges[sl]
    parts = []
    for b in orig_boxes:
        x0, y0, x1, y1 = R.px(b, rs.shape)
        parts.append((max(0, y0 - win[1]), max(0, y1 - win[1]), max(0, x0 - win[0]), max(0, x1 - win[0])))
    Rm, bg = R.region(gfx, seed, parts=parts)
    m = {"win": win, "ink_px": int(ink.sum()), "ink_bbox": R.mask_bbox_pt(ink, win),
         "glyph_bbox": [round(ux, 2) for ux in (min(b[0] for b in glyphs), min(b[1] for b in glyphs),
                                                max(b[2] for b in glyphs), max(b[3] for b in glyphs))] if glyphs else None,
         "orig_ink_bbox": R.mask_bbox_pt(oink, win), "bg": [int(v) for v in bg] if bg is not None else None}
    # graphics tracing the original letters (outline or shadow effects drawn as shapes)
    ob_mask = R.box_mask(orig_boxes, rs.shape, win)
    m["effect_density"] = round(float(rs.edges[sl][ob_mask].mean()), 3) if ob_mask.any() else 0.0
    # collisions with strokes / edges that the original ink did not already share
    coll = core & rs.edges[sl] & ~oink_dil
    m["collision_px"] = int(coll.sum())
    m["collision_bbox"] = R.mask_bbox_pt(coll, win)
    if Rm is not None:
        rb = R.mask_bbox_pt(Rm, win)
        ys, xs = np.nonzero(Rm)
        H, W = Rm.shape
        m["container_bbox"] = rb
        m["container_open"] = {"left": bool(xs.min() == 0), "top": bool(ys.min() == 0),
                               "right": bool(xs.max() == W - 1), "bottom": bool(ys.max() == H - 1)}
        dout = R.dist_outside(Rm)
        outside = core & ~Rm & ~oink_dil
        m["outside_px"] = int(outside.sum())
        m["outside_depth"] = round(float(dout[outside].max()) / R.S, 2) if outside.any() else 0.0
        m["outside_bbox"] = R.mask_bbox_pt(outside & (dout > OVERFLOW_DEPTH * R.S), win)
        # glyphs with (almost) no visible ink, e.g. white text that left its box onto white paper
        invisible = []
        for g in glyphs:
            gx0, gy0, gx1, gy1 = R.px(g, rs.shape)
            if gx1 > gx0 and gy1 > gy0 and rs.out_ink[gy0:gy1, gx0:gx1].sum() < 3:
                invisible.append(g)
        Gi = R.box_mask(invisible, rs.shape, win, inset=0.6) if invisible else np.zeros_like(Rm)
        outG = Gi & ~Rm & ~oink_dil
        m["outside_box_depth"] = round(float(dout[outG].max()) / R.S, 2) if outG.any() else 0.0
        din = R.dist_inside(Rm)
        inR = core & Rm
        m["pad"] = round(float(din[inR].min()) / R.S, 2) if inR.any() else None
        oinR = oink & Rm
        m["orig_pad"] = round(float(din[oinR].min()) / R.S, 2) if oinR.any() else None
    else:
        m["container_bbox"] = None
    return m


# ====================================================================== main check

def check_page(page_dir, english_pdf=None, write=True):
    """Check one page folder; returns the checks.json dict. Never raises for a problem in a single
    check: that becomes a page-level error naming the check, and the outputs are still written."""
    state = {"t0": time.time()}
    try:
        return _check_page(page_dir, english_pdf, write, state)
    except Exception as e:  # noqa: BLE001
        import traceback
        F = state.get("F") or Findings()
        tb = traceback.extract_tb(e.__traceback__)[-1]
        F.add(None, "internal:" + state.get("stage", "setup"), "error",
              "Checking stopped early at the '%s' stage (%s: %s); the findings are incomplete, so review the page by eye." % (
                  state.get("stage", "setup"), type(e).__name__, e), where="%s:%d" % (Path(tb.filename).name, tb.lineno))
        return _finish(Path(page_dir), state.get("pj"), state.get("units", []), F, state.get("measured", {}), state.get("outputs"),
                       None, write, state["t0"], state.get("placements"), state.get("content"), page_dir_raw=page_dir)


def _check_page(page_dir, english_pdf, write, state):
    t0 = state["t0"]
    state["stage"] = "loading"
    page_dir, pj, uj, placements, src_path, out_path = load_context(page_dir, english_pdf)
    F = Findings()
    state.update(F=F, pj=pj, placements=placements)
    units = uj.get("units", [])
    state["units"] = units
    keep_ids, drop_ids = set(uj.get("keep", [])), set(uj.get("drop", []))
    pl_by_id = {p["id"]: p for p in (placements or {}).get("units", [])} if placements else {}
    lines_by_id = {l["id"]: l for l in pj["lines"]}
    outlined_by_id = {o["id"]: o for o in pj.get("outlined", [])}

    # ---------------- open both pages
    fitz.TOOLS.mupdf_warnings(reset=True)
    try:
        out_doc, out_page = _open_output(out_path)
    except Exception as e:  # noqa: BLE001
        F.add(None, "file_opens", "error", "The English PDF could not be opened (%s)." % e)
        return _finish(page_dir, pj, units, F, {}, None, None, write, t0, placements)
    warn_open = fitz.TOOLS.mupdf_warnings(reset=True)
    src_doc, src_page = _open_normalised(src_path, pj["page_index"])
    if len(out_doc) != 1:
        F.add(None, "file_opens", "warn", "The English PDF has %d pages; one was expected." % len(out_doc))
    if getattr(out_doc, "is_repaired", False) or warn_open:
        F.add(None, "file_opens", "warn", "The English PDF needed repair when opened.", warnings=str(warn_open)[:300])
    if abs(out_page.rect.width - src_page.rect.width) > 0.5 or abs(out_page.rect.height - src_page.rect.height) > 0.5:
        F.add(None, "page_size", "error", "Page size changed from %s x %s pt to %s x %s pt." % (
            fmt(src_page.rect.width), fmt(src_page.rect.height), fmt(out_page.rect.width), fmt(out_page.rect.height)))

    # ---------------- renders
    state["stage"] = "rendering"
    src_full = R.render(src_page)
    g_doc, g_page = _open_normalised(src_path, pj["page_index"])
    R.strip_text(g_page)
    src_gfx = R.render(g_page)
    out_full = R.render(out_page)
    o_doc, o_page = _open_output(out_path)
    R.strip_text(o_page)
    out_gfx = R.render(o_page)
    h = min(src_full.shape[0], out_full.shape[0]); w = min(src_full.shape[1], out_full.shape[1])
    src_full, src_gfx, out_full, out_gfx = (a[:h, :w] for a in (src_full, src_gfx, out_full, out_gfx))
    rs = Rasters(src_full, src_gfx, out_full, out_gfx)
    state["outputs"] = {"src_full": src_full, "out_full": out_full}
    state["stage"] = "text layer"

    # ---------------- text layers
    orig = T.original_lines(src_page, pj["lines"])
    out_lines = T.page_text_lines(out_page)
    remnant = {}          # out line index -> original line id it reproduces
    for i, ol in enumerate(out_lines):
        t = ol["text"].strip()
        r = fitz.Rect(ol["bbox"])
        for lid, l in lines_by_id.items():
            lt = l["text"].strip()
            if t and (t == lt or ((len(t) >= 2 or T.has_japanese(t)) and t in lt)) and (fitz.Rect(l["bbox"]) + (-1.5, -1.5, 1.5, 1.5)).contains(r):
                remnant[i] = lid
                break
    new_lines = [ol for i, ol in enumerate(out_lines) if i not in remnant]

    # zones of the original text per unit
    zones, orig_boxes, orig_ink_boxes, outl_bbox, orig_line_objs = {}, {}, {}, {}, {}
    for u in units:
        uid = u["id"]
        boxes, inkb, objs = [], [], []
        if u.get("lines"):
            for lid in u["lines"]:
                ol = orig.get(lid)
                if ol:
                    objs.append(ol)
                    boxes += [c["g"] for c in ol["chars"] if not c["c"].isspace()]
                    inkb.append(ol["bbox"])
                elif lid in lines_by_id:
                    boxes.append(lines_by_id[lid]["bbox"]); inkb.append(lines_by_id[lid]["bbox"])
        elif u.get("outlined") in outlined_by_id:
            b = outlined_by_id[u["outlined"]]["bbox"]
            boxes, outl_bbox[uid] = [b], b
        elif u.get("region"):
            boxes = [u["region"]]
        if not boxes:
            continue
        zones[uid] = fitz.Rect(min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        orig_boxes[uid], orig_ink_boxes[uid], orig_line_objs[uid] = boxes, inkb, objs

    assigned, unassigned = T.assign_lines(new_lines, units, zones, pl_by_id)

    # ---------------- content box from the original page
    content, bleed = derive_content_box(src_page, src_gfx, pj)
    if placements and placements.get("content_margins"):
        cm = placements["content_margins"]
        dev = max(abs(a - b) for a, b in zip(cm, content))
        if dev > 3:
            F.add(None, "margins", "info", "The layout engine's content margins differ from the ones measured on the original page by up to %spt." % fmt(dev),
                  measured=rect_list(content), placements=cm)

    state["content"] = content
    state["stage"] = "units"
    measured = {}
    state["measured"] = measured
    unit_meas = {}
    for u in units:
        uid = u["id"]
        lines = assigned.get(uid, [])
        chars, vlines = T.unit_chars(lines)
        ink_chars = [c for c in chars if not c["c"].isspace()]
        meas = {"sizes": sorted({round(c["size"], 2) for c in ink_chars}), "lines": len(vlines), "ink_bbox": None}
        measured[uid] = meas
        if not ink_chars:
            F.add(uid, "completeness", "error", "No English text was found on the page for this unit (%s)." % snippet(_seg_text(u.get("en")) or u.get("ja", "")))
            continue
        geo = None
        if uid in zones:
            geo = guard(F, "ink_vs_graphics", unit_geometry, rs, [c["g"] for c in ink_chars], orig_boxes[uid],
                        orig_ink_boxes[uid] or orig_boxes[uid], tuple(zones[uid]), outl_bbox.get(uid), uid=uid)
        if geo:
            meas["ink_bbox"] = geo["ink_bbox"] or geo["glyph_bbox"]
            meas["container_bbox"] = geo.get("container_bbox")
            meas["clearance"] = geo.get("pad")
        else:
            gb = [c["g"] for c in ink_chars]
            meas["ink_bbox"] = [round(min(b[0] for b in gb), 2), round(min(b[1] for b in gb), 2),
                                round(max(b[2] for b in gb), 2), round(max(b[3] for b in gb), 2)]
        unit_meas[uid] = {"chars": chars, "vlines": vlines, "geo": geo, "u": u}
        guard(F, "geometry", judge_geometry, F, u, geo, content, zones.get(uid), rs,
              in_bleed=uid in zones and in_mask(bleed, tuple(zones[uid])), uid=uid)
        size_override = None
        if (u.get("outlined") or u.get("region")) and uid in zones:
            size_override = guard(F, "original_style", measure_original_style, rs, u, zones[uid], outlined_by_id, uid=uid)
        guard(F, "text", judge_text, F, u, chars, vlines, lines_by_id, orig_line_objs.get(uid, []), geo, pl_by_id.get(uid), rs,
              size_override, uid=uid)

    guard(F, "text_overlap", judge_overlaps, F, unit_meas, out_lines, remnant, keep_ids, rs)
    guard(F, "siblings", judge_siblings, F, units, unit_meas, zones, lines_by_id, pl_by_id)
    guard(F, "hierarchy", judge_hierarchy, F, units, unit_meas, zones, lines_by_id, rs)
    guard(F, "columns", judge_columns, F, units, unit_meas, zones, lines_by_id)
    guard(F, "completeness", judge_completeness, F, units, uj, pj, out_lines, remnant, unassigned, lines_by_id, keep_ids, drop_ids)
    guard(F, "global", judge_global, F, src_doc, src_page, out_doc, out_page, units, outlined_by_id, rs, pj, zones)

    outputs = {"src_full": src_full, "out_full": out_full}
    return _finish(page_dir, pj, units, F, measured, outputs, unit_meas, write, t0, placements, content)


def _seg_text(segs):
    """Segments joined for word comparison: a space between segments never merges two words."""
    return " ".join(s.get("t", "") for s in (segs or []))


# ====================================================================== content margins

def derive_content_box(src_page, src_gfx, pj):
    """The extent of the original page's content, ignoring bleeds (bands and tabs touching the page
    edge), page numbers and full-page backgrounds."""
    W, H = src_page.rect.width, src_page.rect.height
    bleed = R.bleed_mask(src_gfx)

    def in_bleed(r):
        cx, cy = int((r[0] + r[2]) / 2 * R.S), int((r[1] + r[3]) / 2 * R.S)
        cx = min(max(cx, 0), bleed.shape[1] - 1); cy = min(max(cy, 0), bleed.shape[0] - 1)
        return bool(bleed[cy, cx])

    def at_edge(r, tol=2.0):
        return r[0] <= tol or r[1] <= tol or r[2] >= W - tol or r[3] >= H - tol

    items = []
    for l in pj["lines"]:
        b = l["bbox"]
        if re.fullmatch(r"\s*\d{1,3}\s*", l["text"]) and (b[1] > H - 45 or b[3] < 45):
            continue
        if in_bleed(b):
            continue
        items.append(b)
    for d in src_page.get_drawings():
        r = d["rect"]
        if r.width < 1 and r.height < 1:
            continue
        if r.width > 0.9 * W or r.height > 0.9 * H or at_edge(r) or in_bleed(r):
            continue
        items.append(tuple(r))
    for im in src_page.get_image_info():
        r = im["bbox"]
        if at_edge(r) or in_bleed(r):
            continue
        items.append(tuple(r))
    if not items:
        return [0, 0, W, H], bleed
    return [round(min(b[0] for b in items), 2), round(min(b[1] for b in items), 2),
            round(max(b[2] for b in items), 2), round(max(b[3] for b in items), 2)], bleed


def in_mask(mask, r):
    cx, cy = int((r[0] + r[2]) / 2 * R.S), int((r[1] + r[3]) / 2 * R.S)
    cx = min(max(cx, 0), mask.shape[1] - 1); cy = min(max(cy, 0), mask.shape[0] - 1)
    return bool(mask[cy, cx])


# ====================================================================== judgements: geometry

def judge_geometry(F, u, geo, content, zone, rs, in_bleed=False):
    if geo is None:
        return
    uid = u["id"]
    overflow = False
    if geo.get("container_bbox") is not None:
        if (geo["outside_px"] >= MIN_PIXELS and geo["outside_depth"] > OVERFLOW_DEPTH) or geo["outside_box_depth"] > OVERFLOW_DEPTH_BOX:
            depth = max(geo["outside_depth"], geo["outside_box_depth"] if geo["outside_box_depth"] > OVERFLOW_DEPTH_BOX else 0)
            F.add(uid, "container", "error",
                  "Text runs %spt outside the box or area it sits in; it must fit inside." % fmt(depth),
                  depth_pt=depth, outside_bbox=geo.get("outside_bbox"), container_bbox=geo["container_bbox"],
                  ink_bbox=geo["ink_bbox"])
            overflow = True
    if geo.get("effect_density", 0) > EFFECT_DENSITY and in_real_run(geo):
        F.add(uid, "text_effect", "warn",
              "The original letters had an outline or shadow drawn as shapes, and those shapes are still on the page under the English; "
              "check that no Japanese letter shapes remain visible.", density=geo["effect_density"])
    if not overflow and geo["collision_px"] >= MIN_PIXELS:
        F.add(uid, "collision", "error",
              "Text touches or crosses a line, border, arrow or other graphic that the original text did not touch.",
              at_bbox=geo["collision_bbox"], pixels=geo["collision_px"])
        overflow = True
    if not overflow and geo.get("pad") is not None and geo.get("orig_pad") is not None:
        if geo["pad"] < PAD_TIGHT and geo["orig_pad"] - geo["pad"] > 0.75:
            F.add(uid, "padding", "warn",
                  "Text is very close to the edge of its box (%spt clearance; the original had %spt)." % (fmt(geo["pad"]), fmt(geo["orig_pad"])),
                  clearance_pt=geo["pad"], original_clearance_pt=geo["orig_pad"], container_bbox=geo["container_bbox"])
    # content margins
    ib = geo["ink_bbox"] or geo["glyph_bbox"]
    if ib and zone is not None and not in_bleed:
        allowed = [min(content[0], zone.x0), min(content[1], zone.y0), max(content[2], zone.x1), max(content[3], zone.y1)]
        over = {"left": allowed[0] - ib[0], "top": allowed[1] - ib[1], "right": ib[2] - allowed[2], "bottom": ib[3] - allowed[3]}
        bad = {k: round(v, 2) for k, v in over.items() if v > MARGIN_TOL}
        if bad:
            side, amt = max(bad.items(), key=lambda kv: kv[1])
            F.add(uid, "margins", "error",
                  "Text runs %spt past the page's %s content margin (the edge the rest of the page keeps to)." % (fmt(amt), side),
                  overrun_pt=bad, content_box=content, ink_bbox=ib)
    return overflow


def _nth_open(vtexts, k):
    """Index (in the space-joined text) of the last unmatched '(' before the end of line k."""
    joined = " ".join(vtexts)
    end = len(" ".join(vtexts[:k + 1]))
    stack = []
    for i, ch in enumerate(joined[:end]):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            stack.pop()
    return stack[-1] if stack else 0


def measure_original_style(rs, u, zone, outlined_by_id):
    """Size and colour of Japanese that has no text style: drawn as shapes (outlined) or inside a
    picture (region). Size from the glyph-row height, colour from the fill or the dominant ink."""
    if u.get("outlined") in outlined_by_id:
        o = outlined_by_id[u["outlined"]]
        b = o["bbox"]
        fill = o.get("fill") or [0, 0, 0]
        col = (int(round(fill[0] * 255)) << 16) | (int(round(fill[1] * 255)) << 8) | int(round(fill[2] * 255))
        return {"size": round(min(b[3] - b[1], b[2] - b[0]) / 0.95, 2), "color": col, "weight": None, "line": None}
    x0, y0, x1, y1 = R.px(tuple(zone), rs.shape)
    crop = rs.src_full[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    flat = crop.reshape(-1, 3)
    bg = R.dominant_colour(flat)
    ink = np.abs(crop.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > 80
    if ink.sum() < 10:
        return None
    c = R.dominant_colour(crop[ink])
    col = (int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])
    rows = ink.any(axis=1)
    runs, n = [], 0
    for v in list(rows) + [False]:
        if v:
            n += 1
        elif n:
            runs.append(n); n = 0
    runs = [r for r in runs if r >= 3]
    size = round(float(np.median(runs)) / R.S / 0.9, 2) if runs else None
    if size is None:
        return None
    return {"size": size, "color": col, "weight": None, "line": None}


def in_real_run(geo):
    return not geo.get("fp_mode")


def judge_vertical(F, u, chars, geo, rs):
    uid = u["id"]
    vchars = [c for c in chars if abs(c["dir"][0]) < 1e-3 and not c["c"].isspace()]
    if not vchars or geo is None or not geo.get("orig_ink_bbox") or not geo.get("ink_bbox"):
        return
    oi, ni = geo["orig_ink_bbox"], geo["ink_bbox"]
    ext = {"top": round(oi[1] - ni[1], 2), "bottom": round(ni[3] - oi[3], 2)}
    for end, amt in ext.items():
        if amt <= 1.0:
            continue
        strip = (ni[0], ni[1], ni[2], oi[1]) if end == "top" else (ni[0], oi[3], ni[2], ni[3])
        x0, y0, x1, y1 = R.px(strip, rs.shape)
        busy = int(rs.edges[y0:y1, x0:x1].sum()) if (x1 > x0 and y1 > y0) else 0
        if busy >= MIN_PIXELS:
            F.add(uid, "vertical_extent", "error",
                  "Rotated text runs %spt beyond the %s end of the space the Japanese used, into a drawn line or shape." % (fmt(amt), end),
                  overrun_pt=amt, original_ink=oi, ink_bbox=ni)
        else:
            F.add(uid, "vertical_extent", "info",
                  "Rotated text runs %spt beyond the %s end of the space the Japanese used; the space there is clear." % (fmt(amt), end),
                  overrun_pt=amt)


# ====================================================================== judgements: text

def judge_text(F, u, chars, vlines, lines_by_id, orig_objs, geo, pl, rs=None, size_override=None):
    uid, role = u["id"], u.get("role", "")
    ink_chars = [c for c in chars if not c["c"].isspace()]
    rtext = T.rendered_text(vlines)
    # leftover Japanese
    jp = T.japanese_chars(rtext)
    if jp:
        F.add(uid, "leftover_japanese", "error", "Japanese characters or punctuation remain in the English: %s" % jp[:20], chars=jp[:40])
    # wording vs units.json
    rw = Counter(T.words(rtext))
    variants = [("en", u.get("en") or [])] + ([("en_short", u["en_short"])] if u.get("en_short") else [])
    best = None
    for name, segs in variants:
        ew = Counter(T.words(_seg_text(segs)))
        missing = ew - rw
        extra = rw - ew
        score = sum(missing.values()) * 2 + sum(extra.values())
        if best is None or score < best[0]:
            best = (score, name, segs, missing, extra)
    _, vname, segs, missing, extra = best
    if missing:
        F.add(uid, "wording", "error", "Words from the translation are missing on the page: %s" % ", ".join(sorted(missing.elements()))[:200],
              missing=sorted(missing.elements()), expected=_seg_text(segs), rendered=rtext)
    if extra:
        F.add(uid, "wording", "warn", "The page shows words that are not in the translation: %s" % ", ".join(sorted(extra.elements()))[:200],
              extra=sorted(extra.elements()), expected=_seg_text(segs), rendered=rtext)
    if vname == "en_short":
        F.add(uid, "short_form", "info", "The shorter translation (en_short) was used.")
    if pl and pl.get("overflow"):
        F.add(uid, "layout_report", "info", "The layout engine itself reported that this text did not fit its allowed area.",
              flags=pl.get("flags"))
    if pl and pl.get("used_short") is not None and bool(pl.get("used_short")) != (vname == "en_short"):
        F.add(uid, "short_form", "info", "The layout engine reports used_short=%s but the page shows the %s wording." % (pl.get("used_short"), vname))

    aligned, frac = T.align(segs, chars)
    seg_meas = []
    for si, seg in enumerate(segs):
        st = T.style_of(seg.get("style"), lines_by_id)
        if size_override:    # outlined / region source: the original look is measured, not looked up
            st = dict(size_override)
        idx = aligned[si]
        sc = [chars[i] for i in idx if not chars[i]["c"].isspace()]
        alnum = sum(1 for ch in seg.get("t", "") if ch.isalnum())
        if not sc or st is None:
            seg_meas.append(None)
            continue
        size = float(np.median([c["size"] for c in sc]))
        col = Counter(c["color"] for c in sc).most_common(1)[0][0]
        bold = sum(1 for c in sc if c["bold"]) / len(sc)
        seg_meas.append({"si": si, "t": seg.get("t", ""), "style": st, "size": size, "color": col, "bold": bold, "alnum": alnum,
                         "is_mark": alnum == 0 and len(seg.get("t", "").strip()) <= 2, "first": idx[0], "last": idx[-1]})
    measured_segments = [m for m in seg_meas if m and m["alnum"] >= 2]

    # ---- size
    flags = set((pl or {}).get("flags") or [])
    worst = None
    for m in measured_segments:
        o, s = m["style"]["size"], m["size"]
        sev, msg = None, None
        floor = min(SIZE_FLOOR, o)
        if role in STRICT_ROLES and s < o - max(0.06, 0.01 * o):
            if "fixed_role_shrunk" in flags and s >= FIXED_ROLE_MIN * o and s >= floor - 0.05:
                sev, msg = "warn", "Set at %d%% of its original size (%spt from %spt) so that it fits inside its box." % (
                    round(100 * s / o), fmt(s), fmt(o))
            else:
                sev, msg = "error", "This %s was shrunk from %spt to %spt; %ss must keep their original size." % (role, fmt(o), fmt(s), role)
        elif role not in STRICT_ROLES and s < floor - 0.05:
            if "small_type" in flags and role in SMALL_TYPE_ROLES and s >= SMALL_TYPE_MIN:
                sev, msg = "warn", "Set in very small type (%spt, original %spt) to fit its box." % (fmt(s), fmt(o))
            else:
                sev, msg = "error", "Text is set at %spt, below the %spt minimum for legibility (original %spt)." % (fmt(s), fmt(floor), fmt(o))
        elif s < 0.8 * o:
            sev, msg = "warn", "Text was shrunk to %d%% of its original size (%spt from %spt)." % (round(100 * s / o), fmt(s), fmt(o))
        elif s < 0.97 * o:
            sev, msg = "info", "Text was shrunk slightly, to %spt from %spt." % (fmt(s), fmt(o))
        elif s > 1.2 * o:
            sev, msg = "info", "Text was enlarged to %spt from %spt." % (fmt(s), fmt(o))
        if sev and (worst is None or SEV_RANK[sev] > SEV_RANK[worst[0]] or (sev == worst[0] and s / o < worst[2])):
            worst = (sev, msg, s / o, m)
    if worst:
        m = worst[3]
        F.add(uid, "size", worst[0], worst[1], original_pt=m["style"]["size"], rendered_pt=round(m["size"], 2),
              segment=snippet(m["t"]), style_line=m["style"].get("line"), original_measured=bool(size_override))
    # ---- mixed sizes flattened
    if len(measured_segments) >= 2:
        hi = max(measured_segments, key=lambda m: m["style"]["size"])
        lo = min(measured_segments, key=lambda m: m["style"]["size"])
        oratio = hi["style"]["size"] / lo["style"]["size"]
        if oratio >= 1.1:
            rratio = hi["size"] / lo["size"]
            if rratio < 1 + 0.5 * (oratio - 1):
                F.add(uid, "emphasis_size", "warn",
                      "Two-size emphasis was flattened: '%s' was %s times the size of '%s', now %s times." % (
                          snippet(hi["t"], 25), fmt(oratio), snippet(lo["t"], 25), fmt(rratio)),
                      original_pt=[hi["style"]["size"], lo["style"]["size"]], rendered_pt=[round(hi["size"], 2), round(lo["size"], 2)])
    # ---- colour and weight
    mark_cols = {m["color"] for m in seg_meas if m and m["is_mark"]}
    for m in measured_segments:
        ec = m["style"].get("color")
        d = max(abs(a - b) for a, b in zip(rgb(ec), rgb(m["color"]))) if ec is not None else 0
        if d > 48:
            if m["color"] in mark_cols and m["color"] != ec:
                F.add(uid, "colour", "warn", "'%s' took the bullet mark's colour (%s) instead of %s." % (snippet(m["t"], 30), hexcol(m["color"]), hexcol(ec)),
                      expected=hexcol(ec), rendered=hexcol(m["color"]))
            else:
                F.add(uid, "colour", "warn", "'%s' is %s; the original style is %s." % (snippet(m["t"], 30), hexcol(m["color"]), hexcol(ec)),
                      expected=hexcol(ec), rendered=hexcol(m["color"]))
        if m["style"].get("weight") is None:
            pass
        elif m["style"]["weight"] >= 700 and m["bold"] < 0.5:
            F.add(uid, "weight", "warn", "'%s' should be bold, as in the original, but is set in a regular weight." % snippet(m["t"], 30))
        elif m["style"]["weight"] < 700 and m["bold"] > 0.5:
            F.add(uid, "weight", "info", "'%s' is bold but the original is regular." % snippet(m["t"], 30))
        if geo and geo.get("bg") is not None and ec is not None:
            cr = contrast(rgb(m["color"]), tuple(geo["bg"]))
            ocr = contrast(rgb(ec), tuple(geo["bg"]))
            if cr < 1.8 and cr < 0.8 * ocr:
                F.add(uid, "contrast", "error", "'%s' is barely visible against its background (contrast %s:1)." % (snippet(m["t"], 30), fmt(cr)),
                      text=hexcol(m["color"]), background=geo["bg"])

    # ---- structure
    nvl = len(vlines)
    vl_of = [c["vline"] for c in chars]
    intended = set()        # vline boundaries (k means between k and k+1) the translator asked for
    for si, seg in enumerate(segs[:-1]):
        if not seg.get("br"):
            continue
        a = seg_meas[si]
        b = next((seg_meas[j] for j in range(si + 1, len(segs)) if seg_meas[j]), None)
        if not a or not b:
            continue
        if vl_of[a["last"]] == vl_of[b["first"]]:
            F.add(uid, "line_breaks", "warn", "The translation asks for a line break after '%s', but the page runs on." % snippet(a["t"], 30),
                  after=a["t"])
        else:
            intended.add(vl_of[a["last"]])
    full = [c["c"] for c in chars]
    vtexts = [T.vline_text(vl) for vl in vlines]
    pos = 0
    depth = 0
    open_at = None
    for k in range(nvl - 1):
        for c in vlines[k]:
            if c["c"] == "(":
                depth += 1
                open_at = pos
            elif c["c"] == ")":
                depth = max(0, depth - 1)
            pos += 1
        if k in intended:
            continue
        cur, nxt = vtexts[k], vtexts[k + 1]
        if depth > 0:
            rest = " ".join(vtexts[:k + 1] + vtexts[k + 1:])
            rest = rest[_nth_open(vtexts, k):]
            close = rest.find(")")
            phrase = rest[:close + 1] if close >= 0 else rest
            if len(phrase) <= 40:
                F.add(uid, "bad_break", "warn", "A line breaks inside the short bracketed phrase '%s'; keep it on one line." % " ".join(phrase.split()),
                      line_end=cur[-25:], next_line=nxt[:25])
        if re.search(r"\d[\d.,]*$", cur) and re.match(r"^(%|％|yrs?\b|years?\b|months?\b|days?\b|JPY|USD|AUD|yen\b|times\b|pt\b)", nxt, re.I):
            F.add(uid, "bad_break", "warn", "A number is separated from its unit at a line break ('%s' / '%s')." % (cur[-12:], nxt[:12]))
        elif re.search(r"(USD|JPY|AUD|¥|\$)$", cur) and re.match(r"^\d", nxt):
            F.add(uid, "bad_break", "warn", "A currency is separated from its amount at a line break ('%s' / '%s')." % (cur[-12:], nxt[:12]))
    if role in WIDOW_ROLES and nvl >= 2 and (nvl - 2) not in intended:
        last = vtexts[-1]
        if " " not in last and len(last) <= 12 and len(last) < 0.4 * len(vtexts[-2]):
            F.add(uid, "widow", "warn", "The last line holds a single short word ('%s'); rebalance the lines." % last)
    # hanging indent for bullets
    horiz = [vl for vl in vlines if abs(vl[0]["dir"][1]) < 1e-3]
    first_ink = next((c for c in ink_chars), None)
    if nvl >= 2 and len(horiz) == nvl and first_ink is not None and (role == "bullet" or first_ink["c"] in T.MARKS):
        l0 = [c for c in vlines[0] if not c["c"].isspace()]
        text_start = next((c["g"][0] for c in l0 if c["c"].isalnum() or c["c"] in "'\"‘“("), None)
        mark_x = l0[0]["g"][0]
        orig_hanging = role == "bullet"
        if len(orig_objs) >= 2 and all(o["wmode"] == 0 for o in orig_objs[:2]):
            ic0 = [c for c in orig_objs[0]["chars"] if not c["c"].isspace()]
            ic1 = [c for c in orig_objs[1]["chars"] if not c["c"].isspace()]
            if len(ic0) >= 2 and ic1:
                start0 = ic0[1]["g"][0] if ic0[0]["c"] in T.MARKS else ic0[0]["g"][0]
                x1_0 = ic1[0]["g"][0]
                c0 = (ic0[0]["g"][0] + ic0[-1]["g"][2]) / 2
                c1 = (ic1[0]["g"][0] + ic1[-1]["g"][2]) / 2
                centred = abs(c0 - c1) < 2.0
                orig_hanging = (ic0[0]["c"] in T.MARKS and abs(x1_0 - start0) < 1.0 and not centred) or \
                               (role == "bullet" and x1_0 > ic0[0]["g"][0] + 1.0)
        if text_start is not None and text_start - mark_x >= 1.0 and orig_hanging:
            bad = [round(vl[0]["g"][0], 2) for vl in vlines[1:] if [c for c in vl if not c["c"].isspace()] and
                   min(c["g"][0] for c in vl if not c["c"].isspace()) < text_start - 1.0]
            if bad:
                F.add(uid, "hanging_indent", "warn",
                      "Wrapped lines start under the bullet mark instead of under the text (hanging indent lost).",
                      text_start=round(text_start, 2), wrapped_line_starts=bad)
    # alignment within a closed container
    if geo and geo.get("container_bbox") and len(horiz) == nvl and orig_objs:
        op = geo["container_open"]
        if not op["left"] and not op["right"]:
            cx0, cx1 = geo["container_bbox"][0], geo["container_bbox"][2]
            o_ext = [(min(c["g"][0] for c in o["chars"] if not c["c"].isspace()), max(c["g"][2] for c in o["chars"] if not c["c"].isspace()))
                     for o in orig_objs if o["wmode"] == 0 and abs(o["dir"][1]) < 1e-3]

            def centred(exts, tolf):
                return exts and all(abs((a - cx0) - (cx1 - b)) <= max(tolf[0], tolf[1] * ((a - cx0) + (cx1 - b))) and min(a - cx0, cx1 - b) > 0.5
                                    for a, b in exts)
            n_ext = []
            for vl in vlines:
                ic = [c for c in vl if not c["c"].isspace()]
                if ic:
                    n_ext.append((min(c["g"][0] for c in ic), max(c["g"][2] for c in ic)))
            if len(o_ext) == len(orig_objs) and centred(o_ext, (1.5, 0.12)) and sum((cx1 - cx0) - (b - a) for a, b in o_ext) / len(o_ext) > 3:
                if n_ext and not centred(n_ext, (2.0, 0.2)):
                    off = max(abs((a - cx0) - (cx1 - b)) / 2 for a, b in n_ext)
                    F.add(uid, "alignment", "warn", "The original is centred in its box; the English is off-centre by up to %spt." % fmt(off),
                          container_bbox=geo["container_bbox"], line_extents=[[round(a, 1), round(b, 1)] for a, b in n_ext])
    if rs is not None:
        judge_vertical(F, u, chars, geo, rs)


# ====================================================================== judgements: across units

def judge_overlaps(F, unit_meas, out_lines, remnant, keep_ids, rs):
    owners = []
    for uid, um in unit_meas.items():
        boxes = [c["g"] for c in um["chars"] if not c["c"].isspace()]
        if boxes:
            owners.append((uid, boxes))
    for i, ol in enumerate(out_lines):
        if i in remnant:
            boxes = [c["g"] for c in ol["chars"] if not c["c"].isspace()]
            if boxes:
                owners.append(("line %d" % remnant[i], boxes))

    def bb(boxes):
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    bbs = [bb(b) for _, b in owners]
    for i in range(len(owners)):
        for j in range(i + 1, len(owners)):
            a, b = bbs[i], bbs[j]
            ix = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
            if ix[2] <= ix[0] or ix[3] <= ix[1]:
                continue
            win = R.px(ix, rs.shape)
            if win[2] <= win[0] or win[3] <= win[1]:
                continue
            ma = R.box_mask(owners[i][1], rs.shape, win)
            mb = R.box_mask(owners[j][1], rs.shape, win)
            n = int((ma & mb & rs.out_core[win[1]:win[3], win[0]:win[2]]).sum())
            if n >= 6:
                ua, ub = owners[i][0], owners[j][0]
                for me, other in ((ua, ub), (ub, ua)):
                    if not me.startswith("line "):
                        what = "kept text (%s)" % other if other.startswith("line ") else "the text of unit %s" % other
                        F.add(me, "text_overlap", "error", "Text overlaps %s." % what, other=other,
                              at_bbox=[round(v, 1) for v in ix], pixels=n)


def _main_style(u, lines_by_id):
    segs = u.get("en") or []
    best = max(segs, key=lambda s: sum(1 for ch in s.get("t", "") if ch.isalnum()), default=None)
    return T.style_of(best.get("style"), lines_by_id) if best else None


def _max_style(u, lines_by_id):
    sts = [T.style_of(s.get("style"), lines_by_id) for s in (u.get("en") or []) if sum(1 for ch in s.get("t", "") if ch.isalnum()) >= 2]
    sts = [x for x in sts if x]
    return max(sts, key=lambda x: x["size"]) if sts else None


def _max_size(um):
    sizes = [c["size"] for c in um["chars"] if c["c"].isalnum()]
    return round(max(sizes), 2) if sizes else None


def _main_size(um):
    sizes = [c["size"] for c in um["chars"] if c["c"].isalnum()]
    if not sizes:
        return None
    return Counter(round(s, 2) for s in sizes).most_common(1)[0][0]


def judge_siblings(F, units, unit_meas, zones, lines_by_id, pl_by_id):
    groups = defaultdict(list)
    for u in units:
        uid = u["id"]
        if uid not in unit_meas or uid not in zones:
            continue
        st = _main_style(u, lines_by_id)
        if not st:
            continue
        groups[(u.get("role"), round(st["size"], 1), st["color"], st["weight"])].append(uid)
    for key, ids in groups.items():
        if len(ids) < 2:
            continue
        parent = {i: i for i in ids}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                za, zb = zones[ids[a]], zones[ids[b]]
                row = abs((za.y0 + za.y1) / 2 - (zb.y0 + zb.y1) / 2) < 1.5 and max(za.x0, zb.x0) - min(za.x1, zb.x1) < 150
                col = (abs((za.x0 + za.x1) / 2 - (zb.x0 + zb.x1) / 2) < 1.5 or abs(za.x0 - zb.x0) < 1.0) and max(za.y0, zb.y0) - min(za.y1, zb.y1) < 80
                ga = (pl_by_id.get(ids[a]) or {}).get("sibling_group")
                same_group = ga is not None and ga == (pl_by_id.get(ids[b]) or {}).get("sibling_group")
                if row or col or same_group:
                    parent[find(ids[a])] = find(ids[b])
        comps = defaultdict(list)
        for i in ids:
            comps[find(i)].append(i)
        for comp in comps.values():
            if len(comp) < 2:
                continue
            sizes = {i: _main_size(unit_meas[i]) for i in comp}
            sizes = {i: s for i, s in sizes.items() if s}
            if len(sizes) < 2 or max(sizes.values()) - min(sizes.values()) <= 0.25:
                continue
            common = Counter(sizes.values()).most_common(1)[0][0]
            ref = next(i for i in comp if sizes.get(i) == max(sizes.values()))
            for i, s in sizes.items():
                if abs(s - common) > 0.25 or (abs(s - max(sizes.values())) > 0.25 and s == min(sizes.values())):
                    F.add(i, "siblings", "warn",
                          "Set at %spt while its matching %s neighbours are at up to %spt (e.g. unit %s); siblings should match." % (
                              fmt(s), key[0] or "unit", fmt(max(sizes.values())), ref),
                          group=sorted(comp), sizes={k: v for k, v in sizes.items()})


def _separated(rs, za, zb):
    """True when a drawn horizontal edge (a rule or a box border) runs between two stacked areas."""
    x0, x1 = max(za.x0, zb.x0), min(za.x1, zb.x1)
    px0, py0, px1, py1 = R.px((x0, za.y1, x1, zb.y0), rs.shape)
    if px1 <= px0 or py1 <= py0:
        return False
    strip = rs.edges[py0:py1, px0:px1]
    return bool((strip.mean(axis=1) > 0.6).any())


def judge_hierarchy(F, units, unit_meas, zones, lines_by_id, rs=None):
    for u in units:
        uid = u["id"]
        if uid not in unit_meas or uid not in zones:
            continue
        st = _max_style(u, lines_by_id)
        za = zones[uid]
        if not st or za.width < za.height:          # vertical units do not introduce text below
            continue
        best, gap = None, 1e9
        for v in units:
            vid = v["id"]
            if vid == uid or vid not in unit_meas or vid not in zones:
                continue
            zb = zones[vid]
            if zb.y0 < za.y1 - 1 or zb.width < zb.height:
                continue
            ov = min(za.x1, zb.x1) - max(za.x0, zb.x0)
            if ov < 0.3 * min(za.width, zb.width):
                continue
            g = zb.y0 - za.y1
            if g < gap and g <= min(30, 3 * st["size"]) and not (rs is not None and _separated(rs, za, zb)):
                best, gap = v, g
        if best is None:
            continue
        sb = _main_style(best, lines_by_id)
        if not sb or st["size"] < 1.12 * sb["size"]:
            continue
        ma, mb = _max_size(unit_meas[uid]), _main_size(unit_meas[best["id"]])
        if ma is None or mb is None:
            continue
        if ma < mb - 0.05:
            F.add(uid, "hierarchy", "error",
                  "This %s is now smaller (%spt) than the text it introduces below it (unit %s, %spt); in the original it was larger (%spt vs %spt)." % (
                      u.get("role"), fmt(ma), best["id"], fmt(mb), fmt(st["size"]), fmt(sb["size"])),
                  rendered_pt=ma, below_unit=best["id"], below_pt=mb, original_pt=[st["size"], sb["size"]])
        elif abs(ma - mb) <= 0.05:
            F.add(uid, "hierarchy", "warn",
                  "This %s is now the same size (%spt) as the text it introduces (unit %s); in the original it was clearly larger." % (
                      u.get("role"), fmt(ma), best["id"]),
                  rendered_pt=ma, below_unit=best["id"], original_pt=[st["size"], sb["size"]])


def judge_columns(F, units, unit_meas, zones, lines_by_id):
    """Units that shared a left edge in the original should still share one."""
    by_role = defaultdict(list)
    for u in units:
        uid = u["id"]
        if uid in unit_meas and uid in zones and zones[uid].width > zones[uid].height:
            by_role[u.get("role")].append(uid)
    for role, ids in by_role.items():
        ids.sort(key=lambda i: zones[i].x0)
        cols, cur = [], []
        for i in ids:
            if cur and zones[i].x0 - zones[cur[0]].x0 > 1.0:
                cols.append(cur); cur = []
            cur.append(i)
        if cur:
            cols.append(cur)
        for col in cols:
            if len(col) < 3:
                continue
            col.sort(key=lambda i: zones[i].y0)
            # stacked, near each other
            chain = [col[0]]
            for i in col[1:]:
                if zones[i].y0 - zones[chain[-1]].y1 < 40:
                    chain.append(i)
            if len(chain) < 3:
                continue
            x0s = {}
            for i in chain:
                vl = unit_meas[i]["vlines"]
                ic = [c for c in vl[0] if not c["c"].isspace()] if vl else []
                if ic:
                    x0s[i] = min(c["g"][0] for c in ic)
            if len(x0s) < 3:
                continue
            x1s = [zones[i].x1 for i in chain]
            if max(x1s) - min(x1s) < 2.0:      # same width too: they may simply be centred in equal cells
                continue
            med = float(np.median(list(x0s.values())))
            for i, x in x0s.items():
                if abs(x - med) > 1.5:
                    F.add(i, "alignment", "warn",
                          "In the original this %s shares a left edge with the %s above and below it; the English starts %spt %s of them." % (
                              role, role + "s", fmt(abs(x - med)), "left" if x < med else "right"),
                          column=chain, left_edge=round(x, 2), column_edge=round(med, 2))


# ====================================================================== completeness and global

def judge_completeness(F, units, uj, pj, out_lines, remnant, unassigned, lines_by_id, keep_ids, drop_ids):
    present = defaultdict(list)
    for i, lid in remnant.items():
        present[lid].append(out_lines[i])
    unit_of_line = {}
    for u in units:
        for lid in u.get("lines") or []:
            unit_of_line[lid] = u["id"]
    for lid, ols in present.items():
        if lid in keep_ids:
            continue
        txt = lines_by_id[lid]["text"]
        where = unit_of_line.get(lid)
        if lid in drop_ids:
            F.add(None, "drop", "error", "Line %d ('%s') should have been removed but is still on the page." % (lid, snippet(txt, 20)),
                  line=lid, bbox=lines_by_id[lid]["bbox"])
        elif where:
            sev = "error" if T.has_japanese(txt) else "warn"
            F.add(where, "leftover_japanese" if sev == "error" else "completeness", sev,
                  "The original text of line %d ('%s') is still on the page." % (lid, snippet(txt, 20)), line=lid, bbox=lines_by_id[lid]["bbox"])
    jp_kept = []
    for lid in sorted(keep_ids):
        l = lines_by_id.get(lid)
        if not l:
            continue
        if lid not in present:
            same = [ol for ol in out_lines if ol["text"].strip() == l["text"].strip()]
            if same:
                F.add(None, "keep", "warn", "Kept text '%s' (line %d) has moved." % (l["text"].strip(), lid), line=lid,
                      original=l["bbox"], now=[round(v, 1) for v in same[0]["bbox"]])
            else:
                F.add(None, "keep", "error", "Kept text '%s' (line %d) is missing from the page." % (l["text"].strip(), lid), line=lid, bbox=l["bbox"])
        letters = T.CJK.sub("", l["text"].replace("・", "\u0000")).replace("\u0000", "・")
        if letters == l["text"] and (T.JP_PUNCT.search(l["text"]) or "・" in l["text"]):
            jp_kept.append(l)     # badges and numbers kept as printed, but with Japanese punctuation
    if jp_kept:
        ex = []
        for l in jp_kept:
            t = l["text"].strip()
            sug = t.replace("・", "–").replace("～", "–").replace("％", "%").replace("（", "(").replace("）", ")")
            if (t, sug) not in ex:
                ex.append((t, sug))
        F.add(None, "kept_punctuation", "warn",
              "%d kept text(s) use Japanese punctuation; English would read: %s." % (
                  len(jp_kept), ", ".join("'%s' -> '%s'" % e for e in ex[:8])),
              lines=[l["id"] for l in jp_kept], bbox=jp_kept[0]["bbox"])
    # extract v2: second copies of a line drawn in the same place (outline or shadow under the fill)
    copies_of = defaultdict(list)
    for dpl in pj.get("duplicates") or []:
        copies_of[dpl.get("of")].append(dpl)
    for lid, dups in copies_of.items():
        l = lines_by_id.get(lid)
        if l is None:
            continue
        t = l["text"].strip()
        box = fitz.Rect(l["bbox"])
        for dpl in dups:
            box |= fitz.Rect(dpl["bbox"])
        box += (-1.5, -1.5, 1.5, 1.5)
        n = sum(1 for ol in out_lines if ol["text"].strip() == t and box.contains(fitz.Rect(ol["bbox"])))
        if lid in keep_ids and n < 1 + len(dups):
            F.add(None, "duplicates", "warn",
                  "Kept text '%s' (line %d) was drawn %d times in the original (outline or shadow) but %d time(s) now; its look may have changed." % (
                      snippet(t, 20), lid, 1 + len(dups), n), line=lid, bbox=l["bbox"])
        elif lid not in keep_ids and n > 0 and lid not in present:
            F.add(None, "duplicates", "error",
                  "A second copy of line %d ('%s') is still on the page; it must go with the line it copies." % (lid, snippet(t, 20)),
                  line=lid, bbox=l["bbox"])
    for ol in unassigned:
        if T.has_japanese(ol["text"]):
            F.add(None, "leftover_japanese", "error", "Japanese text remains on the page: '%s'." % snippet(ol["text"], 30), bbox=rect_list(ol["bbox"]))
        else:
            F.add(None, "completeness", "warn", "Text on the page does not belong to any unit: '%s'." % snippet(ol["text"], 30), bbox=rect_list(ol["bbox"]))


def _drawing_key(d):
    r = d["rect"]
    return (round(r.x0), round(r.y0), round(r.x1), round(r.y1), tuple(round(c, 2) for c in (d.get("fill") or ())),
            tuple(round(c, 2) for c in (d.get("color") or ())))


def judge_global(F, src_doc, src_page, out_doc, out_page, units, outlined_by_id, rs, pj, zones):
    # fonts
    src_fonts = {f[3] for f in src_page.get_fonts()}
    seen, dup = {}, Counter()
    for f in out_page.get_fonts():
        xref, ext, typ, base = f[0], f[1], f[2], f[3]
        if base in src_fonts:
            continue
        dup[base] += 1
        if ext == "n/a":
            F.add(None, "fonts", "error", "Font '%s' is not embedded; the page will look different on other computers." % base, font=base)
            continue
        if xref in seen:
            continue
        try:
            buf = out_doc.extract_font(xref)[3]
            seen[xref] = len(buf)
        except Exception:  # noqa: BLE001
            continue
        if "+" not in base and len(buf) > 150000:
            F.add(None, "fonts", "warn", "Font '%s' is embedded in full (%d KB) rather than subset." % (base, len(buf) // 1024), font=base, bytes=len(buf))
    many = {k: v for k, v in dup.items() if v > 4}
    if many:
        F.add(None, "fonts", "info", "The same font is embedded several times: %s." % ", ".join("%s x%d" % kv for kv in many.items()), copies=many)
    # images
    ni, no = len(src_page.get_image_info()), len(out_page.get_image_info())
    if ni != no:
        F.add(None, "graphics", "error", "The original has %d images, the English page %d." % (ni, no))
    # drawings: unchanged apart from removed outlined glyphs
    used_outl = [fitz.Rect(outlined_by_id[u["outlined"]]["bbox"]) + (-1, -1, 1, 1) for u in units if u.get("outlined") in outlined_by_id]
    sd = src_page.get_drawings()
    od = out_page.get_drawings()
    removed_ok = Counter(_drawing_key(d) for d in sd if any(r.contains(d["rect"]) for r in used_outl))
    sk, ok_ = Counter(_drawing_key(d) for d in sd), Counter(_drawing_key(d) for d in od)
    gone = sk - ok_ - removed_ok
    new = ok_ - sk
    if new:
        boxes = [list(k[:4]) for k in list(new.elements())[:10]]
        F.add(None, "graphics", "warn", "%d vector shapes were added to the page (e.g. patches covering text)." % sum(new.values()), examples=boxes)
    if gone:
        boxes = [list(k[:4]) for k in list(gone.elements())[:10]]
        F.add(None, "graphics", "warn", "%d vector shapes of the original are missing from the English page." % sum(gone.values()), examples=boxes)
    not_removed = removed_ok - (sk - ok_)
    # outlined text still drawn
    remaining = extract.outlined_text(out_page)
    for o in remaining:
        ob = fitz.Rect(o["bbox"])
        hit = [r for r in used_outl if area(r & ob) > 0.5 * area(ob)]
        if hit:
            F.add(None, "outlined", "error", "Japanese drawn as shapes is still on the page at %s." % rect_list(ob), bbox=o["bbox"])
        else:
            F.add(None, "outlined", "warn", "Something that looks like text drawn as shapes is on the page at %s; check it is not untranslated Japanese." % rect_list(ob), bbox=o["bbox"])
    # graphics raster changes outside removed outlined glyphs
    ch = R.diff(rs.src_gfx, rs.out_gfx) > 60
    for r in used_outl:
        x0, y0, x1, y1 = R.px(tuple(r), rs.shape, pad=1)
        ch[y0:y1, x0:x1] = False
    lab, n = ndi.label(ndi.binary_opening(ch, iterations=1))
    if n:
        objs = ndi.find_objects(lab)
        big = []
        for sl in objs:
            hh, ww = (sl[0].stop - sl[0].start) / R.S, (sl[1].stop - sl[1].start) / R.S
            if hh * ww > 4:
                big.append([round(sl[1].start / R.S, 1), round(sl[0].start / R.S, 1), round(sl[1].stop / R.S, 1), round(sl[0].stop / R.S, 1)])
        if big:
            F.add(None, "graphics", "warn", "The page's graphics changed in %d place(s) (not counting removed Japanese shapes)." % len(big), regions=big[:15])
    # images that may contain text
    region_zones = [zones[u["id"]] for u in units if u.get("region") and u["id"] in zones]
    for im in out_page.get_image_info():
        b = fitz.Rect(im["bbox"]) & out_page.rect
        if b.width < 30 or b.height < 12:
            continue
        if any(area(b & z) > 0.5 * area(z) for z in region_zones):
            continue
        x0, y0, x1, y1 = R.px(tuple(b), rs.shape)
        rows = R.text_like_blobs(rs.out_gfx[y0:y1, x0:x1])
        if rows >= 8:
            F.add(None, "image_text", "info", "The picture at %s may contain text; check it in a visual review." % rect_list(b), bbox=rect_list(b), glyph_like=rows)


# ====================================================================== output

def _finish(page_dir, pj, units, F, measured, outputs, unit_meas, write, t0, placements, content=None, page_dir_raw=None):
    out_units = []
    units = units or []
    by_check = Counter()
    errs = warns = 0
    for u in units:
        fs = F.units.get(u["id"], [])
        for f in fs:
            by_check["%s:%s" % (f["check"], f["severity"])] += 1
        errs += sum(1 for f in fs if f["severity"] == "error")
        warns += sum(1 for f in fs if f["severity"] == "warn")
        out_units.append({"id": u["id"], "role": u.get("role"), "findings": fs, "measured": measured.get(u["id"], {})})
    for f in F.page:
        by_check["%s:%s" % (f["check"], f["severity"])] += 1
    errs += sum(1 for f in F.page if f["severity"] == "error")
    warns += sum(1 for f in F.page if f["severity"] == "warn")
    verdict = "fail" if errs else ("review" if warns else "pass")
    res = {"summary": {"errors": errs, "warnings": warns, "by_check": dict(sorted(by_check.items())), "verdict": verdict,
                       "placements_used": placements is not None, "content_box": content, "seconds": round(time.time() - t0, 1)},
           "units": out_units, "page": F.page}
    if write:
        page_dir = Path(page_dir)
        (page_dir / "checks.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            if not outputs:
                outputs = _fallback_renders(pj)
            write_images(page_dir, res, outputs)
        except Exception as e:  # noqa: BLE001
            res["page"].append({"check": "internal:images", "severity": "error",
                                "message": "review.png / compare.png could not be drawn (%s: %s)." % (type(e).__name__, e), "evidence": {}})
            res["summary"]["errors"] += 1
            res["summary"]["verdict"] = "fail"
            (page_dir / "checks.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def _fallback_renders(pj):
    """Renders for review/compare when the English page could not be rendered: the original and a blank page."""
    src = Path(pj["source"])
    if not src.is_absolute():
        src = Path(__file__).resolve().parents[2] / src
    doc, page = _open_normalised(src, pj["page_index"])
    full = R.render(page)
    return {"src_full": full, "out_full": np.full_like(full, 255)}


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(io.BytesIO(fitz.Font("notos").buffer), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


COL = {"error": (220, 30, 30), "warn": (240, 140, 0), "info": (40, 110, 220)}


def write_images(page_dir, res, outputs):
    from PIL import Image, ImageDraw
    k = 150 / R.DPI
    src = Image.fromarray(outputs["src_full"])
    out = Image.fromarray(outputs["out_full"])
    size = (int(out.width * k), int(out.height * k))
    src_s, out_s = src.resize(size, Image.LANCZOS), out.resize(size, Image.LANCZOS)
    # compare.png
    gut, head = 24, 34
    cmp_img = Image.new("RGB", (size[0] * 2 + gut * 3, size[1] + head + gut), "white")
    cmp_img.paste(src_s, (gut, head)); cmp_img.paste(out_s, (size[0] + 2 * gut, head))
    d = ImageDraw.Draw(cmp_img)
    f = _font(18)
    d.text((gut, 6), "Original", fill=(0, 0, 0), font=f)
    d.text((size[0] + 2 * gut, 6), "English", fill=(0, 0, 0), font=f)
    cmp_img.save(str(page_dir / "compare.png"))
    # review.png
    sc = 150 / 72.0
    items = []
    for u in res["units"]:
        fs = [f for f in u["findings"] if f["severity"] in ("error", "warn")]
        if not fs:
            continue
        sev = max(fs, key=lambda f: SEV_RANK[f["severity"]])["severity"]
        box = u["measured"].get("ink_bbox")
        items.append((sev, u["id"], u["role"], box, sorted(fs, key=lambda f: -SEV_RANK[f["severity"]])))
    for f in res["page"]:
        if f["severity"] in ("error", "warn"):
            ev = f["evidence"]
            box = ev.get("bbox") or (ev.get("regions") or ev.get("examples") or [None])[0]
            items.append((f["severity"], "page", "", box, [f]))
    lf = _font(15)
    lines = []
    for n, (sev, uid, role, box, fs) in enumerate(items, 1):
        for j, f in enumerate(fs):
            lines.append((n if j == 0 else None, f["severity"], ("%s %s" % (uid, role)).strip() if j == 0 else "", f["message"]))
    leg_h = 40 + 20 * len(lines)
    img = Image.new("RGB", (size[0], size[1] + leg_h), "white")
    img.paste(out_s, (0, 0))
    d = ImageDraw.Draw(img)
    nf = _font(13)
    for n, (sev, uid, role, box, fs) in enumerate(items, 1):
        if not box:
            continue
        x0, y0, x1, y1 = (v * sc for v in box)
        d.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], outline=COL[sev], width=2)
        lab = str(n)
        tw = d.textlength(lab, font=nf)
        d.rectangle([x0 - 2, y0 - 18, x0 + tw + 4, y0 - 2], fill=COL[sev])
        d.text((x0 + 1, y0 - 18), lab, fill="white", font=nf)
    y = size[1] + 8
    s = res["summary"]
    d.text((10, y), "Verdict: %s   errors: %d   warnings: %d   (red = error, orange = warning)" % (s["verdict"].upper(), s["errors"], s["warnings"]),
           fill=(0, 0, 0), font=lf)
    y += 26
    for n, sev, who, msg in lines:
        if n is not None:
            d.rectangle([10, y + 2, 34, y + 18], fill=COL[sev])
            d.text((13, y + 1), str(n), fill="white", font=nf)
        d.text((42, y), ("%s: %s" % (who, msg)) if who else "      " + msg, fill=COL[sev] if sev == "error" else (60, 60, 60), font=lf)
        y += 20
    img.save(str(page_dir / "review.png"))


# ====================================================================== original-as-output (calibration)

def ink_on_original(page_dir):
    """Run the ink-vs-graphics measurements on the ORIGINAL page, treating each unit's Japanese as if
    it were the rendered output (exemptions off). A sound check reports nothing here."""
    page_dir, pj, uj, placements, src_path, _ = load_context(page_dir)
    src_doc, src_page = _open_normalised(src_path, pj["page_index"])
    g_doc, g_page = _open_normalised(src_path, pj["page_index"])
    R.strip_text(g_page)
    full, gfx = R.render(src_page), R.render(g_page)
    rs = Rasters(full, gfx, full, gfx)
    orig = T.original_lines(src_page, pj["lines"])
    content, bleed = derive_content_box(src_page, gfx, pj)
    F = Findings()
    for u in uj.get("units", []):
        objs = [orig[l] for l in (u.get("lines") or []) if l in orig]
        boxes = [c["g"] for o in objs for c in o["chars"] if not c["c"].isspace()]
        if not boxes:
            continue
        zone = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        geo = unit_geometry(rs, boxes, boxes, [o["bbox"] for o in objs], zone, exempt=False)
        if geo["effect_density"] > EFFECT_DENSITY:
            F.add(u["id"], "text_effect", "info", "The original letters sit on their own outline/shadow shapes; not judged here.")
            continue
        judge_geometry(F, u, geo, content, None, rs)
        if geo.get("pad") is not None and geo["pad"] < 0.3:
            F.add(u["id"], "padding", "info", "original clearance %spt" % fmt(geo["pad"]))
    return F, content


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    pdf = None
    if "--pdf" in args:
        i = args.index("--pdf")
        pdf = args[i + 1]
        del args[i:i + 2]
    res = check_page(args[0], pdf)
    s = res["summary"]
    print("%s: %s, %d errors, %d warnings (%.1fs)" % (args[0], s["verdict"], s["errors"], s["warnings"], s["seconds"]))
    for u in res["units"]:
        for f in u["findings"]:
            if f["severity"] != "info":
                print("  %-5s %-6s %-16s %s" % (f["severity"], u["id"], f["check"], f["message"]))
    for f in res["page"]:
        if f["severity"] != "info":
            print("  %-5s %-6s %-16s %s" % (f["severity"], "page", f["check"], f["message"]))
