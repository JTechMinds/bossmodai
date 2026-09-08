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
    `.modal-panel[data-size="wide"]`.
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
    JS / "core" / "overlays.js",
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
    assert payload["wideModalFocusesFirstBodyControl"] is True
    assert payload["wideModalDoesNotFocusThePrimary"] is True


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
    assert payload["bodylessWideModalFocusesLastAction"] is True


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
    # Backdrops track their panels through the whole sequence.
    assert payload["backdropCountTracksPanelCount"] is True


# ─── Task 1: the connections matrix becomes a three-column grid ───

# The order tests/js_agent_form_harness.cjs evaluates its modules in.
AGENT_FORM_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "avatar.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-connections.js",
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


def test_the_matrix_is_a_three_column_grid() -> None:
    """Five activation types rendered as five full-width rows.

    Each `<select>` spanned ~600px to display the word `None` and the block
    cost ~264px of a dialog that already scrolls.

    The plan asked for `payload["matrixColumns"] == 3` off the markup builder.
    The count is read from the stylesheet instead: overlays.css is the only
    thing that lays the grid out, and a builder that also named a column count
    would be a second source of truth for a number it does not own. The
    property — three columns — is asserted, on the value that actually applies.
    """
    payload = _agent_form_payload()
    # Still five activation types, still one Set All. Both of these PASSED
    # before this round: they are regression guards on the fieldset the layout
    # change moves, not evidence that it moved.
    assert payload["matrixCoversEveryModelType"] is True, payload["matrixSelectNames"]
    # `Set All` writes to the other five rather than being a sixth value, so it
    # stays full width above the grid. That is what stops it reading as one.
    assert payload["setAllIsOutsideTheGrid"] is True

    css = _read(CSS / "overlays.css")
    assert _grid_columns(css) == 3
    # Which is the whole point: five selects across three columns is TWO rows
    # where five full-width rows cost five.
    activation_types = len(payload["matrixSelectNames"]) - 1
    assert activation_types == 5, payload["matrixSelectNames"]
    assert -(-activation_types // _grid_columns(css)) == 2

    # Labels above, and associated — not bare spans. A form control whose label
    # is a `<span>` beside it is a screen-reader dead end.
    assert payload["everySelectHasAnAssociatedLabel"] is True


def test_a_long_connection_name_stays_recoverable() -> None:
    """Names are arbitrary operator input; the layout must not lose them.

    A label is `${name} (${model})`, so a three-column select at the dialog's
    760px shows roughly thirty characters of one. Truncation is acceptable only
    because it stays recoverable: the `title` carries the full label on hover,
    and the native dropdown popup is not bounded by the control's width.
    """
    payload = _agent_form_payload()
    assert payload["optionsCarryTitleAttribute"] is True, (
        payload["optionsMissingTheirFullLabel"]
    )
    css = _read(CSS / "overlays.css")
    assert "text-overflow: ellipsis" in _rule(css, ".connection-select")


def test_the_grid_collapses_before_it_crushes() -> None:
    """The dialog is `min(760px, calc(100vw - 32px))`, so its width follows the
    window's. Three columns crush on a small one."""
    css = _read(CSS / "overlays.css")
    assert "grid-template-columns" in _rule(css, ".connection-grid")
    assert "@media (max-width:" in css
    # To two, then to one. A single query dropping straight to one column would
    # satisfy the line above and still crush at 700px.
    steps = _column_steps(css)
    assert [columns for _, columns in steps] == [2, 1], steps
    # Widest breakpoint first: two equal-specificity queries are resolved by
    # source order, so the narrower one has to come last to ever apply.
    widths = [width for width, _ in steps]
    assert widths == sorted(widths, reverse=True), steps


def test_the_matrix_kept_every_behaviour() -> None:
    """All four PASSED before this round. They are here because a layout change
    to a markup builder is exactly where a behaviour quietly stops happening,
    and because `matrixError` is the round-one arity repair's guard: this
    harness CALLS the builder, and a source grep cannot see a throw."""
    payload = _agent_form_payload()
    assert payload["matrixError"] is None, payload["matrixError"]
    assert payload["matrixRendersConnectionOptions"] is True
    assert payload["matrixPreselectsTheStoredModel"] is True
    assert payload["emptyMatrixLinksToSettings"] is True
