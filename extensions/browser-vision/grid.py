"""Browser Vision — grid maths and grid rendering (pure; Pillow only).

Numbering: at density ``N`` over a ``W×H`` viewport there are
``cols = ceil(W/N)`` by ``rows = ceil(H/N)`` cells, numbered row-major from 0
over the WHOLE viewport. The same number at the same density is therefore the
same spot in a full view and in any focused (zoomed) view.

All coordinates are CSS pixels; screenshots are taken at CSS scale, so image
pixels and click coordinates share one space.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Union

from PIL import Image, ImageDraw, ImageFont, ImageStat

RGB = tuple[int, int, int]
GridColor = Union[Literal["auto"], RGB]
Rect = tuple[int, int, int, int]

# Line and label inks for auto contrast: near-black on light ground,
# near-white on dark ground. Not pure black/white so the halo still reads.
_DARK: RGB = (17, 17, 17)
_LIGHT: RGB = (240, 240, 240)
# Mean luma above this is "light ground" (midpoint of 0–255).
_LUMA_SPLIT = 128
# Label height as a share of the rendered cell: four digits still fit across
# a cell at the smallest labelled size.
_LABEL_FONT_RATIO = 0.38
# Gap between a cell's top-left corner and its label, in rendered px.
_LABEL_PAD = 2

_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_FOCUS_RE = re.compile(r"^(\d+)-(\d+)$")


class CellOutOfRange(ValueError):
    """A cell number is outside the grid it was resolved against."""


@dataclass(frozen=True)
class GridSpec:
    """A grid of ``density``-px cells over a ``viewport_w × viewport_h`` page.

    Raises:
        ValueError: Any dimension is not a positive integer.
    """

    viewport_w: int
    viewport_h: int
    density: int

    def __post_init__(self) -> None:
        if min(self.viewport_w, self.viewport_h, self.density) <= 0:
            raise ValueError("grid viewport and density must be positive")

    @property
    def cols(self) -> int:
        """Columns, counting a partial right-edge column."""
        return math.ceil(self.viewport_w / self.density)

    @property
    def rows(self) -> int:
        """Rows, counting a partial bottom-edge row."""
        return math.ceil(self.viewport_h / self.density)

    @property
    def cell_count(self) -> int:
        """Total cells; valid numbers are ``0 .. cell_count - 1``."""
        return self.cols * self.rows

    def cell_number(self, col: int, row: int) -> int:
        """Return the number of the cell at ``(col, row)``.

        Raises:
            CellOutOfRange: ``col`` or ``row`` is outside the grid.
        """
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            raise CellOutOfRange(f"cell ({col}, {row}) is outside {self.cols}×{self.rows}")
        return row * self.cols + col

    def cell_of(self, n: int) -> tuple[int, int]:
        """Return ``(col, row)`` of cell ``n``.

        Raises:
            CellOutOfRange: ``n`` is not in ``0 .. cell_count - 1``; the
                message names the valid range.
        """
        if not 0 <= n < self.cell_count:
            raise CellOutOfRange(
                f"CELL_OUT_OF_RANGE: cell {n} is not on this grid; valid cells are 0–{self.cell_count - 1}"
            )
        return n % self.cols, n // self.cols

    def cell_rect(self, n: int) -> Rect:
        """Return cell ``n`` as ``(x0, y0, x1, y1)``, clipped to the viewport."""
        col, row = self.cell_of(n)
        x0, y0 = col * self.density, row * self.density
        return x0, y0, min(x0 + self.density, self.viewport_w), min(y0 + self.density, self.viewport_h)

    def cell_center(self, n: int) -> tuple[float, float]:
        """Return the click point of cell ``n``.

        The centre of the full cell, clamped inside the viewport for a partial
        edge cell. The clamp is geometric; out-of-range numbers raise instead.
        """
        col, row = self.cell_of(n)
        x = col * self.density + self.density / 2
        y = row * self.density + self.density / 2
        return min(x, self.viewport_w - 1), min(y, self.viewport_h - 1)


@dataclass(frozen=True)
class GridStyle:
    """How the grid is drawn: on/off, colour (``"auto"`` or RGB) and opacity 0–1."""

    enabled: bool
    color: GridColor
    opacity: float


@dataclass(frozen=True)
class FocusSpec:
    """A zoom box: the corner cells the agent named and the snapped CSS rect."""

    a: int
    b: int
    rect: Rect


@dataclass(frozen=True)
class RenderedGrid:
    """A screenshot with the grid drawn on.

    Attributes:
        png: The image bytes.
        labelled: Whether cell numbers were drawn (False when the grid is off
            or the cells were too small to label).
        width: Image width in px.
        height: Image height in px.
        scale: Rendered px per CSS px (1 for a full view).
    """

    png: bytes
    labelled: bool
    width: int
    height: int
    scale: float


def parse_density(raw: str) -> int:
    """Parse a grid density (px per cell).

    Raises:
        ValueError: ``raw`` is not a positive whole number.
    """
    text = raw.strip()
    if not (text.isascii() and text.isdigit()) or int(text) <= 0:
        raise ValueError(f"density must be a positive whole number of pixels (e.g. 40), got {raw!r}")
    return int(text)


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


def parse_focus(raw: str) -> tuple[int, int]:
    """Parse ``<a>-<b>``: two cell numbers from the most recent screenshot.

    Raises:
        ValueError: Any other form.
    """
    match = _FOCUS_RE.match(raw.strip())
    if match is None:
        raise ValueError(f"focus must be two cell numbers like 245-290, got {raw!r}")
    return int(match.group(1)), int(match.group(2))


def focus_rect(prev_grid: GridSpec, a: int, b: int, focus_density: int) -> Rect:
    """Return the CSS rect to zoom into for corner cells ``a`` and ``b``.

    The box bounding both cells (read on ``prev_grid``, the grid the agent
    just saw) is snapped outward to ``focus_density`` cell boundaries, so the
    crop starts and ends on whole focus cells.

    Raises:
        CellOutOfRange: ``a`` or ``b`` is not on ``prev_grid``.
        ValueError: ``focus_density`` is not positive.
    """
    if focus_density <= 0:
        raise ValueError("focus density must be positive")
    ax0, ay0, ax1, ay1 = prev_grid.cell_rect(a)
    bx0, by0, bx1, by1 = prev_grid.cell_rect(b)
    x0, y0 = min(ax0, bx0), min(ay0, by0)
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    snapped_x0 = (x0 // focus_density) * focus_density
    snapped_y0 = (y0 // focus_density) * focus_density
    snapped_x1 = min(math.ceil(x1 / focus_density) * focus_density, prev_grid.viewport_w)
    snapped_y1 = min(math.ceil(y1 / focus_density) * focus_density, prev_grid.viewport_h)
    return snapped_x0, snapped_y0, snapped_x1, snapped_y1


def render_grid(
    png: bytes,
    spec: GridSpec,
    style: GridStyle,
    focus: FocusSpec | None,
    *,
    label_min_px: int,
    render_max_px: int,
) -> RenderedGrid:
    """Draw the numbered grid on a viewport screenshot, optionally zoomed.

    A full view keeps the screenshot's size. A focused view crops
    ``focus.rect`` and scales it up by ``label_min_px / density`` (never
    below 1, LANCZOS) so each cell is big enough to label; cells keep their
    GLOBAL numbers at ``spec.density``. Labels are omitted — lines are still
    drawn — when a rendered cell is smaller than ``label_min_px``, or when a
    scaled-up crop would be longer than ``render_max_px`` on its long edge.

    Args:
        png: The viewport screenshot, exactly ``spec.viewport_w × viewport_h``.
        spec: The grid, at the density this capture is drawn with.
        style: On/off, colour and opacity.
        focus: The zoom box, or ``None`` for a full view.
        label_min_px: Smallest rendered cell that gets a number.
        render_max_px: Longest scaled-up crop that still gets numbers.

    Returns:
        The rendered image and whether it carries numbers.

    Raises:
        ValueError: The screenshot's size does not match ``spec`` (click
            numbers would then point somewhere other than what is shown).
    """
    source = Image.open(io.BytesIO(png)).convert("RGB")
    if source.size != (spec.viewport_w, spec.viewport_h):
        raise ValueError(
            f"screenshot is {source.size[0]}×{source.size[1]} but the grid is for "
            f"{spec.viewport_w}×{spec.viewport_h}"
        )
    if focus is None:
        origin_x, origin_y = 0, 0
        image = source
        scale = 1.0
        labelled = spec.density >= label_min_px
    else:
        x0, y0, x1, y1 = focus.rect
        origin_x, origin_y = x0, y0
        scale = max(1.0, label_min_px / spec.density)
        crop = source.crop((x0, y0, x1, y1))
        if scale > 1.0:
            size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
            image = crop.resize(size, Image.Resampling.LANCZOS)
            labelled = spec.density * scale >= label_min_px and max(size) <= render_max_px
        else:
            image = crop
            labelled = spec.density >= label_min_px

    if not style.enabled:
        return _encode(image, labelled=False, scale=scale)

    luma = image.convert("L")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    alpha = round(style.opacity * 255)
    region = (origin_x, origin_y, origin_x + image.size[0] / scale, origin_y + image.size[1] / scale)
    _draw_lines(draw, luma, spec, style, region, scale, alpha)
    if labelled:
        _draw_labels(draw, luma, spec, style, region, scale, alpha)
    composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    return _encode(composed, labelled=labelled, scale=scale)


def _encode(image: Image.Image, *, labelled: bool, scale: float) -> RenderedGrid:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return RenderedGrid(
        png=buffer.getvalue(),
        labelled=labelled,
        width=image.size[0],
        height=image.size[1],
        scale=scale,
    )


def _inks(luma: Image.Image, box: tuple[float, float, float, float], color: GridColor) -> tuple[RGB, RGB]:
    """Return ``(ink, halo)`` for a line segment or label over ``box``."""
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


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    luma: Image.Image,
    spec: GridSpec,
    style: GridStyle,
    region: tuple[float, float, float, float],
    scale: float,
    alpha: int,
) -> None:
    """Draw every internal cell boundary, one cell-long segment at a time."""
    rx0, ry0, rx1, ry1 = region
    width, height = luma.size
    col_first, col_last = int(rx0 // spec.density), math.ceil(rx1 / spec.density)
    row_first, row_last = int(ry0 // spec.density), math.ceil(ry1 / spec.density)

    for col in range(col_first + 1, col_last):
        px = round((col * spec.density - rx0) * scale)
        if not 0 < px < width:
            continue
        for row in range(row_first, row_last):
            top = max(0, round((row * spec.density - ry0) * scale))
            bottom = min(height - 1, round(((row + 1) * spec.density - ry0) * scale))
            if bottom <= top:
                continue
            ink, halo = _inks(luma, (px - 1, top, px + 2, bottom), style.color)
            draw.line([(px - 1, top), (px - 1, bottom)], fill=(*halo, alpha))
            draw.line([(px + 1, top), (px + 1, bottom)], fill=(*halo, alpha))
            draw.line([(px, top), (px, bottom)], fill=(*ink, alpha))

    for row in range(row_first + 1, row_last):
        py = round((row * spec.density - ry0) * scale)
        if not 0 < py < height:
            continue
        for col in range(col_first, col_last):
            left = max(0, round((col * spec.density - rx0) * scale))
            right = min(width - 1, round(((col + 1) * spec.density - rx0) * scale))
            if right <= left:
                continue
            ink, halo = _inks(luma, (left, py - 1, right, py + 2), style.color)
            draw.line([(left, py - 1), (right, py - 1)], fill=(*halo, alpha))
            draw.line([(left, py + 1), (right, py + 1)], fill=(*halo, alpha))
            draw.line([(left, py), (right, py)], fill=(*ink, alpha))


def _draw_labels(
    draw: ImageDraw.ImageDraw,
    luma: Image.Image,
    spec: GridSpec,
    style: GridStyle,
    region: tuple[float, float, float, float],
    scale: float,
    alpha: int,
) -> None:
    """Write each cell's global number in its top-left corner."""
    rx0, ry0, rx1, ry1 = region
    font = _font(max(1, round(spec.density * scale * _LABEL_FONT_RATIO)))
    col_first, col_last = int(rx0 // spec.density), math.ceil(rx1 / spec.density)
    row_first, row_last = int(ry0 // spec.density), math.ceil(ry1 / spec.density)
    for row in range(row_first, min(row_last, spec.rows)):
        for col in range(col_first, min(col_last, spec.cols)):
            number = str(spec.cell_number(col, row))
            x = round((col * spec.density - rx0) * scale) + _LABEL_PAD
            y = round((row * spec.density - ry0) * scale) + _LABEL_PAD
            box = draw.textbbox((x, y), number, font=font)
            ink, halo = _inks(luma, box, style.color)
            draw.text((x, y), number, font=font, fill=(*ink, alpha), stroke_width=1, stroke_fill=(*halo, alpha))


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow's bundled default font; the size argument needs Pillow >= 10.1.
    return ImageFont.load_default(size=size)
