# -*- coding: utf-8 -*-
"""生成程序图标 icon.ico（纯标准库写 PNG + ICO，无需 Pillow）。"""
import os
import struct
import zlib

BG = (47, 107, 255, 255)      # 主色蓝
FG = (255, 255, 255, 255)     # 箭头白
SIZES = (16, 32, 48, 256)


def rounded_alpha(x, y, n, r=0.22):
    """圆角矩形内返回 255，边缘抗锯齿，外部 0。"""
    cx = min(max(x, r), n - 1 - r * 1.0)
    cy = min(max(y, r), n - 1 - r * 1.0)
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    inner = r * n - 1.0
    if d <= inner - 1.0:
        return 255
    if d >= inner + 1.0:
        return 0
    return int(255 * (inner + 1.0 - d) / 2.0)


def in_arrow(x, y, n):
    fx, fy = x / n, y / n
    if 0.44 <= fx <= 0.56 and 0.26 <= fy <= 0.60:      # 竖杆
        return True
    if 0.60 <= fy <= 0.74:                             # 箭头三角
        t = (fy - 0.60) / 0.14
        half = 0.24 * (1.0 - t)
        return abs(fx - 0.5) <= half
    return False


def render(n):
    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            a = rounded_alpha(x + 0.5, y + 0.5, n)
            if a == 0:
                row += bytes(4)
                continue
            r, g, b, _ = FG if in_arrow(x + 0.5, y + 0.5, n) else BG
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    return rows


def png_bytes(rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    n = len(rows)
    raw = b"".join(b"\x00" + r for r in rows)
    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    imgs = [(s, png_bytes(render(s))) for s in SIZES]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    offset = 6 + 16 * len(imgs)
    entries, blobs = b"", b""
    for s, data in imgs:
        entries += struct.pack("<BBBBHHII",
                               s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(out, "wb") as f:
        f.write(head + entries + blobs)
    print("icon:", out, "%.1f KB" % (os.path.getsize(out) / 1024))


if __name__ == "__main__":
    main()
