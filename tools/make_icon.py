"""Generate the application icon as a multi-resolution .ico.

Pure stdlib: a tiny supersampling rasteriser plus an ICO writer, so the logo is
reproducible from source and the repository carries no opaque binary blob.

    python tools/make_icon.py

Writes assets/autoclicker.ico (idle) and assets/autoclicker-running.ico.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib

SIZES = (16, 24, 32, 48, 64, 128, 256)

# The classic arrow pointer, in a 0..1 box. Tip top-left, split tail.
CURSOR = [
    (0.325, 0.150), (0.325, 0.790), (0.462, 0.655), (0.548, 0.858),
    (0.648, 0.812), (0.566, 0.614), (0.735, 0.598),
]

IDLE = {"top": (0x5B, 0x9C, 0xFF), "bottom": (0x2B, 0x5C, 0xD9)}
RUNNING = {"top": (0x56, 0xD9, 0x7F), "bottom": (0x1E, 0x8E, 0x4A)}


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def sd_round_rect(px, py, cx, cy, hw, hh, r):
    """Signed distance to a rounded rectangle; negative inside."""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    return (math.hypot(max(qx, 0.0), max(qy, 0.0))
            + min(max(qx, qy), 0.0) - r)


def in_polygon(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def scale_poly(poly, factor, ox=0.0, oy=0.0):
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return [(cx + (x - cx) * factor + ox, cy + (y - cy) * factor + oy)
            for x, y in poly]


def over(dst, src):
    """Standard source-over compositing on premultiplied-by-hand tuples."""
    sr, sg, sb, sa = src
    if sa <= 0:
        return dst
    dr, dg, db, da = dst
    out_a = sa + da * (1 - sa)
    if out_a <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    r = (sr * sa + dr * da * (1 - sa)) / out_a
    g = (sg * sa + dg * da * (1 - sa)) / out_a
    b = (sb * sa + db * da * (1 - sa)) / out_a
    return (r, g, b, out_a)


def sample(u, v, size, palette):
    """Colour at normalised point (u, v). Returns straight RGBA floats 0..1."""
    px = (0.0, 0.0, 0.0, 0.0)

    # --- badge -----------------------------------------------------------
    d = sd_round_rect(u, v, 0.5, 0.5, 0.44, 0.44, 0.215)
    if d < 0.0:
        t = min(1.0, max(0.0, (v - 0.06) / 0.88))
        top, bottom = palette["top"], palette["bottom"]
        # Slight ease so the gradient does not look like a linear ramp.
        t = t * t * (3 - 2 * t)
        colour = tuple((top[i] + (bottom[i] - top[i]) * t) / 255.0 for i in range(3))
        px = over(px, colour + (1.0,))
        # A soft highlight across the top third gives the badge some volume.
        if v < 0.45:
            glow = (1.0 - v / 0.45) ** 2 * 0.16
            px = over(px, (1.0, 1.0, 1.0, glow))

    # --- ripples (omitted at small sizes, where they turn to mush) --------
    if size >= 48:
        tip = (0.325, 0.150)
        for radius, alpha in ((0.30, 0.55), (0.42, 0.34)):
            dist = math.hypot(u - tip[0], v - tip[1])
            band = abs(dist - radius)
            width = 0.022
            if band < width:
                angle = math.degrees(math.atan2(v - tip[1], u - tip[0]))
                if -62.0 <= angle <= 30.0:
                    edge = 1.0 - band / width
                    px = over(px, (1.0, 1.0, 1.0, alpha * min(1.0, edge * 2.0)))

    # --- cursor ----------------------------------------------------------
    shadow = scale_poly(CURSOR, 1.10, 0.012, 0.022)
    if in_polygon(u, v, shadow):
        px = over(px, (0.03, 0.10, 0.25, 0.33))
    if in_polygon(u, v, CURSOR):
        px = over(px, (1.0, 1.0, 1.0, 1.0))

    return px


def render(size, palette):
    """Return BGRA bytes, top-down, straight alpha."""
    ss = 4 if size <= 64 else 2
    inv = 1.0 / (size * ss)
    rows = bytearray()
    weight = 1.0 / (ss * ss)
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0.0
            for sy in range(ss):
                v = (y * ss + sy + 0.5) * inv
                for sx in range(ss):
                    u = (x * ss + sx + 0.5) * inv
                    cr, cg, cb, ca = sample(u, v, size, palette)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            a *= weight
            if a > 0.0:
                # Un-premultiply back to straight alpha.
                r = min(1.0, r * weight / a)
                g = min(1.0, g * weight / a)
                b = min(1.0, b * weight / a)
            rows += bytes((int(b * 255 + 0.5), int(g * 255 + 0.5),
                           int(r * 255 + 0.5), int(a * 255 + 0.5)))
    return bytes(rows)


# --------------------------------------------------------------------------
# Encoders
# --------------------------------------------------------------------------


def bmp_entry(size, bgra):
    """BITMAPINFOHEADER + bottom-up BGRA + AND mask."""
    stride = size * 4
    flipped = b"".join(bgra[(size - 1 - y) * stride:(size - y) * stride]
                       for y in range(size))
    mask_row = ((size + 31) // 32) * 4
    mask = bytes(mask_row * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(flipped) + len(mask), 0, 0, 0, 0)
    return header + flipped + mask


def png_entry(size, bgra):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        row = bgra[y * size * 4:(y + 1) * size * 4]
        for i in range(0, len(row), 4):
            raw += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def write_ico(path, palette):
    images = []
    for size in SIZES:
        bgra = render(size, palette)
        # 256px goes in as PNG to keep the file small; Windows has understood
        # PNG-compressed icon entries since Vista.
        data = png_entry(size, bgra) if size >= 256 else bmp_entry(size, bgra)
        images.append((size, data))
        print("   %3dx%-3d  %6d bytes" % (size, size, len(data)))

    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    directory = b""
    for size, data in images:
        directory += struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0,
                                 1, 32, len(data), offset)
        offset += len(data)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(header + directory + b"".join(data for _, data in images))
    return path


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "assets")
    for name, palette in (("autoclicker.ico", IDLE),
                          ("autoclicker-running.ico", RUNNING)):
        print("rendering %s" % name)
        path = write_ico(os.path.join(assets, name), palette)
        print("  -> %s  (%d bytes)\n" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
