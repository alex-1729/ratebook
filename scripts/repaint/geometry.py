"""Page geometry for the brochure repaint, from rasters rather than path bounding boxes.

The page is rendered with all text removed ("graphics-only"). A text's container is the
connected region of pixels that have the colour found behind the text (flood fill with a
tolerance against the seed colour, not neighbour to neighbour, so gradients do not leak).
That is the real shape: a wavy panel, a pill, a slanted banner, a table cell bounded by
rules, the flat area of an image, or the open page background. Free space is tested
against that region plus every other text, with padding, through an integral image.
"""
import numpy as np
import fitz
from scipy import ndimage

S = 3.0          # analysis raster: pixels per point
TOL = 30         # colour tolerance (max channel difference, 0-255) for "same background"
LOW = 3          # stride for the low-resolution whole-page labelling


def render(page, scale):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    return a.copy()


def px(v, mode):
    return int(np.floor(v * S)) if mode == "lo" else int(np.ceil(v * S))


def rect_px(r, shape):
    """Outward-rounded pixel box (r0, r1, c0, c1) clipped to the array."""
    h, w = shape[:2]
    return (max(0, px(r.y0, "lo")), min(h, px(r.y1, "hi")), max(0, px(r.x0, "lo")), min(w, px(r.x1, "hi")))


def colour_dist(img, seed):
    return np.abs(img.astype(np.int16) - np.asarray(seed, np.int16)).max(axis=2)


class Grid:
    """Integral image over a boolean 'blocked' mask covering window `win` (points)."""

    def __init__(self, blocked, win, transposed=False):
        self.blocked = blocked
        self.ii = np.zeros((blocked.shape[0] + 1, blocked.shape[1] + 1), np.int32)
        self.ii[1:, 1:] = blocked.astype(np.int32).cumsum(0).cumsum(1)
        self.win = fitz.Rect(win)
        self.h, self.w = blocked.shape
        self.t = transposed

    def count(self, r):
        """Blocked pixels inside rect r (points, page or transposed space). Outside the window counts as blocked."""
        if self.t:
            ox, oy = self.win.y0, self.win.x0
        else:
            ox, oy = self.win.x0, self.win.y0
        c0 = int(np.floor((r.x0 - ox) * S + 1e-6))
        c1 = int(np.ceil((r.x1 - ox) * S - 1e-6))
        r0 = int(np.floor((r.y0 - oy) * S + 1e-6))
        r1 = int(np.ceil((r.y1 - oy) * S - 1e-6))
        if c0 < 0 or r0 < 0 or c1 > self.w or r1 > self.h:
            return 10 ** 7
        if c1 <= c0 or r1 <= r0:
            return 0
        ii = self.ii
        return int(ii[r1, c1] - ii[r0, c1] - ii[r1, c0] + ii[r0, c0])

    def transposed(self):
        return Grid(self.blocked.T, self.win, transposed=not self.t)


