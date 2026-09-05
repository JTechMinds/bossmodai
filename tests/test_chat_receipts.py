"""HA-PROD-P2-01 — walk_to from human_chat emits a chat-visible receipt."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_loop.notifications import project_chat_notifications


def test_walk_to_from_human_chat_emits_receipt() -> None:
    agent = SimpleNamespace(id="agent-1", name="Alex")
    notes = project_chat_notifications(
        agent=agent,
        trigger={"type": "human_chat"},
        active_activity=None,
        action={"action": "walkTo", "destination": "desk"},
        result={"event": "agent_updated"},
    )
    assert len(notes) == 1
    assert notes[0].kind == "receipt"
    assert notes[0].chat_visible is True
    assert "heading to" in notes[0].content
    assert "desk" in notes[0].content


def test_idle_non_receipt_action_does_not_emit() -> None:
    agent = SimpleNamespace(id="agent-1", name="Alex")
    notes = project_chat_notifications(
        agent=agent,
        trigger={"type": "human_chat"},
        active_activity=None,
        action={"action": "idle"},
        result={"event": "agent_updated"},
    )
    assert notes == []
