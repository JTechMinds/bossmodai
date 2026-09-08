"""core/overlays.js traps focus, closes on Esc, and restores focus."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_overlays_harness.cjs"


def test_both_overlays_share_one_focus_trap() -> None:
    """One trap, or they drift — and one of them did.

    createModal's trap used to cycle over its own action buttons and return
    without preventing the default whenever focus sat anywhere else. Modals do
    carry focusable bodies (context/desk-opener.js puts radios and a text input
    in one), so Tab walked out of the dialog into the page behind it. slideOver
    had the correct implementation five lines away. Two implementations of one
    rule is how that happened; this test is why it cannot happen again.
    """
    source = (JS / "core" / "overlays.js").read_text(encoding="utf-8")
    assert source.count("function trapKeydown(") == 1
    # Three overlays share it now — the modal, the slide-over, and the anchored
    # menu the chat header's `⋯` opens. The count is exact on purpose: a fourth
    # overlay that quietly skipped the trap would otherwise pass.
    assert source.count("const onKeydown = (event) => trapKeydown(event, element, close);") == 3
    # The trap must consider everything focusable in the overlay, not one row.
    assert "element.querySelectorAll(FOCUSABLE)" in source
    # And it must recover focus that has already escaped, rather than shrugging.
    assert "if (!element.contains(active)) {" in source
    # The old button-only cycle must not come back in either overlay.
    assert "buttons.indexOf(document.activeElement)" not in source


def test_modal_accessibility_contract() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js"), str(JS / "core" / "overlays.js")],
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
