"""core/store.js notifies per-selector and never leaks subscriptions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_store_harness.cjs"


def test_store_selector_semantics_and_leak_guard() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "store.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "firesOnlyOnChange": True,
        "disposersDetach": True,
        "noLeakAfter50Cycles": True,
        "isolatesThrowingSubscriber": True,
    }


def test_store_exposes_subscriber_count_for_leak_detection() -> None:
    source = (JS / "core" / "store.js").read_text(encoding="utf-8")
    assert "function subscriberCount(" in source, (
        "subscriberCount is the leak guard from spec 13 — it is test surface, not debug code"
    )
