"""core/overlays.js traps focus, closes on Esc, and restores focus."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_overlays_harness.cjs"


def _overlay_modules() -> dict[str, str]:
    """The overlay module set: core/overlays.js and anything split out of it.

    Round five split the shared focus/Esc machinery into core/overlay-focus.js,
    because core/overlays.js had reached exactly 299 lines under a 300-line cap
    and a bug fix in createModal had nowhere to land. `core/overlay*.js` is the
    whole set by construction — a fourth overlay or a second trap would have to
    be written into a core module named `overlay…` to be one of these, and
    anywhere else it is caught by test_ui_index.py's tree-wide createModal
    guard instead.
    """
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((JS / "core").glob("overlay*.js"))
    }


def test_both_overlays_share_one_focus_trap() -> None:
    """One trap, or they drift — and one of them did.

    createModal's trap used to cycle over its own action buttons and return
    without preventing the default whenever focus sat anywhere else. Modals do
    carry focusable bodies (context/desk-opener.js puts radios and a text input
    in one), so Tab walked out of the dialog into the page behind it. slideOver
    had the correct implementation five lines away. Two implementations of one
    rule is how that happened; this test is why it cannot happen again.

    Counted across the module SET rather than down one file. The property is
    unchanged and so are the numbers: one definition, three wirings. Splitting
    the file moved where they live, not how many there may be.
    """
    modules = _overlay_modules()
    assert modules, "core/ holds no overlay module"
    sources = list(modules.values())
    assert sum(text.count("function trapKeydown(") for text in sources) == 1
    # ...and all of that one definition is in ONE file. A split that left a
    # copy behind in a file the count happened to balance out would pass the
    # sum above; this is what makes the split unable to become the duplication
    # the test was written to prevent.
    definers = sorted(name for name, text in modules.items()
                      if "function trapKeydown(" in text)
    assert len(definers) == 1, definers
    # Three overlays share it — the modal, the slide-over, and the anchored
    # menu the chat header's `⋯` opens. The count is exact on purpose: a fourth
    # overlay that quietly skipped the trap would otherwise pass.
    assert sum(
        text.count("const onKeydown = (event) => trapKeydown(event, element, close);")
        for text in sources
    ) == 3
    joined = "\n".join(sources)
    # The trap must consider everything focusable in the overlay, not one row.
    assert "element.querySelectorAll(FOCUSABLE)" in joined
    # And it must recover focus that has already escaped, rather than shrugging.
    assert "if (!element.contains(active)) {" in joined
    # The old button-only cycle must not come back in either overlay.
    assert "buttons.indexOf(document.activeElement)" not in joined


def test_modal_accessibility_contract() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js"), str(JS / "core" / "overlay-focus.js"),
         str(JS / "core" / "overlays.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "hasDialogSemantics": True,
        "focusesSafeAction": True,
        "escClosesWithoutConfirming": True,
        "restoresFocus": True,
        "unbindsOnClose": True,
        # The wide SIZE, added with the agent form: one implementation, more
        # room, and a body that scrolls with the title and the action row
        # pinned outside it. Listed here because this file owns the modal's
        # contract and the variant is part of it — a scrolling body of form
        # controls is exactly where a focus trap leaks.
        "wideModalIsMarked": True,
        "wideModalActionsSitOutsideTheBody": True,
        "wideModalTrapsTabAcrossItsBody": True,
        "wideModalEscCloses": True,
        "wideModalRestoresFocus": True,
        # Round five: WHERE a dialog opens the keyboard. Listed here because
        # the opening focus is part of the modal's contract and round four's
        # pinned primary made the old answer wrong — the last action is the
        # safe choice in a confirm dialog and the submit button in a form one.
        # The rule keys off what the BODY holds, which is why a wide dialog
        # with an empty body is in the list beside the confirm dialog.
        # tests/test_ui_polish_round_five.py reads the same four.
        "wideModalFocusesFirstBodyControl": True,
        "wideModalDoesNotFocusThePrimary": True,
        "confirmModalFocusesLastAction": True,
        "bodylessWideModalFocusesLastAction": True,
        # Round four gave the modal the backdrop it never had: the panel used
        # to float over a live page and clicks reached the controls behind it.
        # Listed here because blocking the page is part of the modal contract,
        # and because the backdrop leaving WITH the panel is the property that
        # matters most — a leaked scrim makes the whole app unclickable.
        # tests/test_ui_polish_round_four.py reads the same five.
        "backdropExists": True,
        "backdropSitsBehindThePanel": True,
        "backdropClickDoesNotDismiss": True,
        "closeRemovesBackdrop": True,
        "escStillCloses": True,
        # Round five: two open dialogs are two document keydown listeners, and
        # every one of them hears every key — so one Esc used to close the
        # confirm AND the half-filled form behind it. Listed here because Esc
        # dismissal is this file's contract and "dismisses WHICH one" is part
        # of it. tests/test_ui_polish_round_five.py reads the same four.
        "escapeClosesOnlyTheTopOverlay": True,
        "escapeClosesTheConfirmNotTheFormBehindIt": True,
        "secondEscapeClosesTheRemainingOverlay": True,
        "backdropCountTracksPanelCount": True,
        # The third shape, added with the chat header's `⋯`: non-modal and
        # anchored, and owing the same keyboard contract as the other two.
        "menuFocusesFirstOption": True,
        "menuTrapsTab": True,
        "menuEscCloses": True,
        "menuRestoresFocusToTheAnchor": True,
        "menuNeedsAnAnchor": True,
    }


def test_modal_does_not_use_window_confirm() -> None:
    """window.confirm is unstyleable and blocks the event loop."""
    source = (JS / "core" / "overlays.js").read_text(encoding="utf-8")
    assert "window.confirm" not in source
    assert "confirm(" not in source
