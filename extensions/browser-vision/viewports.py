"""Browser Vision — window presets and custom sizes (pure)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CUSTOM_RE = re.compile(r"^(\d+)x(\d+)$")


class WindowPreset(BaseModel):
    """A manifest window preset: a Playwright device name, or a plain size."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_kind(self) -> "WindowPreset":
        sized = self.width is not None and self.height is not None
        if (self.device is None) == (not sized) or (self.width is None) != (self.height is None):
            raise ValueError("a window preset is either {device} or {width, height}")
        return self


@dataclass(frozen=True)
class ViewportSpec:
    """The window an agent's browser context uses.

    Attributes:
        name: The preset name, or ``WxH`` for a custom size.
        device: A Playwright device descriptor name (sets size, touch, scale
            factor and user agent); ``None`` for a plain desktop viewport.
        width: CSS width for a plain viewport; ``None`` with a device.
        height: CSS height for a plain viewport; ``None`` with a device.
    """

    name: str
    device: str | None
    width: int | None
    height: int | None


def resolve_viewport(arg: str, presets: Mapping[str, WindowPreset]) -> ViewportSpec:
    """Resolve ``bv window`` input: a preset name or ``<W>x<H>``.

    Args:
        arg: What the agent typed.
        presets: The manifest's presets by name.

    Returns:
        The viewport to open contexts with.

    Raises:
        ValueError: Neither a preset nor a positive ``WxH``; the message
            lists the presets.
    """
    text = arg.strip().lower()
    preset = presets.get(text)
    if preset is not None:
        return ViewportSpec(name=text, device=preset.device, width=preset.width, height=preset.height)
    match = _CUSTOM_RE.match(text)
    if match is not None and int(match.group(1)) > 0 and int(match.group(2)) > 0:
        return ViewportSpec(name=text, device=None, width=int(match.group(1)), height=int(match.group(2)))
    names = "|".join(presets)
    raise ValueError(f"window must be {names} or <W>x<H> (e.g. 1024x768), got {arg!r}")
