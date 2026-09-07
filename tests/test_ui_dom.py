"""core/dom.js builds elements and hands back working disposers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_dom_harness.cjs"


def test_dom_helpers_behave() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "skipsNullAttrs": True,
        "flattensChildren": True,
        "disposerRemovesListener": True,
    }


def test_delegate_returns_disposer() -> None:
    source = (JS / "core" / "dom.js").read_text(encoding="utf-8")
    assert "function delegate(" in source
    assert "removeEventListener" in source, "delegate must be undoable"
