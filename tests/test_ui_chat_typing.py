"""UI A5 — the thinking indicator is scoped to the selected conversation.

Phase 2A retired createChatTypingController's `isActiveChat` predicate for a
presence model keyed `conversationId::agentId`. Scoping is now structural:
state cannot leak between conversations because it is partitioned, not
guarded. Every property the old tests proved is re-proven here against the
new pair.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_chat_typing_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_gates_export_presence_controller() -> None:
    source = _read("core/gates.js")
    assert "function createChannelPresenceController(" in source
    assert "createChannelPresenceController," in source
    # The key IS the scoping: two conversations cannot collide.
    assert "return `${channelId}::${agentId}`;" in source


def test_presence_is_conversation_scoped() -> None:
    source = _read("conversation/transcript.js")
    assert "presence.list(conversationId)" in source
    assert "is thinking..." in source
    # Negative controls that keep the old global-indicator bug dead.
    assert "function showTypingIndicator(" not in source
    assert "function hideTypingIndicator(" not in source


def test_a_reply_clears_its_own_agents_indicator_even_when_unwatched() -> None:
    """The clear runs BEFORE the "is this my conversation" guard.

    Re-points the ordering assertion that guarded
    `chatTyping.hide(data.agent_id)` in agent-context.js. Phase 2A implemented
    the behaviour and documented it in a comment, but nothing enforced it: move
    the stop below the guard and a reply landing while the operator is looking
    elsewhere leaves that agent thinking forever, with every test still green.
    """
    source = _read("conversation/sources/agent-source.js")
    handler = source.split("bus.subscribe('chat_message', (data) => {", 1)[1].split(
        "bus.subscribe('chat_reset'", 1
    )[0]
    assert "presence.stop(data.agent_id, data.agent_id)" in handler
    assert "if (data.agent_id !== agentId) return;" in handler
    assert handler.index("presence.stop(data.agent_id, data.agent_id)") < handler.index(
        "if (data.agent_id !== agentId) return;"
    )


def test_chat_typing_harness_scopes_indicator_to_selected_agent() -> None:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "conversation" / "transcript.js"),
        ],
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
