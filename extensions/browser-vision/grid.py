"""Browser Vision — the keypad view and its rendering (pure; Pillow only).

The agent looks at a VIEW: the whole viewport, or a rect it zoomed into. The
view splits into a 3×3 keypad, regions numbered like a phone keypad::

    1 2 3
    4 5 6
    7 8 9

``zoom <d>`` narrows the view to region ``d``; ``click <d>`` clicks its
centre. Element marks (``marks``) are drawn on top wherever they fall.

All coordinates are CSS pixels; screenshots are taken at CSS scale, so image
pixels and click coordinates share one space. A view's scale ``s`` maps CSS
px to image px only for drawing: ``(css - view origin) * s``.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Sequence, Union

from PIL import Image, ImageDraw, ImageFont, ImageStat

from .marks import Mark

RGB = tuple[int, int, int]
GridColor = Union[Literal["auto"], RGB]
# (x0, y0, x1, y1), CSS px, floats.
Rect = tuple[float, float, float, float]

# Line and label inks for auto contrast: near-black on light ground,
# near-white on dark ground. Not pure black/white so the halo still reads.
_DARK: RGB = (17, 17, 17)
_LIGHT: RGB = (240, 240, 240)
# Mean luma above this is "light ground" (midpoint of 0–255).
_LUMA_SPLIT = 128
# Space between a tag's edge and its digits, in rendered px.
_TAG_PAD = 1
# A mark's outline width, in rendered px.
_OUTLINE_PX = 2
# The smallest region a zoom may produce, in CSS px on each side: below one
# pixel there is nothing left to see.
MIN_REGION_PX = 1.0

_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


class ZoomLimit(ValueError):
    """A zoom would produce a region smaller than one CSS pixel."""


@dataclass(frozen=True)
class GridStyle:
    """How the keypad is drawn: on/off, colour (``"auto"`` or RGB) and line opacity 0–1."""

    enabled: bool
    color: GridColor
    opacity: float


@dataclass(frozen=True)
class LabelStyle:
    """How mark numbers are drawn: small corner tags.

    Attributes:
        font_ratio: Digit height as a share of the mark's rendered height.
        font_min_px: Smallest digit height.
        font_max_px: Largest digit height, so a tag never covers more page
            text than it must.
        opacity: Tag opacity 0–1 (keypad digits too), separate from the lines'.
    """

    font_ratio: float
    font_min_px: int
    font_max_px: int
    opacity: float


@dataclass(frozen=True)
class RenderedView:
    """A screenshot of the current view with keypad and marks drawn on.

    Attributes:
        png: The image bytes.
        width: Image width in px.
        height: Image height in px.
        scale: Rendered px per CSS px.
    """

    png: bytes
    width: int
    height: int
    scale: float


def parse_color(raw: str) -> GridColor:
    """Parse a grid colour: ``auto`` or ``#rrggbb``.

    Raises:
        ValueError: Any other form.
    """
    text = raw.strip()
    if text.lower() == "auto":
        return "auto"
    match = _COLOR_RE.match(text)
    if match is None:
        raise ValueError(f"grid colour must be auto or #rrggbb (e.g. #ff00ff), got {raw!r}")
    hex_digits = match.group(1)
    return int(hex_digits[0:2], 16), int(hex_digits[2:4], 16), int(hex_digits[4:6], 16)


def parse_opacity(raw: str) -> float:
    """Parse a grid opacity between 0 and 1.

    Raises:
        ValueError: Not a number, or outside 0–1.
    """
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"grid opacity must be a number from 0 to 1 (e.g. 0.5), got {raw!r}") from None
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"grid opacity must be a number from 0 to 1 (e.g. 0.5), got {raw!r}")
    return value


def parse_keypad_digit(raw: str) -> int:
    """Parse one keypad region, ``1``–``9``.

    Raises:
        ValueError: Anything else.
    """
    text = raw.strip()
    if len(text) != 1 or text not in "123456789":
        raise ValueError(f"a keypad region is one digit 1–9 (1 2 3 / 4 5 6 / 7 8 9), got {raw!r}")
    return int(text)


def keypad_region(rect: Rect, digit: int) -> Rect:
    """Return region ``digit`` (1–9, keypad order) of ``rect``.

    Raises:
        ValueError: ``digit`` is not 1–9.
    """
    if not 1 <= digit <= 9:
        raise ValueError(f"keypad digit must be 1–9, got {digit}")
    x0, y0, x1, y1 = rect
    col, row = (digit - 1) % 3, (digit - 1) // 3
    w, h = (x1 - x0) / 3, (y1 - y0) / 3
    return x0 + col * w, y0 + row * h, x0 + (col + 1) * w, y0 + (row + 1) * h


def zoom_into(rect: Rect, digit: int) -> Rect:
    """Return region ``digit`` of ``rect`` as the next view.

    Raises:
        ZoomLimit: The region would be smaller than one CSS px on a side.
    """
    region = keypad_region(rect, digit)
    if region[2] - region[0] < MIN_REGION_PX or region[3] - region[1] < MIN_REGION_PX:
        raise ZoomLimit(
            f"ZOOM_LIMIT: region {digit} would be {region[2] - region[0]:.2g}×{region[3] - region[1]:.2g} px, "
            "under one pixel; zoom out or click here"
        )
    return region


def region_center(rect: Rect, digit: int) -> tuple[float, float]:
    """Return the centre of region ``digit`` of ``rect`` (the click point)."""
    x0, y0, x1, y1 = keypad_region(rect, digit)
    return (x0 + x1) / 2, (y0 + y1) / 2


def view_scale(rect: Rect, *, zoomed: bool, image_max_px: int) -> float:
    """Return rendered px per CSS px for a view.

    A full view only shrinks to fit ``image_max_px``; a zoomed view is scaled
    to fit it exactly (upscaling is the point of zooming), never past it.
    """
    long_edge = max(rect[2] - rect[0], rect[3] - rect[1])
    fit = image_max_px / long_edge
    return fit if zoomed else min(1.0, fit)


def render_view(
    png: bytes,
    viewport: tuple[int, int],
    rect: Rect | None,
    style: GridStyle,
    marks: Sequence[Mark],
    *,
    show_marks: bool,
    image_max_px: int,
    label: LabelStyle,
    keypad_font_px: int,
) -> RenderedView:
    """Crop and scale the screenshot to the view, then draw keypad and marks.

    The image is scaled BEFORE anything is drawn, so lines and tags are crisp
    at the final size. Marks partly inside the view are drawn clipped; marks
    outside it are not drawn (they stay addressable by number).

    Args:
        png: The viewport screenshot, exactly ``viewport`` in size.
        viewport: ``(width, height)`` in CSS px.
        rect: The zoomed view, or ``None`` for the full viewport.
        style: Keypad on/off, colour and line opacity.
        marks: The marks of this screenshot, in page CSS px.
        show_marks: Whether to draw them.
        image_max_px: Longest edge of the returned image.
        label: Mark tag size and opacity.
        keypad_font_px: Keypad digit size.

    Returns:
        The rendered image.

    Raises:
        ValueError: The screenshot's size does not match ``viewport`` (clicks
            would then land somewhere other than what is shown).
    """
    source = Image.open(io.BytesIO(png)).convert("RGB")
    if source.size != viewport:
        raise ValueError(
            f"screenshot is {source.size[0]}×{source.size[1]} but the viewport is {viewport[0]}×{viewport[1]}"
        )
    view: Rect = rect if rect is not None else (0.0, 0.0, float(viewport[0]), float(viewport[1]))
    scale = view_scale(view, zoomed=rect is not None, image_max_px=image_max_px)
    size = (max(1, round((view[2] - view[0]) * scale)), max(1, round((view[3] - view[1]) * scale)))
    if rect is None and scale == 1.0:
        image = source
    else:
        image = source.resize(size, Image.Resampling.LANCZOS, box=view)

    luma = image.convert("L")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if style.enabled:
        _draw_keypad_lines(draw, luma, style, round(style.opacity * 255))
        _draw_keypad_digits(draw, luma, style, label, keypad_font_px)
    if show_marks:
        for mark in marks:
            _draw_mark(draw, luma, mark, view, scale, style, label)
    composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = io.BytesIO()
    composed.save(buffer, format="PNG")
    return RenderedView(png=buffer.getvalue(), width=composed.size[0], height=composed.size[1], scale=scale)


def _inks(luma: Image.Image, box: tuple[float, float, float, float], color: GridColor) -> tuple[RGB, RGB]:
    """Return ``(ink, halo)`` for a line, outline or tag over ``box``."""
    if color == "auto":
        left, top, right, bottom = _clip_box(box, luma.size)
        mean = ImageStat.Stat(luma.crop((left, top, right, bottom))).mean[0]
        return (_DARK, _LIGHT) if mean >= _LUMA_SPLIT else (_LIGHT, _DARK)
    # A fixed colour keeps a halo in whichever ink contrasts with it.
    red, green, blue = color
    own_luma = 0.299 * red + 0.587 * green + 0.114 * blue
    return color, (_DARK if own_luma >= _LUMA_SPLIT else _LIGHT)


def _clip_box(box: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    left = min(max(int(box[0]), 0), width - 1)
    top = min(max(int(box[1]), 0), height - 1)
    right = min(max(int(math.ceil(box[2])), left + 1), width)
    bottom = min(max(int(math.ceil(box[3])), top + 1), height)
    return left, top, right, bottom


def _draw_keypad_lines(draw: ImageDraw.ImageDraw, luma: Image.Image, style: GridStyle, alpha: int) -> None:
    """Two lines each way, one region-long segment at a time for auto contrast."""
    width, height = luma.size
    for third in (1, 2):
        px = round(width * third / 3)
        py = round(height * third / 3)
        for part in range(3):
            top, bottom = round(height * part / 3), round(height * (part + 1) / 3) - 1
            ink, halo = _inks(luma, (px - 1, top, px + 2, bottom), style.color)
            draw.line([(px - 1, top), (px - 1, bottom)], fill=(*halo, alpha))
            draw.line([(px + 1, top), (px + 1, bottom)], fill=(*halo, alpha))
            draw.line([(px, top), (px, bottom)], fill=(*ink, alpha))
            left, right = round(width * part / 3), round(width * (part + 1) / 3) - 1
            ink, halo = _inks(luma, (left, py - 1, right, py + 2), style.color)
            draw.line([(left, py - 1), (right, py - 1)], fill=(*halo, alpha))
            draw.line([(left, py + 1), (right, py + 1)], fill=(*halo, alpha))
            draw.line([(left, py), (right, py)], fill=(*ink, alpha))


def _draw_keypad_digits(
    draw: ImageDraw.ImageDraw,
    luma: Image.Image,
    style: GridStyle,
    label: LabelStyle,
    font_px: int,
) -> None:
    """Draw 1–9 as tags centred in their regions."""
    width, height = luma.size
    font = _font(font_px)
    for digit in range(1, 10):
        cx, cy = region_center((0.0, 0.0, float(width), float(height)), digit)
        _draw_tag(draw, luma, str(digit), font, (cx, cy), style.color, label.opacity, centred=True)


def _draw_mark(
    draw: ImageDraw.ImageDraw,
    luma: Image.Image,
    mark: Mark,
    view: Rect,
    scale: float,
    style: GridStyle,
    label: LabelStyle,
) -> None:
    """Outline a mark and tag it with its number, if any of it is in view."""
    x0, y0, x1, y1 = mark.rect
    vx0, vy0, vx1, vy1 = view
    if x1 <= vx0 or x0 >= vx1 or y1 <= vy0 or y0 >= vy1:
        return
    box = ((x0 - vx0) * scale, (y0 - vy0) * scale, (x1 - vx0) * scale, (y1 - vy0) * scale)
    ink, _halo = _inks(luma, box, style.color)
    alpha = round(label.opacity * 255)
    draw.rectangle(box, outline=(*ink, alpha), width=_OUTLINE_PX)
    font = _font(label_font_size(box[3] - box[1], label))
    number = str(mark.n)
    tag = tag_box(draw, (0, 0), number, font)
    tag_w, tag_h = tag[2] + 1, tag[3] + 1
    # Inside the top-left corner when the mark has room, else just above it.
    inside = (box[3] - box[1]) >= tag_h + 2 * _OUTLINE_PX and (box[2] - box[0]) >= tag_w + 2 * _OUTLINE_PX
    origin = (box[0] + _OUTLINE_PX, box[1] + _OUTLINE_PX) if inside else (box[0], box[1] - tag_h)
    _draw_tag(draw, luma, number, font, origin, style.color, label.opacity, centred=False)


def _draw_tag(
    draw: ImageDraw.ImageDraw,
    luma: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    at: tuple[float, float],
    color: GridColor,
    opacity: float,
    *,
    centred: bool,
) -> None:
    """A filled tag with ``text`` on it, kept inside the image, auto-contrast."""
    tag = tag_box(draw, (0, 0), text, font)
    x, y = at
    if centred:
        x, y = x - (tag[2] + 1) / 2, y - (tag[3] + 1) / 2
    placed = fit_inside(tag_box(draw, (round(x), round(y)), text, font), luma.size)
    fill, digits = _inks(luma, placed, color)
    alpha = round(opacity * 255)
    draw.rectangle(placed, fill=(*fill, alpha))
    left, top, _right, _bottom = draw.textbbox((0, 0), text, font=font)
    draw.text((placed[0] + _TAG_PAD - left, placed[1] + _TAG_PAD - top), text, font=font, fill=(*digits, alpha))


def fit_inside(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Shift an inclusive box left/up/right/down just enough to lie inside ``size``.

    A tag at an edge moves back over its own target instead of being cut off.
    A box already inside is returned unchanged.
    """
    x0, y0, x1, y1 = box
    width, height = size
    dx = min(0, (width - 1) - x1) or max(0, -x0)
    dy = min(0, (height - 1) - y1) or max(0, -y0)
    return x0 + dx, y0 + dy, x1 + dx, y1 + dy


def label_font_size(rendered_px: float, label: LabelStyle) -> int:
    """Return the digit size for a target ``rendered_px`` tall, within min/max."""
    return min(label.font_max_px, max(label.font_min_px, round(rendered_px * label.font_ratio)))


def tag_box(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> tuple[int, int, int, int]:
    """Return the inclusive box of a tag at ``origin``: the text plus padding."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x, y = origin
    return x, y, x + (right - left) + 2 * _TAG_PAD - 1, y + (bottom - top) + 2 * _TAG_PAD - 1


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow's bundled default font; the size argument needs Pillow >= 10.1.
    return ImageFont.load_default(size=size)
