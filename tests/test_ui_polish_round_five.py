"""Round five: the connections matrix, and two keyboard defects in the modal.

Round four pinned the modal's primary action and gave the dialog a backdrop.
Both are correct and both left something behind: the pinned primary is now the
LAST action, and `createModal` focused the last action on open — so opening
Hire put the keyboard on `Create Agent`. And a modal has never known about
another modal, so one Esc closed every open dialog at once.

Everything here that is a claim about a KEY PRESS is proven by driving the
built nodes, not by reading source. The source assertions in this file are
about layout that only a stylesheet can carry.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HERE = Path(__file__).resolve().parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """The declaration block of exactly one rule.

    Anchored on a newline and a following `{`, so `.connection-select` never
    picks up `.connection-select:focus` and `.modal-panel` never picks up
    `.modal-panel[data-size="panel"]`.
    """
    opener = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert opener, f"no rule for {selector}"
    return css[opener.end():].split("}", 1)[0]


def _run(harness: str, modules: list[Path]) -> dict:
    """Run a Node harness over an explicit module list and read its payload."""
    args = ["node", str(HERE / harness)] + [str(path) for path in modules]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


OVERLAY_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
]


def _overlays_payload() -> dict:
    return _run("js_overlays_harness.cjs", OVERLAY_MODULES)


# ─── Task 3: a form dialog focuses the form, not the submit button ───


def test_a_form_dialog_starts_in_the_form() -> None:
    """Enter must not submit an empty form the instant the dialog opens.

    `buttons[buttons.length - 1].focus()` was harmless while `Cancel` was the
    only pinned action. Round four put the primary last, so the same line
    landed the keyboard on `Create Agent`.
    """
    payload = _overlays_payload()
    assert payload["panelModalFocusesFirstBodyControl"] is True
    assert payload["panelModalDoesNotFocusThePrimary"] is True


def test_a_confirm_dialog_still_starts_on_the_safe_action() -> None:
    """Unchanged, and asserted so the fix above cannot regress it.

    Both assertions here PASSED before the fix — they are regression guards,
    not evidence of it. The second is the one that says which rule was
    implemented: it is a WIDE dialog with an empty body, so a fix keyed off
    the `size` flag would move its focus and a fix keyed off what the body
    holds leaves it alone.
    """
    payload = _overlays_payload()
    assert payload["confirmModalFocusesLastAction"] is True
    assert payload["bodylessPanelModalFocusesLastAction"] is True


# ─── Task 4: Escape closes one overlay, not the stack ───


def test_escape_closes_only_the_topmost_overlay() -> None:
    """A data-loss bug round four found by running the app, not by inference.

    Two open dialogs meant two `document` keydown listeners, and every one of
    them hears every key. `trapKeydown` called preventDefault() but nothing
    told a dialog it was underneath another, so one Escape closed the confirm
    AND the half-filled agent form behind it.

    stopPropagation() alone would not have fixed it: document-level listeners
    fire in REGISTRATION order, so the first dialog's handler runs first and
    stopping there closes the one the operator can't even see.
    """
    payload = _overlays_payload()
    assert payload["escapeClosesOnlyTheTopOverlay"] is True
    # The one it closes is the top one, not whichever bound first.
    assert payload["escapeClosesTheConfirmNotTheFormBehindIt"] is True
    # A second Escape then closes the one underneath — the dialog left
    # standing must still be operable, not merely still on screen.
    assert payload["secondEscapeClosesTheRemainingOverlay"] is True
    # One scrim for the whole stack through the whole sequence — the confirm
    # is a layer over the form — and none once the last layer closes.
    assert payload["oneScrimForTheWholeStack"] is True


# ─── Task 1: the connections matrix becomes a three-column grid ───

# The order tests/js_agent_form_harness.cjs evaluates its modules in.
AGENT_FORM_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "avatar.js",
    JS / "core" / "communication.js",
    # The dropdowns the bindings mount (core/menu-select.js) and their panel.
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
    JS / "core" / "menu-select.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-choices.js",
    JS / "context" / "agent-form-connections.js",
    JS / "context" / "agent-form-bindings.js",
    JS / "context" / "agent-submit.js",
]


def _agent_form_payload() -> dict:
    return _run("js_agent_form_harness.cjs", AGENT_FORM_MODULES)


def _grid_columns(css: str) -> int:
    """How many columns `.connection-grid` is authored for at full width."""
    rule = _rule(css, ".connection-grid")
    match = re.search(r"grid-template-columns:\s*repeat\((\d+),", rule)
    assert match, f"no repeat() column count in .connection-grid: {rule}"
    return int(match.group(1))


def _column_steps(css: str) -> list[tuple[int, int]]:
    """(breakpoint, column count) for each narrowing step, in source order."""
    return [
        (int(width), int(columns))
        for width, columns in re.findall(
            r"@media \(max-width:\s*(\d+)px\)\s*\{\s*\.connection-grid\s*\{\s*"
            r"grid-template-columns:\s*repeat\((\d+),",
            css,
        )
    ]


def test_the_thinking_levels_are_a_two_column_grid() -> None:
    """One connection across the column, the two thinking levels side by side.

    The section once rendered five per-activation connection rows; the runtime
    routes two activations, and an agent has one connection, so there is one
    full-width connection picker and a two-column row for the two thinking
    levels under it.

    The column count is read from the stylesheet: overlays.css is the only
    thing that lays the grid out, and a builder that also named a column count
    would be a second source of truth for a number it does not own.
    """
    payload = _agent_form_payload()
    assert payload["sectionCoversEveryRoutedMode"] is True
    # The connection spans the column above the grid, the levels share it.
    assert payload["thinkingIsAGridOfTwo"] is True

    css = _read(CSS / "overlays.css")
    assert _grid_columns(css) == 2
    # Two routed activations across two columns is ONE row.
    assert -(-2 // _grid_columns(css)) == 1

    # Every control is named by visible text the menu trigger repeats in its
    # accessible name.
    assert payload["everyControlIsNamedOnScreen"] is True


def test_a_long_connection_name_stays_recoverable() -> None:
    """Names are arbitrary operator input; the layout must not lose them.

    A label is `${name} (${model})`, so the picker's trigger can clip one.
    Clipping is acceptable only because it stays recoverable: the trigger's
    accessible name is the full label (core/menu-select.js), and the open menu
    lists every row in full.
    """
    payload = _agent_form_payload()
    assert payload["connectionOptionsNameTheModel"] is True
    css = _read(CSS / "controls.css")
    assert "text-overflow: ellipsis" in _rule(css, ".menu-select-field .menu-select-value")
    menu = _read(JS / "core" / "menu-select.js")
    assert "trigger.setAttribute('aria-label', `${label}: ${option.label}`);" in menu


def test_the_grid_collapses_before_it_crushes() -> None:
    """The dialog is `min(960px, calc(100vw - 32px))`, so its width follows the
    window's. Two columns in a half-width track crush on a small one.

    The property is unchanged — the grid must collapse before a select becomes
    unreadable — and the step count follows the base. It was 3 -> 2 -> 1 while
    the matrix spanned the whole dialog; the base is 2 now, so there is one
    step left to take.
    """
    css = _read(CSS / "overlays.css")
    assert "grid-template-columns" in _rule(css, ".connection-grid")
    assert "@media (max-width:" in css
    steps = _column_steps(css)
    assert [columns for _, columns in steps] == [1], steps
    # ...and it collapses at the SAME width the form itself does, so the matrix
    # never sits two-up in a column that has already gone full width.
    assert steps[0][0] == 900, steps
    form_grid_steps = [
        width for width, block in re.findall(
            r"@media \(max-width: (\d+)px\) \{(.*?)\}\s*\}", css, re.S)
        if ".agent-form-grid" in block
    ]
    assert form_grid_steps == ["900"], form_grid_steps


def test_the_ai_section_kept_every_behaviour() -> None:
    """A layout change to a markup builder is exactly where a behaviour quietly
    stops happening, and `sectionError` is the arity repair's guard: this
    harness CALLS the builder, and a source grep cannot see a throw."""
    payload = _agent_form_payload()
    assert payload["sectionError"] is None, payload["sectionError"]
    assert payload["sectionHasNoNativeSelect"] is True
    assert payload["sectionCarriesTheStoredChoice"] is True
    assert payload["anUnofferedLevelIsResetAndNamed"] is True
    assert payload["anUnlinkedAgentIsToldToChoose"] is True
    assert payload["aBlankFormSaysNothing"] is True
    assert payload["thinkingOptionsFollowTheConnection"] is True
    assert payload["emptySectionLinksToSettings"] is True
    assert payload["submitSendsTheConnectionAndLevels"] is True
    assert payload["submitSendsNullWhileUnchosen"] is True
