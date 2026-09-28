"""Browser Vision — element marks: the page's clickable elements, numbered (pure).

Marks come from plain page inspection (see ``browser_host``): no model, no
guessing. Each visible, hit-testable control gets a number in reading order,
an outline and a tag on the screenshot, and a line in the result legend, so
an agent can act on ``@n`` instead of aiming at pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, TypeVar

Box = tuple[float, float, float, float]
T = TypeVar("T")

MARK_KINDS = frozenset(
    {"link", "button", "textbox", "checkbox", "radio", "select", "tab", "menuitem", "option", "switch", "other"}
)
MARK_STATES = frozenset({"checked", "unchecked", "empty", "filled"})
# Rows of marks are read top to bottom in bands this tall, left to right in a band.
_ROW_BUCKET_PX = 8
# The smallest visible mark, in CSS px on each side.
MIN_MARK_PX = 4


@dataclass(frozen=True)
class Mark:
    """One numbered control on the latest screenshot.

    Attributes:
        n: 1-based number, in reading order.
        kind: One of :data:`MARK_KINDS`.
        name: The control's accessible-ish name, ≤ 40 chars (may be empty).
        rect: ``(x0, y0, x1, y1)`` in page CSS px, clipped to the viewport.
        point: The hit-tested click point, in page CSS px.
        state: ``checked``/``unchecked``/``empty``/``filled`` or ``None``.
    """

    n: int
    kind: str
    name: str
    rect: Box
    point: tuple[float, float]
    state: str | None


@dataclass(frozen=True)
class RawMark:
    """A mark as one frame reported it, before page offsets and numbering."""

    kind: str
    name: str
    rect: Box
    point: tuple[float, float]
    state: str | None


def raw_from_js(item: dict[str, Any]) -> RawMark:
    """Validate one object the in-page script returned.

    Raises:
        ValueError: A field is missing or of the wrong kind (the script and
            this parser disagree — a bug, never page content).
    """
    kind = str(item["kind"])
    state = item.get("state")
    if kind not in MARK_KINDS or (state is not None and state not in MARK_STATES):
        raise ValueError(f"unexpected mark from the page script: {item!r}")
    x0, y0, x1, y1 = (float(v) for v in item["rect"])
    px, py = (float(v) for v in item["point"])
    return RawMark(kind=kind, name=str(item.get("name") or ""), rect=(x0, y0, x1, y1), point=(px, py), state=state)


def place(
    raw: Iterable[tuple[RawMark, float, float, T]],
    viewport_w: float,
    viewport_h: float,
) -> list[tuple[Mark, T]]:
    """Offset frame marks into page coordinates, clip, and number them.

    Args:
        raw: ``(mark, frame offset x, frame offset y, key)`` for every frame's
            marks; ``key`` (the frame) is handed back beside each mark.
        viewport_w: Page viewport width in CSS px.
        viewport_h: Page viewport height in CSS px.

    Returns:
        ``(mark, key)`` for marks at least :data:`MIN_MARK_PX` on each side
        after clipping, whose click point is inside the viewport, numbered in
        reading order (top to bottom in 8 px bands, then left to right).
    """
    placed: list[tuple[RawMark, T]] = []
    for mark, dx, dy, key in raw:
        x0, y0, x1, y1 = mark.rect
        clipped = (max(0.0, x0 + dx), max(0.0, y0 + dy), min(viewport_w, x1 + dx), min(viewport_h, y1 + dy))
        px, py = mark.point[0] + dx, mark.point[1] + dy
        if clipped[2] - clipped[0] < MIN_MARK_PX or clipped[3] - clipped[1] < MIN_MARK_PX:
            continue
        if not (0 <= px < viewport_w and 0 <= py < viewport_h):
            continue
        placed.append((RawMark(kind=mark.kind, name=mark.name, rect=clipped, point=(px, py), state=mark.state), key))
    placed.sort(key=lambda item: (int(item[0].rect[1] // _ROW_BUCKET_PX), item[0].rect[0]))
    return [
        (Mark(n=index, kind=m.kind, name=m.name, rect=m.rect, point=m.point, state=m.state), key)
        for index, (m, key) in enumerate(placed, start=1)
    ]


def legend_line(mark: Mark) -> str:
    """One legend line, e.g. ``[12] textbox "Enter your address" (empty)``."""
    line = f'[{mark.n}] {mark.kind} "{mark.name}"'
    return f"{line} ({mark.state})" if mark.state else line


def feedback_line(hit: dict[str, Any] | None) -> str:
    """Describe what a click landed on, from the in-page ``describe`` result.

    Returns:
        ``clicked <kind> "<name>"`` for a control, ``clicked <tag> (not a
        control)`` otherwise, ``clicked nothing`` when the point is empty.
    """
    if not hit:
        return "clicked nothing"
    if hit.get("kind"):
        return f'clicked {hit["kind"]} "{hit.get("name") or ""}"'
    return f"clicked {str(hit.get('tag') or 'element').lower()} (not a control)"
