"""Raster helpers for checks.py: renders, ink masks, edge maps, container regions, content box.

All renders are RGB uint8 numpy arrays at DPI dots per inch; a point p maps to pixel p * S.
"""
import numpy as np
import fitz
from scipy import ndimage as ndi

DPI = 200
S = DPI / 72.0
INK = 40          # channel difference that counts as ink
CORE = 90         # ... as solid ink (ignores anti-aliased fringes)
EDGE = 48         # neighbour difference that counts as an edge in the graphics
REGION_TOL = 26   # colour distance still inside the same filled area
SEED_SHARE = 0.06 # share of the original text's area a colour needs to count as part of its container


def render(page, dpi=DPI):
    pm = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
    return np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, :3].copy()


def strip_text(page):
    """Remove every text object from the page in place, leaving images and vector graphics untouched."""
    page.add_redact_annot(page.rect, fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)


def diff(a, b):
    return np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)


def edges(img, thr=EDGE):
    a = img.astype(np.int16)
    gx = np.abs(np.diff(a, axis=1)).max(axis=2) > thr
    gy = np.abs(np.diff(a, axis=0)).max(axis=2) > thr
    e = np.zeros(img.shape[:2], bool)
    e[:, 1:] |= gx
    e[:, :-1] |= gx
    e[1:, :] |= gy
    e[:-1, :] |= gy
    return e


def px(rect, shape, pad=0.0):
    """Point rect -> clipped pixel slice bounds (x0, y0, x1, y1)."""
    h, w = shape[:2]
    x0 = int(np.floor((rect[0] - pad) * S)); y0 = int(np.floor((rect[1] - pad) * S))
    x1 = int(np.ceil((rect[2] + pad) * S)); y1 = int(np.ceil((rect[3] + pad) * S))
    return max(0, x0), max(0, y0), min(w, max(0, x1)), min(h, max(0, y1))


def box_mask(boxes, shape, win=None, inset=0.0):
    """Boolean mask of the union of point boxes, in full-page or window coordinates."""
    if win is None:
        win = (0, 0, shape[1], shape[0])
    wx0, wy0, wx1, wy1 = win
    m = np.zeros((wy1 - wy0, wx1 - wx0), bool)
    for b in boxes:
        x0 = int(round((b[0] + inset) * S)) - wx0; y0 = int(round((b[1] + inset) * S)) - wy0
        x1 = int(round((b[2] - inset) * S)) - wx0; y1 = int(round((b[3] - inset) * S)) - wy0
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(m.shape[1], x1), min(m.shape[0], y1)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
    return m


def mask_bbox_pt(mask, win):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [round((xs.min() + win[0]) / S, 2), round((ys.min() + win[1]) / S, 2),
            round((xs.max() + 1 + win[0]) / S, 2), round((ys.max() + 1 + win[1]) / S, 2)]


