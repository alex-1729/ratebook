"""Typesetting for the brochure repaint: fonts, runs, line breaking and drawing.

The engine sets text itself (no Story/htmlbox) so it knows exactly where every line and
glyph goes: lines are broken with real font metrics, every segment keeps its own size,
colour and weight, and the measured block is what gets drawn.

One family: Noto Sans regular / bold from pymupdf-fonts. Characters Noto Sans lacks
(geometric bullets, circled numbers, arrows) fall back to Noto Sans Symbols / Symbols2.
"""
import re
import unicodedata

import fitz

LINE_GAP = 1.22          # default line height as a multiple of the largest size on the line
INK_UP, INK_DOWN = 0.76, 0.24   # glyph ink above / below the baseline, in em (Noto Sans)

_FONT_NAMES = {"regular": "notos", "bold": "notosbo", "sym2": "symbol2", "sym1": "symbol1", "math": "math"}
_fonts = {}
_glyph_cache = {}
_width_cache = {}

# Japanese marks and full-width forms that have a plain English equivalent
CHAR_MAP = {
    "　": " ", "、": ", ", "。": ". ", "〈": "<", "〉": ">", "《": "<<", "》": ">>",
    "「": "“", "」": "”", "『": "“", "』": "”", "【": "[", "】": "]",
    "〔": "(", "〕": ")", "〜": "~", "－": "-", "￥": "¥", "”": "”", "“": "“",
}
MARK_CHARS = set("●○•・■□◆◇▲△▶►◎★☆※＊*")
ENUM_RE = re.compile(r"^\s*(\(\d{1,2}\)|\d{1,2}\)|[①-⑳]|\*\d+|※\d*|\*)\s+")
OPEN_BR, CLOSE_BR = "([{“‘<", ")]}”’>"


def font(key):
    if key not in _fonts:
        _fonts[key] = fitz.Font(_FONT_NAMES[key])
    return _fonts[key]


def font_key_for(ch, bold):
    k = (ch, bold)
    if k not in _glyph_cache:
        base = "bold" if bold else "regular"
        res = None
        if ch in "  " or font(base).has_glyph(ord(ch)):
            res = base
        else:
            for alt in ("sym2", "sym1", "math"):
                if font(alt).has_glyph(ord(ch)):
                    res = alt
                    break
        _glyph_cache[k] = res
    return _glyph_cache[k]


def text_width(text, fkey, size):
    k = (text, fkey)
    if k not in _width_cache:
        _width_cache[k] = font(fkey).text_length(text.replace(" ", " "), fontsize=1)
    return _width_cache[k] * size


def normalise_text(t, leading=False):
    """Map full-width forms and Japanese punctuation to English typography.
    Returns (text, list of (from, to) replacements)."""
    out, repl = [], []
    for i, ch in enumerate(t):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            new = chr(o - 0xFEE0)
        elif ch == "・":
            new = "•" if (leading and i == 0) else "·"
        elif ch in CHAR_MAP:
            new = CHAR_MAP[ch]
        else:
            new = ch
        if new != ch:
            repl.append((ch, new))
        out.append(new)
    return "".join(out), repl


class Style:
    __slots__ = ("size", "color", "bold")

    def __init__(self, size, color, bold):
        self.size, self.color, self.bold = size, color, bold

    def key(self):
        return (round(self.size, 2), self.color, self.bold)


class Para:
    """A unit's English as a sequence of styled characters with forced breaks."""

    def __init__(self, segments, styles):
        """segments: list of dicts {t, br}; styles: parallel list of Style."""
        self.chars = []          # (ch, style_index)
        self.styles = styles
        self.breaks = set()      # char index *before which* a forced break happens
        self.replacements = []
        self.missing = []
        first = True
        for si, seg in enumerate(segments):
            t, repl = normalise_text(seg.get("t", ""), leading=first and bool(seg.get("t", "").strip()))
            self.replacements += repl
            if first and t.strip():
                t = t.lstrip()
                # a leading mark glued to the text ("・Target", "●Choose") gets a space
                if len(t) >= 2 and t[0] in MARK_CHARS | set("•·") and not t[1].isspace() and t[1] not in "0123456789":
                    t = t[0] + " " + t[1:]
                elif len(t) == 1 and t in MARK_CHARS | set("•"):
                    t = t + " "
                first = False
            for ch in t:
                if ch != " " and font_key_for(ch, styles[si].bold) is None:
                    self.missing.append(ch)
                self.chars.append((ch, si))
            if seg.get("br"):
                self.breaks.add(len(self.chars))
        # collapse doubled spaces created by segment joins
        cleaned, brk = [], set()
        for i, (ch, si) in enumerate(self.chars):
            if i in self.breaks:
                brk.add(len(cleaned))
                while cleaned and cleaned[-1][0] == " ":
                    cleaned.pop()
                if ch == " ":
                    continue
            if ch == " " and (not cleaned or cleaned[-1][0] == " " or len(cleaned) in brk):
                continue
            cleaned.append((ch, si))
        while cleaned and cleaned[-1][0] == " ":
            cleaned.pop()
        self.chars, self.breaks = cleaned, {b for b in brk if 0 < b < len(cleaned)}
        self.text = "".join(c for c, _ in self.chars)
        self.structured = bool(self.breaks)

    def hang_chars(self):
        """Number of leading characters (mark or enumerator plus space) that hang."""
        m = ENUM_RE.match(self.text)
        if m:
            return m.end()
        if self.text[:1] in MARK_CHARS | {"•"} and self.text[1:2] == " ":
            return 2
        return 0


