"""BossMod AI — Shared message delivery semantics.

Keeps agent-to-agent message typing consistent so social chatter does not
accidentally become work delegation, while still preserving explicit work
handoffs once they already exist.
"""

from __future__ import annotations

from typing import Any

from core.models import AgentState, Message


def resolve_peer_message_type(
    *,
    state: AgentState,
    trigger: dict[str, Any] | None = None,
) -> str:
    """Return the correct peer message type for one outbound agent message.

    Rules:
    - Agent-to-agent messages are conversational by default.
    - Social triggers always emit social messages.
    - Replies to peer messages stay social even if the incoming payload used an
      older `work` label; durable work delegation must use explicit task assignment.
    - This keeps coworker chatter from bootstrapping into accidental tasks.
    """
    del state
    if isinstance(trigger, dict) and str(trigger.get("type") or "").strip().lower() == "social":
        return "social"
    if isinstance(trigger, dict):
        incoming_type = str(trigger.get("message_type") or "").strip().lower()
        if incoming_type == "meeting":
            return "meeting"
    return "social"


def source_channel_for_message_type(message_type: str) -> str:
    """Return the trigger source channel that matches one persisted message type."""
    return "chat" if str(message_type).strip().lower() == "social" else "work"


def peer_message_event(message: Message) -> dict[str, Any]:
    """Return the one row shape of an agent↔agent message for the operator.

    Shared by the ``peer_message`` runtime event and ``GET /office/chatter``,
    so the Office chatter panel reads live and loaded rows identically.

    Args:
        message: A persisted agent↔agent row.

    Returns:
        ``message_id``, ``from_agent_id``, ``to_agent_id``, ``content``,
        ``message_type``, ``created_at`` (ISO 8601, serialized as
        ``db.get_formatted_messages`` does for the chat history route) and
        ``floor_id`` (read from the persisted row).

    Raises:
        ValueError: The row has no floor. A peer row without one is a bug at
            the send site, not something to show.
    """
    if not message.floor_id:
        raise ValueError(f"Peer message {message.id} has no floor_id")
    return {
        "message_id": message.id,
        "from_agent_id": message.from_agent,
        "to_agent_id": message.to_agent,
        "content": message.content,
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat(),
        "floor_id": message.floor_id,
    }
