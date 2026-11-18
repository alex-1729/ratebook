"""Draw the Ratebook icon (navy tile, white step line, orange point). Standard library only.

    python3 app/build/make-icon.py

Writes, next to this file:
  icon.png      1024px, tile fills the canvas (Windows; electron-builder makes the .ico from it)
  icon-mac.png  1024px, tile inset to the macOS icon grid (also the Dock icon for `npm start`)
  icon.icns     from icon-mac.png, macOS only (needs sips and iconutil)
icon.svg is the same drawing for reference; keep the two in step by hand.
"""
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 1024
NAVY, WHITE, ORANGE = (0x20, 0x34, 0x4F), (0xFB, 0xF8, 0xF2), (0xEB, 0x68, 0x34)
# Geometry on a 100-unit tile, as in icon.svg.
RADIUS = 22
LINE = [(18, 70), (34, 70), (34, 54), (50, 54), (50, 62), (64, 62), (64, 36), (80, 36)]
LINE_HALF = 3
DOT, DOT_R = (80, 36), 7


def seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def render(inset):
    """inset: margin around the tile, as a fraction of the canvas."""
    scale = SIZE * (1 - 2 * inset) / 100  # pixels per tile unit
    off = SIZE * inset
    segs = list(zip(LINE, LINE[1:]))
    rows = []
    for y in range(SIZE):
        row = bytearray([0])  # PNG filter byte
        v = (y + 0.5 - off) / scale
        for x in range(SIZE):
            u = (x + 0.5 - off) / scale
            # Rounded square, signed distance in pixels.
            qx, qy = abs(u - 50) - (50 - RADIUS), abs(v - 50) - (50 - RADIUS)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - RADIUS
            alpha = max(0.0, min(1.0, 0.5 - d * scale))
            if alpha == 0:
                row += b"\0\0\0\0"
                continue
            r, g, b = NAVY
            if 12 < u < 90 and 26 < v < 76:
                ld = min(seg_dist(u, v, p, q) for p, q in segs) - LINE_HALF
                c = max(0.0, min(1.0, 0.5 - ld * scale))
                r, g, b = (r + (WHITE[0] - r) * c, g + (WHITE[1] - g) * c, b + (WHITE[2] - b) * c)
                dd = math.hypot(u - DOT[0], v - DOT[1]) - DOT_R
                c = max(0.0, min(1.0, 0.5 - dd * scale))
                r, g, b = (r + (ORANGE[0] - r) * c, g + (ORANGE[1] - g) * c, b + (ORANGE[2] - b) * c)
            row += bytes((round(r), round(g), round(b), round(alpha * 255)))
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, raw):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icns(src, dest):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "icon.iconset")
        os.mkdir(iconset)
        for pts in (16, 32, 128, 256, 512):
            for mult, suffix in ((1, ""), (2, "@2x")):
                out = os.path.join(iconset, f"icon_{pts}x{pts}{suffix}.png")
                subprocess.run(["sips", "-z", str(pts * mult), str(pts * mult), src, "--out", out],
                               check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", dest], check=True)


write_png(os.path.join(HERE, "icon.png"), render(0))
write_png(os.path.join(HERE, "icon-mac.png"), render(100 / 1024))
if sys.platform == "darwin" and shutil.which("iconutil"):
    make_icns(os.path.join(HERE, "icon-mac.png"), os.path.join(HERE, "icon.icns"))
else:
    print("icon.icns not rebuilt (macOS only)")
