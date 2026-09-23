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
    guard instead. The one named exception is core/menu.js: createMenu moved
    there when core/overlays.js reached its 400-line cap, and it is listed by
    name so the counts below still cover both overlays.
    """
    paths = [*sorted((JS / "core").glob("overlay*.js")), JS / "core" / "menu.js"]
    return {path.name: path.read_text(encoding="utf-8") for path in paths}


def test_both_overlays_share_one_focus_trap() -> None:
    """One trap, or they drift — and one of them did.

    createModal's trap used to cycle over its own action buttons and return
    without preventing the default whenever focus sat anywhere else. Modals do
    carry focusable bodies (context/desk-opener.js puts radios and a text input
    in one), so Tab walked out of the dialog into the page behind it. slideOver
    had the correct implementation five lines away. Two implementations of one
    rule is how that happened; this test is why it cannot happen again.

    Counted across the module SET rather than down one file. The property is
    unchanged and so are the numbers: one definition, two wirings. Splitting
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
    joined = "\n".join(sources)
    # Two overlays share it — the modal and the anchored menu the chat
    # header's `⋯` opens. The slide-over was the third until 2026-09-21, when
    # every secondary screen moved into the modal. The count is exact on
    # purpose: a third overlay that quietly skipped the trap would pass.
    assert sum(
        text.count("const onKeydown = (event) => trapKeydown(event, element, close);")
        for text in sources
    ) == 2
    assert "function slideOver(" not in joined
    # The trap must consider everything focusable in the overlay, not one row.
    assert "element.querySelectorAll(FOCUSABLE)" in joined
    # And it must recover focus that has already escaped, rather than shrugging.
    assert "if (!element.contains(active)) {" in joined
    # The old button-only cycle must not come back in either overlay.
    assert "buttons.indexOf(document.activeElement)" not in joined


def test_modal_accessibility_contract() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js"), str(JS / "core" / "overlay-focus.js"),
         str(JS / "core" / "overlays.js"), str(JS / "core" / "menu.js")],
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
        # An action may opt out of the close every other action ends in:
        # "Save as template" opens a layer over the agent form, which must
        # still be under it. Opt-in, so everything else still closes.
        "keepOpenActionStaysOpen": True,
        "plainActionStillCloses": True,
        # The panel SIZE (born `wide`, with the agent form): one implementation,
        # more room, and a body that scrolls with the title and the action row
        # pinned outside it. Listed here because this file owns the modal's
        # contract and the variant is part of it — a scrolling body of form
        # controls is exactly where a focus trap leaks.
        "panelModalIsMarked": True,
        "panelModalActionsSitOutsideTheBody": True,
        "panelModalTrapsTabAcrossItsBody": True,
        "panelModalEscCloses": True,
        "panelModalRestoresFocus": True,
        # Round five: WHERE a dialog opens the keyboard. Listed here because
        # the opening focus is part of the modal's contract and round four's
        # pinned primary made the old answer wrong — the last action is the
        # safe choice in a confirm dialog and the submit button in a form one.
        # The rule keys off what the BODY holds, which is why a panel dialog
        # with an empty body is in the list beside the confirm dialog.
        # tests/test_ui_polish_round_five.py reads the same four.
        "panelModalFocusesFirstBodyControl": True,
        "panelModalDoesNotFocusThePrimary": True,
        "confirmModalFocusesLastAction": True,
        "bodylessPanelModalFocusesLastAction": True,
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
        # 2026-09-21: the chat-chrome frame. Every dialog carries one head row —
        # title, optional subtitle and tools, and its own ✕ — and the ✕ closes
        # exactly as Esc does. Outside click is OPT-IN: the default binds nothing
        # (backdropClickDoesNotDismiss above), a caller may pass true, and a
        # function is asked at click time so a viewer can refuse mid-edit.
        "headIsFirstAndOrdered": True,
        "panelSizeIsDeclared": True,
        "closeButtonTakesFocusWhenNothingElseCan": True,
        "closeLabelNamesTheDialog": True,
        "closeButtonCloses": True,
        "optedInBackdropCloses": True,
        "guardRefusesWhileEditing": True,
        "guardAllowsOnceClean": True,
        "rejectsBadBackdropOption": True,
        "badOptionMountsNothing": True,
        # A stop counts only once it has TAKEN focus: focus() on a hidden
        # control is a silent no-op in a browser, and the file viewer opens with
        # its editor hidden in the body.
        "skipsAHiddenFirstControl": True,
        "allHiddenBodyFallsBackToClose": True,
        # The trap's first/last are RENDERED controls: Tab must wrap past a hidden one.
        "trapWrapsPastAHiddenLastControl": True,
        # Retired 2026-09-21: every secondary screen is a modal now.
        "slideOverIsGone": True,
        # Round five: two open dialogs are two document keydown listeners, and
        # every one of them hears every key — so one Esc used to close the
        # confirm AND the half-filled form behind it. Listed here because Esc
        # dismissal is this file's contract and "dismisses WHICH one" is part
        # of it. tests/test_ui_polish_round_five.py reads the same four.
        "escapeClosesOnlyTheTopOverlay": True,
        "escapeClosesTheConfirmNotTheFormBehindIt": True,
        "secondEscapeClosesTheRemainingOverlay": True,
        # Once "one scrim per dialog". The confirm is a layer over the form
        # now, so the property is one scrim for the whole stack at every step,
        # and none once the last layer closes.
        "oneScrimForTheWholeStack": True,
        "lowerLayerIsHidden": True,
        # 2026-09-21: modals never stack — a modal opened from a modal is a layer.
        "oneFrameOnScreen": True,
        "backNamesTheLayerBeneath": True,
        "backReturnsToTheLayerBeneath": True,
        "escGoesBackOneLayer": True,
        "closeClosesTheWholeStack": True,
        "scrimSparesAStackHoldingAForm": True,
        "formStackClosedByTheX": True,
        "middleCloseRelabels": True,
        "lostOpenerFallsBackToClose": True,
        # A layer closing beneath another hands its opener to the one above,
        # so a base that closes first never strands focus on a detached node.
        "survivorBecomesTheBase": True,
        "baseCloseHandsItsOpenerUp": True,
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


