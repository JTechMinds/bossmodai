"""Desktop brand mark is a robot-on-blue icon, wired into the Tauri bundle."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "desktop" / "icons"
BRAND_RGB = (0x25, 0x63, 0xEB)
WHITE_RGB = (255, 255, 255)
BUNDLE_ICONS = (
    "icons/32x32.png",
    "icons/128x128.png",
    "icons/128x128@2x.png",
    "icons/icon.png",
    "icons/icon.icns",
    "icons/icon.ico",
)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def png_rgba(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos = 8
    width = height = None
    idat = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit, color, *_ = struct.unpack(">IIBBBBB", chunk)
            assert bit == 8 and color == 6, f"{path} must be 8-bit RGBA"
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    assert width and height, f"{path} missing IHDR"
    raw = zlib.decompress(idat)
    bpp = 4
    row_len = 1 + width * bpp
    prev = bytearray(width * bpp)
    out = bytearray()
    for y in range(height):
        filt = raw[y * row_len]
        scan = bytearray(raw[y * row_len + 1 : (y + 1) * row_len])
        if filt == 1:
            for i, val in enumerate(scan):
                scan[i] = (val + (scan[i - bpp] if i >= bpp else 0)) & 255
        elif filt == 2:
            for i, val in enumerate(scan):
                scan[i] = (val + prev[i]) & 255
        elif filt == 3:
            for i, val in enumerate(scan):
                left = scan[i - bpp] if i >= bpp else 0
                scan[i] = (val + ((left + prev[i]) // 2)) & 255
        elif filt == 4:
            for i, val in enumerate(scan):
                left = scan[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                scan[i] = (val + _paeth(left, prev[i], up_left)) & 255
        elif filt != 0:
            raise AssertionError(f"{path} unsupported PNG filter {filt}")
        prev = scan
        out.extend(scan)
    return width, height, bytes(out)


def opaque_colors(pixels: bytes, min_alpha: int = 250) -> set[tuple[int, int, int]]:
    colors: set[tuple[int, int, int]] = set()
    for i in range(0, len(pixels), 4):
        if pixels[i + 3] >= min_alpha:
            colors.add((pixels[i], pixels[i + 1], pixels[i + 2]))
    return colors


def test_tauri_bundle_icon_is_wired() -> None:
    config = json.loads((ROOT / "desktop" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["bundle"]["icon"] == list(BUNDLE_ICONS)
    for rel in BUNDLE_ICONS:
        path = ROOT / "desktop" / rel
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 200, f"{path} looks empty"


def test_icon_png_is_robot_on_brand_blue_not_flat_square() -> None:
    path = ICONS / "icon.png"
    # The previous asset was a 512×512 solid #2563eb square (~2KB).
    assert path.stat().st_size > 4000
    width, height, pixels = png_rgba(path)
    assert width == height == 512
    colors = opaque_colors(pixels)
    assert BRAND_RGB in colors
    assert WHITE_RGB in colors
    assert len(colors) > 8, f"icon.png still looks flat ({len(colors)} opaque colors)"


def test_png_variants_are_square_rgba_and_not_flat() -> None:
    expected = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
    }
    for name, size in expected.items():
        width, height, pixels = png_rgba(ICONS / name)
        assert width == height == size
        colors = opaque_colors(pixels)
        assert BRAND_RGB in colors
        assert len(colors) > 2


def test_ico_and_icns_containers() -> None:
    ico = (ICONS / "icon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"
    count = struct.unpack_from("<H", ico, 4)[0]
    assert count >= 6
    first_w, first_h = ico[6], ico[7]
    assert (first_w, first_h) == (32, 32)

    icns = (ICONS / "icon.icns").read_bytes()
    assert icns[:4] == b"icns"
    assert struct.unpack(">I", icns[4:8])[0] == len(icns)


def test_source_svg_is_lucide_bot_on_brand() -> None:
    svg = (ICONS / "icon.svg").read_text(encoding="utf-8")
    assert "#2563eb" in svg
    assert 'data-lucide="bot"' in svg or "M12 8V4H8" in svg
    assert "M15 13v2" in svg
    assert "M9 13v2" in svg