class Glyphs:
    """A run of characters in one font/size/colour, positioned on a line."""
    __slots__ = ("text", "fkey", "size", "color", "x", "w")

    def __init__(self, text, fkey, size, color):
        self.text, self.fkey, self.size, self.color = text, fkey, size, color
        self.x, self.w = 0.0, text_width(text, fkey, size)


def _words(para, sizes, glue_brackets):
    """Split into words. Each word: dict(pieces=[Glyphs], w, space_after_w, forced_break_before,
    soft=list of split points)."""
    words = []
    cur, depth = [], 0
    pending_break = False

    def flush(space_style=None):
        nonlocal cur, pending_break
        if cur:
            words.append({"chars": cur, "space": space_style, "brk": pending_break})
            pending_break = False
        cur = []

    for i, (ch, si) in enumerate(para.chars):
        if i in para.breaks:
            flush()
            pending_break = True
        if ch in OPEN_BR:
            depth += 1
        elif ch in CLOSE_BR:
            depth = max(0, depth - 1)
        if ch == " " and not (glue_brackets and depth > 0):
            flush(si)
            continue
        cur.append((ch, si))
    flush()
    out = []
    for w in words:
        pieces = _pieces(w["chars"], para, sizes)
        sp = 0.0
        if w["space"] is not None:
            st = para.styles[w["space"]]
            sp = text_width(" ", "bold" if st.bold else "regular", sizes[w["space"]])
        out.append({"chars": w["chars"], "pieces": pieces, "w": sum(p.w for p in pieces), "sp": sp,
                    "brk": w["brk"], "hmax": max(sizes[si] for _, si in w["chars"])})
    return out


def _pieces(chars, para, sizes):
    pieces = []
    for ch, si in chars:
        st = para.styles[si]
        fk = font_key_for(ch, st.bold) or ("bold" if st.bold else "regular")
        key = (fk, sizes[si], st.color)
        if pieces and (pieces[-1].fkey, pieces[-1].size, pieces[-1].color) == key:
            pieces[-1].text += ch
        else:
            pieces.append(Glyphs(ch, fk, sizes[si], st.color))
    for p in pieces:
        p.w = text_width(p.text, p.fkey, p.size)
    return pieces


def _split_word(word, para, sizes, maxw):
    """Split an over-long word after '/', '-' or '–' so the first part fits maxw. None if impossible."""
    chars = word["chars"]
    best = None
    for i, (ch, _) in enumerate(chars[:-1]):
        if ch in "/-–":
            head = chars[:i + 1]
            if sum(p.w for p in _pieces(head, para, sizes)) <= maxw:
                best = i + 1
    if best is None:
        return None
    a, b = chars[:best], chars[best:]
    pa, pb = _pieces(a, para, sizes), _pieces(b, para, sizes)
    wa = {"chars": a, "pieces": pa, "w": sum(p.w for p in pa), "sp": 0.0, "brk": word["brk"],
          "hmax": max(sizes[si] for _, si in a)}
    wb = {"chars": b, "pieces": pb, "w": sum(p.w for p in pb), "sp": word["sp"], "brk": False,
          "hmax": max(sizes[si] for _, si in b)}
    return wa, wb


class Block:
    """Result of setting a paragraph into a frame (local coordinates, y down)."""

    def __init__(self):
        self.lines = []      # dicts: x0, x1, top, base, size, pieces (with absolute x)
        self.ok = True
        self.bracket_broken = False
        self.word_split = False
        self.nlines = 0
        self.ink = None      # fitz.Rect of the ink box


