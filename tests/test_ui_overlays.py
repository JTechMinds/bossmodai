"""core/overlays.js traps focus, closes on Esc, and restores focus."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_overlays_harness.cjs"


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
    }


def test_modal_does_not_use_window_confirm() -> None:
    """window.confirm is unstyleable and blocks the event loop."""
    source = (JS / "core" / "overlays.js").read_text(encoding="utf-8")
    assert "window.confirm" not in source
    assert "confirm(" not in source
