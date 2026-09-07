"""Session restore is validated against live data, never trusted."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_session_harness.cjs"


def test_session_restore_is_validated() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "shell" / "session.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "whitelistsKeys": True,
        "dropsStaleConversation": True,
        "fallsBackOnUnknownPlace": True,
        "discardsCorruptBlob": True,
        "fillsDefaults": True,
    }


def test_session_does_not_persist_server_collections() -> None:
    source = (JS / "shell" / "session.js").read_text(encoding="utf-8")
    assert "PERSISTED_KEYS" in source
    for forbidden in ("roster", "needs", "messages", "tasks"):
        assert f"'{forbidden}'" not in source, (
            f"{forbidden} is server data and must never be persisted to localStorage"
        )