def dominant_colour(pixels):
    q = (pixels.astype(np.int32) // 8)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    k = vals[counts.argmax()]
    sel = pixels[keys == k]
    return sel.mean(axis=0)


def region(gfx_win, seed, tol=REGION_TOL, parts=None):
    """The filled area the original text sat in: pixels connected to the seed pixels whose colour
    stays within tol of a colour found under the original text. Returns (mask, main colour) or
    (None, None).

    Text may legitimately straddle several filled areas (a tab and a pill inside it, a digit on a
    small white disc, letters on their own drop shadow), so besides the dominant colour of the whole
    seed area, the dominant colour under each character (`parts`: list of (y0, y1, x0, x1) windows)
    also counts when it fills a good share of that character's box."""
    if seed.sum() < 4:
        return None, None
    g16 = gfx_win.astype(np.int16)
    colour = dominant_colour(gfx_win[seed])
    cands = [colour]
    total = seed.sum()
    rem = seed & (np.abs(g16 - colour.astype(np.int16)).max(axis=2) > tol)
    if rem.sum() >= SEED_SHARE * total:
        cands.append(dominant_colour(gfx_win[rem]))
    for (y0, y1, x0, x1) in parts or []:
        sm = seed[y0:y1, x0:x1]
        if sm.sum() < 6:
            continue
        c = dominant_colour(gfx_win[y0:y1, x0:x1][sm])
        share = (np.abs(g16[y0:y1, x0:x1][sm] - c.astype(np.int16)).max(axis=1) <= tol).mean()
        if share >= 0.3 and all(np.abs(c - k).max() > tol for k in cands):
            cands.append(c)
        rest = np.abs(g16[y0:y1, x0:x1] - c.astype(np.int16)).max(axis=2) > tol
        rest &= sm
        if rest.sum() >= 0.25 * sm.sum():
            c2 = dominant_colour(gfx_win[y0:y1, x0:x1][rest])
            if all(np.abs(c2 - k).max() > tol for k in cands):
                cands.append(c2)
        if len(cands) > 8:
            break
    out = np.zeros(seed.shape, bool)
    for c in cands:
        close = np.abs(g16 - c.astype(np.int16)).max(axis=2) <= tol
        lab, n = ndi.label(close)
        seeds_in = lab[seed & close]
        seeds_in = seeds_in[seeds_in > 0]
        if len(seeds_in):
            ids, counts = np.unique(seeds_in, return_counts=True)
            out |= np.isin(lab, ids[counts >= 4])
    if not out.any():
        return None, None
    return out, colour


def dist_inside(R):
    """For pixels in R: distance (px) to the nearest pixel outside R; the window edge does not count."""
    Rp = np.pad(R, 1, constant_values=True)
    return ndi.distance_transform_edt(Rp)[1:-1, 1:-1]


def dist_outside(R):
    """For pixels outside R: distance (px) to the nearest pixel of R."""
    nR = np.pad(~R, 1, constant_values=True)
    return ndi.distance_transform_edt(nR)[1:-1, 1:-1]


def bleed_mask(gfx, step=6, tol=24):
    """Coloured areas that touch the page edge but are not the page background (bands, tabs, bleeds),
    at full resolution."""
    small = gfx[::step, ::step].astype(np.int16)
    h, w = small.shape[:2]
    border = np.concatenate([small[0], small[-1], small[:, 0], small[:, -1]])
    q = border // 16
    keys = np.unique(q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2])
    comps = []
    for k in keys:
        c = np.array([k // 256, (k // 16) % 16, k % 16]) * 16 + 8
        close = np.abs(small - c).max(axis=2) <= tol
        lab, n = ndi.label(close)
        edge_ids = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        for i in edge_ids:
            m = lab == i
            comps.append((int(m.sum()), m))
    if not comps:
        return np.zeros(gfx.shape[:2], bool)
    comps.sort(key=lambda c: -c[0])
    bg = comps[0][1]
    out = np.zeros((h, w), bool)
    for area, m in comps[1:]:
        if area > 0.002 * h * w and not (m & bg).all():
            out |= m & ~bg
    full = np.repeat(np.repeat(out, step, axis=0), step, axis=1)[:gfx.shape[0], :gfx.shape[1]]
    pad_h, pad_w = gfx.shape[0] - full.shape[0], gfx.shape[1] - full.shape[1]
    if pad_h or pad_w:
        full = np.pad(full, ((0, pad_h), (0, pad_w)), mode="edge")
    return full


def text_like_blobs(rgb):
    """Rough count of glyph-sized blobs arranged in rows inside an image crop (for 'image has text')."""
    g = rgb.astype(np.float32).mean(axis=2)
    if g.size < 400:
        return 0
    lo, hi = np.percentile(g, 10), np.percentile(g, 90)
    if hi - lo < 60:
        return 0
    thr = (lo + hi) / 2
    best = 0
    for m in (g < thr, g > thr):
        lab, n = ndi.label(m)
        if n == 0 or n > 4000:
            continue
        objs = ndi.find_objects(lab)
        hs = []
        for sl in objs:
            hh = sl[0].stop - sl[0].start
            ww = sl[1].stop - sl[1].start
            if 4 * S / 2.78 <= hh <= 14 * S and 1 <= ww <= 16 * S and hh * ww < 300 * S * S:
                hs.append(((sl[0].start + sl[0].stop) / 2, hh))
        # glyphs in rows: count blobs that share a row centre with at least 3 others
        hs.sort()
        rows = 0
        for i, (cy, hh) in enumerate(hs):
            near = sum(1 for cy2, h2 in hs if abs(cy2 - cy) < 0.3 * hh and abs(h2 - hh) < 0.5 * hh)
            if near >= 4:
                rows += 1
        best = max(best, rows)
    return best
