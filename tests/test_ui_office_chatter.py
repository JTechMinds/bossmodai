"""The Office chatter panel: a floor's agent-to-agent messages in Chat's context column."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_office_chatter_harness.cjs"

# The order the harness evaluates its modules in: index.html's order.
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "clamped-markdown.js",
    JS / "core" / "avatar.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "shell" / "floor-scope.js",
    JS / "context" / "office-chatter.js",
]


def _harness() -> dict:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_office_chatter_behaviour() -> None:
    """Every state renders, live rows reconcile with pages, and nothing leaks."""
    payload = _harness()
    failed = sorted(name for name, ok in payload.items() if ok is not True)
    assert failed == [], f"office chatter harness checks failed: {failed}"
    assert len(payload) == 25


def test_the_list_is_not_a_live_region() -> None:
    """Bursts of agent messages would drown a screen reader; the list is read on demand."""
    source = (JS / "context" / "office-chatter.js").read_text(encoding="utf-8")
    assert "aria-live" not in source
    assert "'role': 'log'" not in source and "role: 'log'" not in source
