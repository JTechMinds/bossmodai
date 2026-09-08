"""The three breakpoints (spec 10), and the accessibility floor they must meet.

Until Phase 4 the new CSS declared zero width media queries: the app worked at
one width and nowhere else, which is not a styling gap but an accessibility
one — WCAG 2.2 SC 1.4.10 asks for content at 320 CSS px without two-dimensional
scrolling, and a fixed three-column grid cannot give it.

Three things are asserted here and each catches a different way this can go
wrong: the widths themselves, that whatever the grid removes stays reachable
from a real keyboard-operable control, and that nothing in the narrow layout
shrinks a target below 24x24 (SC 2.5.8).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HARNESS = Path(__file__).resolve().parent / "js_responsive_harness.cjs"

HARNESS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "places.js",
    JS / "shell" / "responsive.js",
]

# Spec 10, exactly. The two upper bounds are one pixel under the boundaries the
# spec names, which is what a max-width query for "below 768" and "below 1200"
# has to say.
BREAKPOINTS = ("(max-width: 1199px)", "(max-width: 767px)")


def _shell_css() -> str:
    return (CSS / "shell.css").read_text(encoding="utf-8")


def _media_block(css: str, query: str) -> str:
    """The body of one `@media` block, brace-matched."""
    marker = f"@media {query} {{"
    start = css.index(marker) + len(marker) - 1
    depth = 0
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1 : index]
    raise AssertionError(f"unterminated @media {query}")


def _harness() -> dict:
    result = subprocess.run(
        ["node", str(HARNESS)] + [str(path) for path in HARNESS_MODULES],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_three_breakpoints_exist() -> None:
    """Two width queries plus the unqualified base, and no third opinion.

    The >=1200px layout is the rules outside any width query — spec 10 says it
    is unchanged, so it is asserted by NOT being wrapped in one. A fourth width
    would mean a layout nothing in the spec describes.
    """
    css = _shell_css()
    widths = re.findall(r"@media\s*\(((?:max|min)-width:[^)]+)\)", css)
    assert widths == ["max-width: 1199px", "max-width: 767px"], widths

    # The base grid is still the three-column one, outside every media block.
    base = css
    for query in BREAKPOINTS:
        base = base.replace(_media_block(css, query), "")
    assert "grid-template-columns: var(--rail) minmax(0, 1fr) var(--ctx);" in base

    # 768-1199: the context column leaves the grid, the roster stays.
    medium = _media_block(css, BREAKPOINTS[0])
    assert "#main-layout > .app-context { display: none; }" in medium
    assert ".app-roster" not in medium, "the roster stays in the grid at this width"

    # <768: one column, the roster leaves too, and the nav becomes a bottom bar.
    narrow = _media_block(css, BREAKPOINTS[1])
    assert "grid-template-columns: minmax(0, 1fr);" in narrow
    assert "#main-layout > .app-roster { display: none; }" in narrow
    assert "position: fixed;" in narrow and "bottom: 0;" in narrow
    # The place nav is the SAME nav, moved — not a second implementation that
    # could fall out of step with the place registry.
    assert ".place-nav {" in narrow
    assert len(re.findall(r"place-nav-item", narrow)) >= 1

    # The dock era's mobile path was a stub with this string and no content.
    # test_ui_index.py bans it from the markup; it must not come back as copy.
    for source in (css, (JS / "shell" / "responsive.js").read_text(encoding="utf-8")):
        assert "Swipe up to expand" not in source


def test_no_gesture_only_controls() -> None:
    """SC 2.1.1. Everything the grid hides is reachable from a real button.

    A drawer that only opens on a swipe is unreachable by keyboard, by switch
    control, and by anyone using a pointing device that does not swipe. The
    harness proves both openers are named buttons that bind no gesture; this
    proves no module anywhere made a touch handler the only way in.
    """
    payload = _harness()
    assert payload["openersAreNamedButtons"] is True
    assert payload["presentsTheLiveColumn"] is True
    assert payload["onePanelAtATime"] is True
    assert payload["restoresColumnOrder"] is True
    assert payload["widenClosesThePanel"] is True
    assert payload["hiddenWhereThereIsNoContext"] is True

    # No module may bind a gesture at all, let alone as a sole opener. The
    # settings resize handle is the one exception and is documented as such:
    # it is a pointer affordance ON TOP OF a panel that is already usable.
    allowed = {"settings/settings-shared.js"}
    offenders = []
    for path in sorted(JS.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        relative = path.relative_to(JS).as_posix()
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # The real DOM event names. A "swipe" is one of these plus arithmetic,
        # so banning the events is what bans the gesture.
        for gesture in ("touchstart", "pointerdown", "touchend", "pointerup", "touchmove"):
            if gesture in text:
                offenders.append(f"{relative} binds {gesture}")
    assert offenders == [], "\n".join(offenders)

    # The overlay is core/overlays.js's, with its focus trap and its Esc — not
    # a third implementation living in the responsive layer.
    responsive = (JS / "shell" / "responsive.js").read_text(encoding="utf-8")
    assert "BossModOverlays.slideOver(" in responsive
    assert "addEventListener('keydown'" not in responsive, "the trap is overlays.js's"
    overlays = (CSS.parent / "js" / "core" / "overlays.js").read_text(encoding="utf-8")
    assert "trapKeydown" in overlays and "Escape" in overlays


def test_touch_targets_meet_the_minimum() -> None:
    """SC 2.5.8: 24x24 CSS px, and the narrow layout is where it gets tested.

    Shrinking a control to fit is the obvious way to make a bottom tab bar hold
    six items, and it is the one thing that must not happen. Every size a rule
    inside the <768px block sets is checked, so a later tweak has to defend
    itself rather than slip through.
    """
    narrow = _media_block(_shell_css(), BREAKPOINTS[1])
    sized = re.findall(r"\b(min-width|min-height|width|height)\s*:\s*([0-9.]+)px", narrow)
    assert sized, "the narrow layout sets no sizes at all"
    for prop, value in sized:
        assert float(value) >= 24, f"{prop}: {value}px is under the 24px floor"

    # The openers themselves carry the floor, outside any query, so they meet
    # it at every width they are visible at.
    css = _shell_css()
    buttons = css.split(".responsive-menu-btn,", 1)[1].split("}", 1)[0]
    assert "min-width: 44px;" in buttons
    assert "min-height: 44px;" in buttons

    # The tab bar's items are targets, not labels: each one carries its own
    # floor rather than relying on the bar's height.
    item = narrow.split(".place-nav-item {", 1)[1].split("}", 1)[0]
    assert re.search(r"min-height:\s*(4[8-9]|[5-9]\d)px", item), item
    assert re.search(r"min-width:\s*(2[4-9]|[3-9]\d)px", item), item
