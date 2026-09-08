"""shell/header.js — Pause is a click plus a dialog, and every control is named."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_header_harness.cjs"

MODULES = [
    ("core", "dom.js"), ("core", "store.js"),
    ("core", "overlay-focus.js"), ("core", "overlays.js"),
    ("shell", "places.js"), ("shell", "header.js"),
]

# A press-and-hold has no keyboard equivalent. On the emergency stop that is a
# WCAG 2.2 SC 2.1.1 failure on the one control that must always work.
HOLD_GESTURES = ("mousedown", "touchstart", "pointerdown", "setTimeout")


def _source() -> str:
    return (JS / "shell" / "header.js").read_text(encoding="utf-8")


def test_pause_binds_no_hold_gesture() -> None:
    source = _source()
    for gesture in HOLD_GESTURES:
        assert gesture not in source, (
            f"Pause must not be a hold gesture; '{gesture}' appears in header.js"
        )
    assert "BossModOverlays.createModal(" in source, (
        "Pause must confirm through the focus-trapped modal, not a hold"
    )


def test_header_accessibility_and_pause_flow() -> None:
    args = ["node", str(HARNESS)] + [str(JS / d / f) for d, f in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "everyIconOnlyButtonIsLabelled": True,
        "navMarksActivePlace": True,
        "bellLabelStatesCount": True,
        "pauseConfirmsResumeDoesNot": True,
        "disposerDrainsSubscriptions": True,
    }


def test_header_does_not_use_window_confirm() -> None:
    """The emergency stop must not depend on a dialog that cannot be styled or tested."""
    source = _source()
    assert "window.confirm" not in source
    assert "confirm(" not in source
