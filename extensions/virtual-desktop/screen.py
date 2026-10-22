"""Virtual Desktop — screenshot scaling and coordinate mapping (pure).

The screen is captured at its real size and shrunk only when its longest
side exceeds ``image_max_px``. Coordinates the agent gives are pixels of the
image it was sent; the screen gets ``image px / scale``. ``point1k``
coordinates are 0–1000 of the image, where 1000 is the last pixel, so
0,0 is the top-left corner and 1000,1000 the bottom-right one.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from .docker_host import Capture

# The 0–1000 scale's top end.
POINT1K_MAX = 1000
# vm wait compares the screen at this size, in greyscale.
_CHANGE_KEY_SIZE = (64, 40)


@dataclass(frozen=True)
class Rendered:
    """The image the model is sent.

    Attributes:
        png: Its bytes.
        width: Its width in pixels.
        height: Its height in pixels.
        scale: Image px per screen px (1.0 when not shrunk).
    """

    png: bytes
    width: int
    height: int
    scale: float


def fit(png: bytes, max_px: int) -> Rendered:
    """Shrink a screenshot so its longest side is at most ``max_px``.

    Args:
        png: The captured PNG.
        max_px: The longest side allowed.

    Returns:
        The PNG unchanged (scale 1.0) when it fits, else a LANCZOS-shrunk copy.

    Raises:
        PIL.UnidentifiedImageError: ``png`` is not an image.
    """
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
        longest = max(width, height)
        if longest <= max_px:
            return Rendered(png=png, width=width, height=height, scale=1.0)
        scale = max_px / longest
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        shrunk = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    shrunk.save(buffer, format="PNG")
    return Rendered(png=buffer.getvalue(), width=size[0], height=size[1], scale=scale)


def image_to_screen(x: int, y: int, scale: float) -> tuple[int, int]:
    """Map image pixels to screen pixels."""
    return round(x / scale), round(y / scale)


def screen_to_image(x: int, y: int, scale: float) -> tuple[int, int]:
    """Map screen pixels to image pixels."""
    return round(x * scale), round(y * scale)


def parse_point(raw_x: str, raw_y: str, rendered: Rendered, *, thousandths: bool) -> tuple[int, int]:
    """Parse ``point``/``point1k`` coordinates into image pixels of ``rendered``.

    Args:
        raw_x: The typed x.
        raw_y: The typed y.
        rendered: The latest screenshot.
        thousandths: ``point1k`` units (0–1000 of the image) instead of pixels.

    Returns:
        ``(x, y)`` in image pixels.

    Raises:
        ValueError: Not a number, or outside the image (the message names
            the valid range); nothing is clamped.
    """
    if thousandths:
        valid = f"x and y must be 0–{POINT1K_MAX} (vm point1k scale)"
        limits = (POINT1K_MAX, POINT1K_MAX)
    else:
        valid = (
            f"x must be 0–{rendered.width - 1}, y 0–{rendered.height - 1} "
            f"(pixels of the latest screenshot, image {rendered.width}x{rendered.height})"
        )
        limits = (rendered.width - 1, rendered.height - 1)
    values: list[float] = []
    for raw, limit in zip((raw_x, raw_y), limits):
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{valid}; got {raw!r}") from None
        if not math.isfinite(value) or not 0 <= value <= limit:
            raise ValueError(f"{valid}; got {raw!r}")
        values.append(value)
    x, y = values
    if thousandths:
        return round(x * (rendered.width - 1) / POINT1K_MAX), round(y * (rendered.height - 1) / POINT1K_MAX)
    return round(x), round(y)


def image_text(rendered: Rendered) -> str:
    """The value of the ``image`` result line: ``1280x800`` or ``1568x882 (scale ×0.8167)``."""
    text = f"{rendered.width}x{rendered.height}"
    if rendered.scale != 1:
        text += f" (scale ×{rendered.scale:.4g})"
    return text


def pointer_text(capture: Capture, rendered: Rendered) -> str:
    """The value of the ``pointer`` result line, from the real pointer.

    E.g. ``(412, 488) px = (322, 610)‰ — over "Mozilla Firefox"``: image
    pixels, thousandths of the image, and the window under the pointer
    (left out when that window has no title).
    """
    x, y = screen_to_image(capture.pointer[0], capture.pointer[1], rendered.scale)
    kx = round(x * POINT1K_MAX / (rendered.width - 1))
    ky = round(y * POINT1K_MAX / (rendered.height - 1))
    text = f"({x}, {y}) px = ({kx}, {ky})‰"
    if capture.pointer_window:
        text += f' — over "{capture.pointer_window}"'
    return text


def change_key(png: bytes) -> bytes:
    """A small greyscale thumbnail of the screen, for ``vm wait`` to compare.

    Raises:
        PIL.UnidentifiedImageError: ``png`` is not an image.
    """
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("L").resize(_CHANGE_KEY_SIZE, Image.Resampling.BOX).tobytes()