def test_modal_frame_is_the_chat_chrome() -> None:
    """The modal's head is the conversation header's row, floated.

    Same rhythm line (--bar), same hairline, same 15px/600 title as
    .conversation-chrome, and the regions own their spacing rather than the
    panel padding around them. The empty footer rule lives HERE, once, for
    every size — it used to be the takeover's alone in marketplace.css.
    """
    css_dir = ROOT / "ui" / "static" / "css"
    css = (css_dir / "overlays.css").read_text(encoding="utf-8")
    head = css.split(".modal-head {", 1)[1].split("}", 1)[0]
    assert "min-height: var(--bar)" in head
    assert "border-bottom: 1px solid var(--line)" in head
    title = css.split(".modal-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px" in title and "font-weight: 600" in title
    actions = css.split(".modal-actions {", 1)[1].split("}", 1)[0]
    assert "border-top: 1px solid var(--line)" in actions
    assert ".modal-actions:empty { display: none; }" in css
    # Three sizes. A content modal scales with the window — a fixed box sized
    # for the old 420px slide-out read as tiny on a wide screen.
    panel = css.split('.modal-panel[data-size="panel"] {', 1)[1].split("}", 1)[0]
    assert "width: 80vw" in panel and "height: 85vh" in panel
    assert "min-width: min(640px, calc(100vw - 32px))" in panel
    takeover = css.split('.modal-panel[data-size="takeover"] {', 1)[1].split("}", 1)[0]
    assert "width: 95vw" in takeover and "height: 95vh" in takeover
    assert 'data-size="medium"' not in css and 'data-size="wide"' not in css
    assert ".modal-body :is(p, li) { max-width: 100ch; }" in css
    assert "@keyframes modal-panel-in" in css and "@keyframes modal-scrim-in" in css
    base = css.split(".modal-panel {", 1)[1].split("}", 1)[0]
    assert "padding:" not in base, "the head, body and footer own their spacing"
    market = (css_dir / "marketplace.css").read_text(encoding="utf-8")
    assert ".modal-actions:empty" not in market, "one definition, in overlays.css"


# Questions with nothing to type: an outside click is a "no", exactly like Esc.
CONFIRMS = (
    "shell/header.js", "places/files/file-ops.js", "places/tasks/tasks-cancel.js",
    "context/agent-recovery.js", "context/desk-actions.js",
    "conversation/sources/thread-archive.js", "conversation/sources/thread-seat.js",
)
# Dialogs that hold typing: a stray click must not throw it away.
FORMS = ("context/desk-opener.js", "context/agent-edit.js")


def test_confirms_close_on_backdrop_and_forms_do_not() -> None:
    for relative in CONFIRMS:
        source = (JS / relative).read_text(encoding="utf-8")
        calls = source.count("BossModOverlays.createModal(")
        assert calls >= 1, relative
        assert source.count("closeOnBackdrop: true") == calls, (
            f"{relative}: every confirm it opens must close on an outside click"
        )
    for relative in FORMS:
        source = (JS / relative).read_text(encoding="utf-8")
        assert "closeOnBackdrop" not in source, f"{relative} holds typing"
