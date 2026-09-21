#!/usr/bin/env python3
"""Render the pixel-robot mascot as PNG/SVG icons (no dependencies).

    python3 make_icons.py   -> icons/icon-180.png, icon-192.png, icon-512.png, icon.svg
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "icons"
GRID = 20                      # canvas cells; the 16x16 robot sits at offset 2,2
BG = (0, 0, 0)
BODY, ANT, EYE = (0xFF, 0x9E, 0x1B), (0xFF, 0xC2, 0x66), (0, 0, 0)
# (x, y, w, h, color) in the 16x16 robot grid, same shapes as index.html
RECTS = [
    (7, 1, 2, 3, ANT), (3, 4, 10, 8, BODY), (5, 6, 2, 3, EYE), (9, 6, 2, 3, EYE),
    (1, 6, 2, 4, BODY), (13, 6, 2, 4, BODY), (4, 12, 3, 2, BODY), (9, 12, 3, 2, BODY),
]


def cell_color(cx: int, cy: int):
    x, y = cx - 2, cy - 2
    color = BG
    for rx, ry, rw, rh, c in RECTS:          # later rects paint over earlier ones (eyes over body)
        if rx <= x < rx + rw and ry <= y < ry + rh:
            color = c
    return color


def png(size: int) -> bytes:
    cell = size / GRID
    rows = []
    for py in range(size):
        cy = min(GRID - 1, int(py / cell))
        row = bytearray([0])                 # filter type 0
        for px in range(size):
            row += bytes(cell_color(min(GRID - 1, int(px / cell)), cy))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def svg() -> str:
    rects = "".join(
        f'<rect x="{x + 2}" y="{y + 2}" width="{w}" height="{h}" fill="#{c[0]:02x}{c[1]:02x}{c[2]:02x}"/>'
        for x, y, w, h, c in RECTS
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" shape-rendering="crispEdges">'
            f'<rect width="{GRID}" height="{GRID}" fill="#000"/>{rects}</svg>\n')


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for s in (180, 192, 512):
        (OUT / f"icon-{s}.png").write_bytes(png(s))
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8")
    print("wrote", sorted(p.name for p in OUT.iterdir()))
