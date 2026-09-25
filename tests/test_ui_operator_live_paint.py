"""Operator surfaces live-paint through one invalidate router."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_operator_invalidate_harness.cjs"


def test_operator_invalidate_harness() -> None:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "bus.js"),
            str(JS / "core" / "operator-invalidate.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip())
    assert payload == {
        "ok": True,
        "liveChatPaint": True,
        "settingsLivePaint": True,
        "singleBusWire": True,
    }


def test_conversation_sources_route_through_operator_invalidate() -> None:
    agent = (JS / "conversation" / "sources" / "agent-source.js").read_text(encoding="utf-8")
    thread = (JS / "conversation" / "sources" / "thread-source.js").read_text(encoding="utf-8")
    assert "BossModOperatorInvalidate.register" in agent
    assert "BossModOperatorInvalidate.register" in thread
    assert "bus.subscribe('chat_message'" not in agent
    assert "bus.subscribe('channel_message'" not in thread


def test_settings_mutations_broadcast_operator_invalidate() -> None:
    source = (ROOT / "api" / "routes" / "settings.py").read_text(encoding="utf-8")
    assert "broadcast_operator_invalidate" in source
    assert "await _broadcast_operator_surfaces" in source


def test_shell_attaches_operator_invalidate_before_socket() -> None:
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "BossModOperatorInvalidate.attach" in shell
    assert shell.index("BossModOperatorInvalidate.attach") < shell.index("BossModSocket.createSocket")
