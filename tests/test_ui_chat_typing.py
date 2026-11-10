"""UI A5 — the thinking indicator is scoped to the selected conversation.

Phase 2A retired createChatTypingController's `isActiveChat` predicate for a
presence model keyed `conversationId::agentId`. Presence is now page-lifetime
store state (core/presence.js, keyed agent + channel, like the server's
registry), and each source selects its own rows (`presenceFor`). Scoping stays
structural: a conversation paints only what its source selects. Every
property the old tests proved is re-proven here against the new pair.
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


def test_presence_rows_are_keyed_by_agent_and_channel() -> None:
    source = _read("core/presence.js")
    assert "function applyEvent(" in source
    # The key IS the scoping: two turns cannot collide.
    assert "row.agentId === agentId && row.channelId === channelId" in source
    # The per-conversation controller is gone; the store slice replaced it.
    assert "createChannelPresenceController" not in _read("core/gates.js")


def test_presence_is_conversation_scoped() -> None:
    source = _read("conversation/transcript.js")
    # The transcript paints the rows it is handed; each source chooses them.
    assert "function renderPresence(rows)" in source
    assert "is thinking..." in source
    assert "function presenceFor(rows)" in _read("conversation/sources/agent-source.js")
    assert "function presenceFor(rows)" in _read("conversation/sources/thread-source.js")
    # Negative controls that keep the old global-indicator bug dead.
    assert "function showTypingIndicator(" not in source
    assert "function hideTypingIndicator(" not in source


def test_the_turns_idle_clears_its_indicator_even_when_unwatched() -> None:
    """The turn's own idle clears the row, whatever conversation is open.

    Phase 2A cleared an agent's row when its reply landed, and that clear had
    to run before the "is this my conversation" guard, or a reply landing
    while the operator looked elsewhere left the agent thinking forever. The
    reply no longer clears anything: the dispatcher's idle (sent from the
    turn's `finally`) is authoritative, and it lands in the page-lifetime
    store with no conversation guard at all, so it can never be missed — and
    a turn that keeps working after replying keeps its row. The behaviour is
    proven in tests/js_channel_presence_harness.cjs (idleWhileAway).
    """
    source = _read("conversation/sources/agent-source.js")
    handler = source.split("if (topic === 'chat_message') {", 1)[1].split(
        "if (topic === 'chat_reset')", 1
    )[0]
    assert "presence" not in handler
    assert "if (data.agent_id !== agentId) return;" in handler
    presence = _read("core/presence.js")
    assert "bus.subscribe('agent_presence'" in presence
    assert "'agent_presence'" not in _read("conversation/conversation-focus-invalidate.js")


def test_chat_typing_harness_scopes_indicator_to_selected_agent() -> None:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "store.js"),
            str(JS / "core" / "bus.js"),
            str(JS / "core" / "presence.js"),
            str(JS / "conversation" / "note-group.js"),
            str(JS / "conversation" / "transcript.js"),
            str(JS / "conversation" / "sources" / "agent-requests.js"),
            str(JS / "conversation" / "sources" / "agent-source.js"),
            str(JS / "context" / "agent-menu.js"),
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