def set_para(para, scale, floor_of, frame, align, anchor_x, anchor_y, valign, line_gap=LINE_GAP,
             obstacles=(), strict=True, hang=None, allow_overflow=False, keep_lines=False):
    """Set `para` at `scale` into `frame` (fitz.Rect, local coords).

    floor_of(i) -> minimum size for style i. align: left|center|right; valign: top|middle.
    anchor_x: left edge / centre / right edge; anchor_y: ink top (top) or ink centre (middle).
    obstacles: rects inside the frame that lines must flow around.
    Returns a Block; block.ok is False when it does not fit (unless allow_overflow)."""
    sizes = [max(min(st.size, st.size * scale), floor_of(i)) for i, st in enumerate(para.styles)]
    words = _words(para, sizes, glue_brackets=strict and para.structured)
    blk = Block()
    if not words:
        blk.ok = False
        return blk
    hang_w = 0.0
    hc = para.hang_chars() if hang is None else hang
    if hc:
        # width of the leading words that make up the hanging mark/enumerator
        n = 0
        for w in words:
            if n >= hc:
                break
            hang_w += w["w"] + w["sp"]
            n += len(w["chars"]) + 1
    est_h = None
    lines = None
    for _ in range(3):
        lines = _break(words, para, sizes, frame, align, anchor_x, anchor_y, valign, line_gap, obstacles,
                       hang_w, est_h, strict, blk, force=allow_overflow, keep_lines=keep_lines and para.structured)
        if lines is None:
            blk.ok = False
            return blk
        h = _ink_height(lines)
        if not obstacles or (est_h is not None and abs(h - est_h) < 0.01):
            est_h = h
            break
        est_h = h
    ink_top = _ink_top(est_h, frame, anchor_y, valign)
    y0 = ink_top - (lines[0]["lh"] - lines[0]["size"]) / 2
    for ln in lines:
        ln["top"] = y0 + ln["off"]          # offsets keep any band skipped for an obstacle
        ln["base"] = ln["top"] + (ln["lh"] - ln["size"]) / 2 + INK_UP * ln["size"]
    blk.lines = lines
    blk.nlines = len(lines)
    blk.ink = fitz.Rect(min(l["x0"] for l in lines), lines[0]["base"] - INK_UP * lines[0]["size"],
                        max(l["x1"] for l in lines), lines[-1]["base"] + INK_DOWN * lines[-1]["size"])
    eps = 0.05
    ink = blk.ink
    if not allow_overflow:
        if ink.y0 < frame.y0 - eps or ink.y1 > frame.y1 + eps or ink.x0 < frame.x0 - eps or ink.x1 > frame.x1 + eps:
            blk.ok = False
    blk.sizes = sizes
    return blk


def _ink_height(lines):
    if not lines:
        return 0.0
    h = lines[-1]["off"] + lines[-1]["lh"]
    return h - (lines[0]["lh"] - lines[0]["size"]) / 2 - (lines[-1]["lh"] - lines[-1]["size"]) / 2


def _ink_top(ink_h, frame, anchor_y, valign):
    """Ink top of the block: at the anchor, kept inside the frame (overflow goes downward)."""
    t = anchor_y - ink_h / 2 if valign == "middle" else anchor_y
    t = min(t, frame.y1 - ink_h)
    return max(t, frame.y0)


