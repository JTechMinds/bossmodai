"""UI A4 — chat/meeting composers keep drafts until send is acknowledged."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_composer_send_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_utils_exports_composer_send_gate() -> None:
    source = _read("utils.js")
    assert "function createComposerSendGate()" in source
    assert "function setComposerError(" in source
    assert "createComposerSendGate," in source
    assert "setComposerError," in source


def test_chat_send_waits_for_ack_and_blocks_inflight() -> None:
    source = _read("agent-context.js")
    assert "const chatSend = BossModUtils.createComposerSendGate()" in source
    chat = source.split("function bindChatSend() {", 1)[1].split(
        "function appendChatMessage(", 1
    )[0]
    assert "await chatSend.submit(" in chat
    assert "el.value = ''" not in chat
    assert "input.value = ''" not in chat
    assert "appendChatMessage('Failed to reach agent.', 'agent')" in chat
    apply_state = source.split("function applyChatSendState() {", 1)[1].split(
        "function bindChatSend()", 1
    )[0]
    assert "chatSend.busy()" in apply_state
    assert "sendBtn.disabled = !allowed || chatSend.busy()" in apply_state
    assert "input.disabled = !allowed || chatSend.busy()" in apply_state


def test_meeting_send_surfaces_error_and_keeps_draft() -> None:
    source = _read("agent-context.js")
    assert "const meetingSend = BossModUtils.createComposerSendGate()" in source
    assert 'id="meeting-send-error"' in source
    bind = source.split("function bindMeetingSend(sessionId) {", 1)[1].split(
        "function handleMeetingMessage(", 1
    )[0]
    assert "await meetingSend.submit(" in bind
    assert "input.value = ''" not in bind
    assert "BossModUtils.setComposerError(" in bind
    assert "console.error('[AgentContext] Failed to send meeting message:" not in bind
    assert "Failed to send meeting message." in bind


def test_composer_send_harness_keeps_draft_and_blocks_double_submit() -> None:
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
        "keptDraftOnFailure": True,
        "clearedOnSuccess": True,
        "blockedDoubleSubmit": True,
        "surfacedError": True,
    }
