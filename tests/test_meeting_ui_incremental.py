"""Meeting UI: routine updates must not remount the session shell."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_meeting_session_harness.cjs"


def _read_agent_context() -> str:
    return (JS / "agent-context.js").read_text(encoding="utf-8")


def test_world_update_reloads_only_on_activity_kind_change() -> None:
    source = _read_agent_context()
    assert "MeetingSessionDom.shouldReloadOnWorldUpdate(previousActivityKind, nextActivityKind)" in source
    assert "|| nextActivityKind === 'meeting'" not in source
    assert re.search(
        r"function handleWorldUpdate\(agents\) \{.*?void renderMeeting\(\);",
        source,
        flags=re.S,
    )


def test_meeting_message_appends_when_session_is_mounted() -> None:
    source = _read_agent_context()
    handler = source.split("function handleMeetingMessage(data) {", 1)[1].split(
        "function handleChannelMessage(", 1
    )[0]
    assert "MeetingSessionDom.isMounted(container)" in handler
    assert "MeetingSessionDom.appendMessage(" in handler
    assert "void renderMeeting()" in handler
    # Unconditional remount on every WS message is the P0.
    assert handler.index("MeetingSessionDom.appendMessage(") < handler.index("void renderMeeting()")


def test_render_meeting_keeps_mounted_shell() -> None:
    source = _read_agent_context()
    render = source.split("async function renderMeeting() {", 1)[1].split(
        "function renderMeetingEmpty(", 1
    )[0]
    assert "const keepShell = MeetingSessionDom.isMounted(container)" in render
    assert "if (!keepShell)" in render
    assert "Loading meeting..." in render
    assert "MeetingSessionDom.updateSessionChrome(" in render
    assert "MeetingSessionDom.syncMessages(" in render
    assert "renderMeetingSession(container, payload.session)" in render


def test_meeting_session_dom_harness_preserves_draft_and_dedups() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "agent-context.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "mounted": True,
        "preservedDraft": True,
        "messageCount": 2,
    }