def _break(words, para, sizes, frame, align, anchor_x, anchor_y, valign, line_gap, obstacles, hang_w, est_h,
           strict, blk, force=False, keep_lines=False):
    """Greedy line breaking with per-line available intervals. With force, a line that cannot hold
    its first word still takes it (the block then overflows its frame)."""
    maxsize = max(sizes)
    ink_top = _ink_top(est_h if est_h is not None else maxsize, frame, anchor_y, valign)
    lines = []
    i = 0
    y_ink = ink_top
    words = list(words)
    first_line = True
    while i < len(words):
        # line band (ink) estimate using the size of the next word
        lh_est = line_gap * maxsize
        band = (y_ink - 0.1 * maxsize, y_ink + maxsize * 1.05)
        x_lo, x_hi = frame.x0, frame.x1
        indent = 0.0 if first_line else hang_w
        intervals = _intervals(x_lo + indent, x_hi, band, obstacles)
        if not intervals:
            if not force:
                return None
            intervals = [(x_lo + indent, max(x_hi, x_lo + indent + 1))]
        # use the widest interval reachable in reading order; text flows into later intervals
        cur = []
        line_pieces = []
        placed_any = False
        for (a, b) in intervals:
            width = b - a
            x = 0.0
            seg_words = []
            while i < len(words):
                w = words[i]
                if w["brk"] and (cur or seg_words or placed_any):
                    break
                need = x + w["w"]
                if need <= width + 1e-6:
                    seg_words.append((x, w))
                    x = need + w["sp"]
                    i += 1
                    continue
                if keep_lines and (seg_words or placed_any):
                    return None     # a structured label may only break where the translator put a break
                if not seg_words and not cur and not placed_any and len(intervals) == 1:
                    # a single word wider than the line
                    if not strict:
                        sp = _split_word(w, para, sizes, width)
                        if sp:
                            words[i:i + 1] = list(sp)
                            blk.word_split = True
                            continue
                    if force:
                        seg_words.append((x, w))
                        x = x + w["w"] + w["sp"]
                        i += 1
                        break
                    return None
                break
            if seg_words:
                placed_any = True
                line_pieces.append((a, b, seg_words))
            if i < len(words) and words[i]["brk"]:
                break
        if not placed_any:
            # nothing fits in any interval on this band: skip down a little (obstacle) or fail
            if obstacles and y_ink < frame.y1:
                y_ink += 0.25 * maxsize
                continue
            if force and i < len(words):
                line_pieces.append((intervals[0][0], intervals[0][1], [(0.0, words[i])]))
                i += 1
            else:
                return None
        # build the line
        size_line = max(w["hmax"] for _, _, sw in line_pieces for _, w in sw)
        lh = line_gap * size_line
        pieces = []
        x0s, x1s = [], []
        for (a, b, seg_words) in line_pieces:
            used = seg_words[-1][0] + seg_words[-1][1]["w"]
            if len(line_pieces) == 1 and len(intervals) == 1:
                if align == "center":
                    start = anchor_x - used / 2
                    start = min(max(start, a), b - used)
                elif align == "right":
                    start = b - used
                else:
                    start = a
            else:
                start = a
            for (xw, w) in seg_words:
                xx = start + xw
                for p in w["pieces"]:
                    g = Glyphs(p.text, p.fkey, p.size, p.color)
                    g.x = xx
                    xx += g.w
                    pieces.append(g)
            x0s.append(start)
            x1s.append(start + used)
        lines.append({"x0": min(x0s), "x1": max(x1s), "size": size_line, "lh": lh, "pieces": pieces,
                      "off": y_ink - ink_top})
        y_ink += lh
        first_line = False
        # detect a bracketed phrase broken across lines
        if i < len(words) and line_pieces:
            last = "".join(c for c, _ in line_pieces[-1][2][-1][1]["chars"])
            depth = sum(last.count(c) for c in "(") - sum(last.count(c) for c in ")")
            if depth > 0:
                blk.bracket_broken = True
    # bracket balance across the whole line text
    for ln in lines:
        t = "".join(p.text for p in ln["pieces"])
        if t.count("(") != t.count(")"):
            blk.bracket_broken = True
    return lines


def _intervals(a, b, band, obstacles):
    ivs = [(a, b)]
    for r in obstacles:
        if r.y1 <= band[0] or r.y0 >= band[1]:
            continue
        new = []
        for (s, e) in ivs:
            if r.x1 <= s or r.x0 >= e:
                new.append((s, e))
                continue
            if r.x0 > s:
                new.append((s, r.x0))
            if r.x1 < e:
                new.append((r.x1, e))
        ivs = new
    return [(s, e) for s, e in ivs if e - s > 2.0]


def draw_block(page, blk, origin=None):
    """Draw a set block. Without origin, local coordinates are page coordinates. With origin
    (a page point), the block was set in a local frame starting at (0, 0) and is turned 90 degrees
    counter-clockwise about that point (reads bottom to top)."""
    writers = {}
    ox, oy = (origin.x, origin.y) if origin is not None else (0.0, 0.0)
    for ln in blk.lines:
        for g in ln["pieces"]:
            if not g.text.strip():
                continue
            col = g.color
            if col not in writers:
                writers[col] = fitz.TextWriter(page.rect, color=tuple(((col >> s) & 255) / 255 for s in (16, 8, 0)))
            writers[col].append(fitz.Point(ox + g.x, oy + ln["base"]), g.text.replace("\u00a0", " "),
                                font=font(g.fkey), fontsize=g.size)
    for tw in writers.values():
        if origin is not None:
            tw.write_text(page, morph=(origin, fitz.Matrix(90)))
        else:
            tw.write_text(page)
