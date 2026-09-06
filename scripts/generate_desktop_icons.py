#!/usr/bin/env python3
"""Rasterize desktop/icons/icon.svg into the PNG/ICO/ICNS set Tauri 2 expects.

Generation-only. Pillow is not a runtime or test dependency:

    uv pip install pillow
    python scripts/generate_desktop_icons.py
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "desktop" / "icons"

# Brand + Lucide bot (ISC) — same numbers as desktop/icons/icon.svg
BRAND = (0x25, 0x63, 0xEB, 255)
WHITE = (255, 255, 255, 255)
TILE_RADIUS_RATIO = 128 / 512  # rx="128" on the 512 viewBox (Tailwind rounded-lg)
BOT_VIEWBOX = 24
BOT_SCALE_IN_TILE = 320 / 512  # translate(96) + scale(13.3333) → 24 * 13.3333 = 320
STROKE_IN_VIEWBOX = 2.0

MASTER_SIZE = 2048

# tauri icon desktop outputs + ICO/ICNS layer sizes
PNG_OUTPUTS = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}

ICO_SIZES = (32, 16, 24, 48, 64, 256)  # 32px first — Tauri ICO guidance
ICNS_LAYERS = {
    b"icp4": 16,
    b"icp5": 32,
    b"icp6": 64,
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,
    b"ic12": 64,
    b"ic13": 256,
    b"ic14": 512,
}


def _pt(ox: float, oy: float, scale: float, x: float, y: float) -> tuple[float, float]:
    return (ox + x * scale, oy + y * scale)


def _line(
    draw: ImageDraw.ImageDraw,
    ox: float,
    oy: float,
    scale: float,
    stroke: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> None:
    p1 = _pt(ox, oy, scale, x1, y1)
    p2 = _pt(ox, oy, scale, x2, y2)
    draw.line([p1, p2], fill=WHITE, width=max(1, round(stroke)))
    r = stroke / 2
    for px, py in (p1, p2):
        draw.ellipse((px - r, py - r, px + r, py + r), fill=WHITE)


def draw_master(size: int) -> Image.Image:
    """Paint the rounded brand tile and stroked Lucide bot at `size` px."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = size * TILE_RADIUS_RATIO
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BRAND)

    scale = size * BOT_SCALE_IN_TILE / BOT_VIEWBOX
    ox = (size - BOT_VIEWBOX * scale) / 2
    oy = ox
    stroke = STROKE_IN_VIEWBOX * scale

    # Antenna: M12 8V4H8
    _line(draw, ox, oy, scale, stroke, 12, 8, 12, 4)
    _line(draw, ox, oy, scale, stroke, 12, 4, 8, 4)

    # Head: rect 16×12 at (4,8) rx=2, stroke centered (fill none)
    half = STROKE_IN_VIEWBOX / 2
    outer = (
        *_pt(ox, oy, scale, 4 - half, 8 - half),
        *_pt(ox, oy, scale, 4 + 16 + half, 8 + 12 + half),
    )
    inner = (
        *_pt(ox, oy, scale, 4 + half, 8 + half),
        *_pt(ox, oy, scale, 4 + 16 - half, 8 + 12 - half),
    )
    draw.rounded_rectangle(outer, radius=(2 + half) * scale, fill=WHITE)
    draw.rounded_rectangle(inner, radius=max(0.0, (2 - half) * scale), fill=BRAND)

    # Ears + eyes
    _line(draw, ox, oy, scale, stroke, 2, 14, 4, 14)
    _line(draw, ox, oy, scale, stroke, 20, 14, 22, 14)
    _line(draw, ox, oy, scale, stroke, 15, 13, 15, 15)
    _line(draw, ox, oy, scale, stroke, 9, 13, 9, 15)
    return img


def resize(master: Image.Image, size: int) -> Image.Image:
    return master.resize((size, size), Image.Resampling.LANCZOS)


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def write_ico(path: Path, layers: list[Image.Image]) -> None:
    payloads = [png_bytes(im) for im in layers]
    count = len(layers)
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    directory = b""
    for im, payload in zip(layers, payloads):
        w = 0 if im.width >= 256 else im.width
        h = 0 if im.height >= 256 else im.height
        directory += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    path.write_bytes(header + directory + b"".join(payloads))


def write_icns(path: Path, layers: dict[bytes, Image.Image]) -> None:
    body = b""
    for ostype, image in layers.items():
        data = png_bytes(image)
        body += ostype + struct.pack(">I", 8 + len(data)) + data
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    master = draw_master(MASTER_SIZE)
    cache: dict[int, Image.Image] = {MASTER_SIZE: master}

    def sized(n: int) -> Image.Image:
        if n not in cache:
            cache[n] = resize(master, n)
        return cache[n]

    for name, n in PNG_OUTPUTS.items():
        sized(n).save(ICONS / name, format="PNG")

    write_ico(ICONS / "icon.ico", [sized(n) for n in ICO_SIZES])
    write_icns(ICONS / "icon.icns", {code: sized(n) for code, n in ICNS_LAYERS.items()})
    print(f"wrote icons in {ICONS}")


if __name__ == "__main__":
    main()
