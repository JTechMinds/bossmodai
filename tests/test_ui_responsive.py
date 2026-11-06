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
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
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


def _resolve_tokens(css: str) -> str:
    """Inline every `var(--x)` that tokens.css defines, as a number.

    The scan below reads sizes off the stylesheet, so a dimension moved into
    tokens.css would otherwise drop out of it — the rule would still be there
    and the floor would simply stop being checked. Resolving first is what
    keeps the net as wide as `test_touch_targets_meet_the_minimum` says it is.
    """
    tokens = dict(re.findall(
        r"--([\w-]+)\s*:\s*([^;]+);", (CSS / "tokens.css").read_text(encoding="utf-8")))
    return re.sub(r"var\(--([\w-]+)\)",
                  lambda match: tokens.get(match.group(1), match.group(0)).strip(), css)


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


def _hides(block: str, selector: str) -> bool:
    """Whether any rule in `block` that names `selector` alone hides it."""
    rules = re.finditer(rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", block)
    return any(re.search(r"display:\s*none", rule.group(1)) for rule in rules)


def test_the_phone_header_sheds_controls_and_keeps_the_logo() -> None:
    """<768px: a shrink order by removal, not by squeezing everything.

    The audit found two ☰s, "BossMod" clipped, and the floor icon drawn over
    the brand at 360/390. The rail toggle has no rail to toggle there, the
    word gives way, and the floor switcher drops its decorative icon to pay
    for the logo — which stays, at full size (Jordan: "We need at least our
    icon logo"), so `.brand` stops shrinking rather than being hidden.
    """
    narrow = _media_block(_shell_css(), BREAKPOINTS[1])
    for selector in (".header-rail-toggle", ".brand-name", ".floor-switcher-icon"):
        assert _hides(narrow, selector), f"{selector} is still shown under 768px"
    assert re.search(r"\.brand\s*\{\s*flex:\s*none;\s*\}", narrow), "the logo can still be squeezed"
    for kept in (".brand", ".brand-mark"):
        assert not _hides(narrow, kept), f"{kept} is hidden: the logo must stay"
        assert not _hides(_shell_css(), kept), f"{kept} is hidden: the logo must stay"
    # The icon the rule hides is classed by the switcher; without the class
    # the rule above would match nothing.
    switcher = (JS / "shell" / "floor-switcher.js").read_text(encoding="utf-8")
    assert "'data-lucide': 'layers', class: 'floor-switcher-icon'" in switcher


def test_the_tablet_header_drops_the_nav_labels_but_not_the_names() -> None:
    """768–1199px: the labelled nav (~466px) overflowed the header and clipped
    the brand to "Bc". The places go icon-only, the tab bar below 768px gets
    its words back, and each button keeps its name for assistive tech and its
    tooltip for a pointer.
    """
    css = _shell_css()
    medium = _media_block(css, BREAKPOINTS[0])
    narrow = _media_block(css, BREAKPOINTS[1])
    assert _hides(medium, ".place-nav-label")
    # The context opener's "Office" repeated the Office place's name. Hidden
    # here, which every narrower width matches too.
    assert _hides(medium, ".responsive-btn-label")
    assert re.search(r"\.place-nav-label\s*\{\s*display:\s*inline;\s*\}", narrow), (
        "the bottom tab bar lost its labels"
    )
    # The <768 block comes later, so it wins at equal specificity.
    assert css.index(BREAKPOINTS[1]) > css.index(BREAKPOINTS[0])

    header = (JS / "shell" / "header.js").read_text(encoding="utf-8")
    nav_button = header.split("class: 'place-nav-item',", 1)[1].split("onclick:", 1)[0]
    assert "'aria-label': place.label," in nav_button
    assert "'data-tooltip': place.label," in nav_button


def test_the_dead_full_width_drawer_rule_is_gone() -> None:
    """`.responsive-panel { width: 100vw; }` lost to the panel size in
    overlays.css on specificity, so it never applied. The full-screen sheet is
    overlays.css's <768px rule now; a copy here would be dead again.
    """
    assert not re.search(r"\.responsive-panel\s*\{[^}]*\bwidth", _shell_css())


def test_no_gesture_only_controls() -> None:
    """SC 2.1.1. Everything the grid hides is reachable from a real button.

    A drawer that only opens on a swipe is unreachable by keyboard, by switch
    control, and by anyone using a pointing device that does not swipe. The
    harness proves both openers are named buttons that bind no gesture; this
    proves no module anywhere made a touch handler the only way in.
    """
    payload = _harness()
    assert payload["openersAreNamedButtons"] is True
    # The column is the office summary alone; its opener says so.
    assert payload["contextOpenerSaysOffice"] is True
    assert payload["presentsTheLiveColumn"] is True
    assert payload["onePanelAtATime"] is True
    assert payload["restoresColumnOrder"] is True
    assert payload["widenClosesThePanel"] is True
    assert payload["hiddenWhereThereIsNoContext"] is True
    # mount() hands back the drawer's door, so Chat's empty state opens the
    # same one; choosing a conversation closes the drawer, and only the drawer.
    assert payload["openRosterPresentsTheRoster"] is True
    assert payload["conversationClosesTheRoster"] is True
    assert payload["conversationLeavesTheContext"] is True

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

    # The overlay is the modal's, with its focus trap and its Esc — not a
    # third implementation living in the responsive layer. Round five split
    # the trap into core/overlay-focus.js, so the two halves of that sentence
    # are now read off the two files that own them rather than off one.
    responsive = (JS / "shell" / "responsive.js").read_text(encoding="utf-8")
    assert "BossModOverlays.createModal({" in responsive
    assert "size: 'panel'" in responsive
    assert "slideOver" not in responsive
    assert "addEventListener('keydown'" not in responsive, "the trap is the overlay chain's"
    core = CSS.parent / "js" / "core"
    overlays = (core / "overlays.js").read_text(encoding="utf-8")
    focus = (core / "overlay-focus.js").read_text(encoding="utf-8")
    assert "trapKeydown" in overlays, "the modal must still wire the shared trap"
    assert "trapKeydown" in focus and "Escape" in focus


def test_touch_targets_meet_the_minimum() -> None:
    """SC 2.5.8: 24x24 CSS px, and the narrow layout is where it gets tested.

    Shrinking a control to fit is the obvious way to make a bottom tab bar hold
    six items, and it is the one thing that must not happen. Every size a rule
    inside the <768px block sets is checked, so a later tweak has to defend
    itself rather than slip through.
    """
    narrow = _resolve_tokens(_media_block(_shell_css(), BREAKPOINTS[1]))
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
