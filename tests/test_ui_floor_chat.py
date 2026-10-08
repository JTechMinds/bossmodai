"""shell/floor-chat.js — each floor remembers its own chat, and the chat follows the floor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_floor_chat_harness.cjs"

MODULES = [
    JS / "core" / "store.js",
    JS / "shell" / "floor-scope.js",
    JS / "shell" / "floor-chat.js",
]


def test_floor_chat_behaviour() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "attachRequiresAStore": True,
        "inertBeforeSettle": True,
        "settleRestoresTheFloorsChat": True,
        "settleKeepsABootTimeOpen": True,
        "switchOpensThatFloorsChat": True,
        "switchToAnEmptyFloorIsEmpty": True,
        "switchBackRestores": True,
        "staleAgentEntryIsEmpty": True,
        "threadEntryIsValidated": True,
        "openRecordsUnderItsFloor": True,
        "crossFloorOpenFollows": True,
        "unknownConversationRecordsOnTheCurrentFloor": True,
        "noOpOpenDoesNotNotify": True,
        "destroyDrainsTheStore": True,
    }

