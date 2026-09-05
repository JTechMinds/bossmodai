"""UI A5 — chat typing indicator is scoped to the selected agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_chat_typing_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_utils_exports_chat_typing_controller() -> None:
    source = _read("utils.js")
    assert "function createChatTypingController(" in source
    assert "createChatTypingController," in source
    assert "isActiveChat(typingAgentId)" in source


def test_chat_typing_is_agent_scoped() -> None:
    source = _read("agent-context.js")
    assert "const chatTyping = BossModUtils.createChatTypingController(" in source
    assert "chatTyping.show(agentId)" in source
    assert "chatTyping.hide(agentId)" in source
    assert "chatTyping.hide(data.agent_id)" in source
    assert "function showTypingIndicator(" not in source
    assert "function hideTypingIndicator(" not in source

    bind = source.split("function bindChatSend() {", 1)[1].split(
        "function appendChatMessage(", 1
    )[0]
    assert "chatTyping.show(agentId)" in bind
    assert "chatTyping.hide(agentId)" in bind
    assert "showTypingIndicator()" not in bind
    assert "hideTypingIndicator()" not in bind

    handle = source.split("function handleChatMessage(data) {", 1)[1].split(
        "async function handleChatReset(", 1
    )[0]
    assert handle.index("chatTyping.hide(data.agent_id)") < handle.index(
        "if (!selectedAgent || data.agent_id !== selectedAgent.id) return;"
    )


def test_chat_typing_harness_scopes_indicator_to_selected_agent() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "utils.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "paintsSelected": True,
        "ignoresForeignHide": True,
        "hidesOnSwitch": True,
        "restoresOnReturn": True,
        "ignoresForeignShow": True,
    }