class PageGraphics:
    """The graphics-only rendering of a page and the analyses built on it."""

    def __init__(self, page_gfx):
        self.page_rect = fitz.Rect(page_gfx.rect)
        self.img = render(page_gfx, S)                      # H x W x 3, text removed
        self.low = self.img[::LOW, ::LOW]
        self._low_labels = {}

    # ---------------------------------------------------------------- colours
    def seed(self, rect):
        """Most common colour behind a text box (quantised mode, then mean of that bucket)."""
        r = fitz.Rect(rect)
        sh = min(0.4, r.width / 4, r.height / 4)
        r = fitz.Rect(r.x0 + sh, r.y0 + sh, r.x1 - sh, r.y1 - sh)
        r0, r1, c0, c1 = rect_px(r, self.img.shape)
        crop = self.img[r0:max(r1, r0 + 1), c0:max(c1, c0 + 1)].reshape(-1, 3)
        if crop.size == 0:
            return (255, 255, 255)
        q = (crop // 12).astype(np.int32)
        keys = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
        vals, counts = np.unique(keys, return_counts=True)
        k = vals[np.argmax(counts)]
        sel = crop[keys == k]
        return tuple(int(v) for v in sel.mean(axis=0).round())

    def inside_colour(self, rect):
        """Most common colour inside rect (the background of text set in an image region)."""
        return self.seed(rect)

    def ring_colour(self, rect, width=2.0):
        """Median colour of a ring just outside rect (for patches over raster text)."""
        r = fitz.Rect(rect)
        outer = fitz.Rect(r.x0 - width, r.y0 - width, r.x1 + width, r.y1 + width) & self.page_rect
        r0, r1, c0, c1 = rect_px(outer, self.img.shape)
        i0, i1, j0, j1 = rect_px(r, self.img.shape)
        m = np.ones((r1 - r0, c1 - c0), bool)
        m[max(0, i0 - r0):max(0, i1 - r0), max(0, j0 - c0):max(0, j1 - c0)] = False
        pix = self.img[r0:r1, c0:c1][m]
        if pix.size == 0:
            return (255, 255, 255)
        return tuple(int(v) for v in np.median(pix, axis=0))

    # ---------------------------------------------------------------- regions
    def low_region(self, seed, rect):
        """Whole-page (low resolution) component of the seed colour under rect.
        Returns dict(area_frac, bbox (points), touches_edge)."""
        key = tuple(int(v) // 6 for v in seed)
        if key not in self._low_labels:
            if len(self._low_labels) > 12:
                self._low_labels.clear()
            # pool the full-resolution mask: a low-res pixel is background only if all of its
            # pixels are, so hairline rules still separate regions
            full = colour_dist(self.img, seed) <= TOL
            h, w = (full.shape[0] // LOW) * LOW, (full.shape[1] // LOW) * LOW
            mask = full[:h, :w].reshape(h // LOW, LOW, w // LOW, LOW).all(axis=(1, 3))
            lab, n = ndimage.label(mask)
            objs = ndimage.find_objects(lab)
            areas = np.bincount(lab.ravel())
            self._low_labels[key] = (lab, objs, areas)
        lab, objs, areas = self._low_labels[key]
        f = S / LOW
        r = fitz.Rect(rect)
        r0, r1 = max(0, int(r.y0 * f)), min(lab.shape[0], int(np.ceil(r.y1 * f)))
        c0, c1 = max(0, int(r.x0 * f)), min(lab.shape[1], int(np.ceil(r.x1 * f)))
        sub = lab[r0:max(r1, r0 + 1), c0:max(c1, c0 + 1)]
        ids = sub[sub > 0]
        if ids.size == 0:
            return {"area_frac": 0.0, "bbox": fitz.Rect(rect), "touches_edge": False}
        cid = int(np.bincount(ids).argmax())
        sl = objs[cid - 1]
        bbox = fitz.Rect(sl[1].start / f, sl[0].start / f, sl[1].stop / f, sl[0].stop / f)
        touches = sl[0].start == 0 or sl[1].start == 0 or sl[0].stop >= lab.shape[0] - 1 or sl[1].stop >= lab.shape[1] - 1
        return {"area_frac": float(areas[cid]) / lab.size, "bbox": bbox, "touches_edge": bool(touches)}

    def region_mask(self, seed, O, win, clip=None):
        """Full-resolution container region inside window `win`: the seed-coloured components
        that lie under the text box O. Returns bool array of the window."""
        r0, r1, c0, c1 = rect_px(win, self.img.shape)
        crop = self.img[r0:r1, c0:c1]
        mask = colour_dist(crop, seed) <= TOL
        if clip is not None:   # an image used as a box: its edge is the container's edge
            keep = np.zeros_like(mask)
            cr = fitz.Rect(clip) & win
            keep[max(0, int(np.ceil((cr.y0 - win.y0) * S))):max(0, int(np.floor((cr.y1 - win.y0) * S))),
                 max(0, int(np.ceil((cr.x0 - win.x0) * S))):max(0, int(np.floor((cr.x1 - win.x0) * S)))] = True
            mask &= keep
        lab, n = ndimage.label(mask)
        o = fitz.Rect(O)
        i0, i1, j0, j1 = rect_px(o, self.img.shape)
        sub = lab[max(0, i0 - r0):max(0, i1 - r0), max(0, j0 - c0):max(0, j1 - c0)]
        ids, counts = np.unique(sub[sub > 0], return_counts=True)
        if ids.size == 0:
            return np.zeros_like(mask)
        need = max(3, 0.02 * sub.size)
        keep = ids[counts >= need]
        if keep.size == 0:
            keep = ids[[np.argmax(counts)]]
        return np.isin(lab, keep)

    def window(self, O, margin):
        return fitz.Rect(O.x0 - margin, O.y0 - margin, O.x1 + margin, O.y1 + margin) & self.page_rect


def mask_rect(mask, win, r, value=True):
    """Set the pixels of rect r (points) in a window mask."""
    rr = fitz.Rect(r) & win
    if rr.is_empty:
        return
    e = 0 if value else 1      # clearing is generous by a pixel so rounding never leaves a fringe
    r0 = max(0, int(np.floor((rr.y0 - win.y0) * S + 1e-6)) - e)
    r1 = min(mask.shape[0], int(np.ceil((rr.y1 - win.y0) * S - 1e-6)) + e)
    c0 = max(0, int(np.floor((rr.x0 - win.x0) * S + 1e-6)) - e)
    c1 = min(mask.shape[1], int(np.ceil((rr.x1 - win.x0) * S - 1e-6)) + e)
    mask[r0:r1, c0:c1] = value


def run_extent(mask, win, y, x):
    """Horizontal run of True pixels in `mask` on the row at y that contains x (points)."""
    i = int((y - win.y0) * S)
    j = int((x - win.x0) * S)
    if not (0 <= i < mask.shape[0] and 0 <= j < mask.shape[1]):
        return None
    row = mask[i]
    if not row[j]:
        return None
    left = j
    while left > 0 and row[left - 1]:
        left -= 1
    right = j
    while right < len(row) - 1 and row[right + 1]:
        right += 1
    return (win.x0 + left / S, win.x0 + (right + 1) / S)


def col_extent(mask, win, x, y):
    i = int((y - win.y0) * S)
    j = int((x - win.x0) * S)
    if not (0 <= i < mask.shape[0] and 0 <= j < mask.shape[1]):
        return None
    col = mask[:, j]
    if not col[i]:
        return None
    top = i
    while top > 0 and col[top - 1]:
        top -= 1
    bot = i
    while bot < len(col) - 1 and col[bot + 1]:
        bot += 1
    return (win.y0 + top / S, win.y0 + (bot + 1) / S)


def inner_components(blocked_in_O, O, win, min_px=4):
    """Blocked components strictly inside O (not touching its border): inline obstacles such as a
    page badge inside a sentence. Returns rects in points."""
    lab, n = ndimage.label(blocked_in_O)
    out = []
    for k, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        r0, r1, c0, c1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if r0 == 0 or c0 == 0 or r1 >= lab.shape[0] or c1 >= lab.shape[1]:
            continue
        if (lab[sl] == k).sum() < min_px:
            continue
        out.append(fitz.Rect(O.x0 + c0 / S, O.y0 + r0 / S, O.x0 + c1 / S, O.y0 + r1 / S))
    return out
