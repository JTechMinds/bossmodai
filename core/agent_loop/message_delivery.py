"""BossMod AI — Shared message delivery semantics.

Keeps agent-to-agent message typing consistent so social chatter does not
accidentally become work delegation, while still preserving explicit work
handoffs once they already exist.
"""

from __future__ import annotations

from typing import Any

from core.models import AgentState


def resolve_peer_message_type(
    *,
    state: AgentState,
    trigger: dict[str, Any] | None = None,
) -> str:
    """Return the correct peer message type for one outbound agent message.

    Rules:
    - Agent-to-agent messages are conversational by default.
    - Social triggers always emit social messages.
    - Replies to peer messages stay social even if the incoming payload was older
      legacy `work` text; durable work delegation must use explicit task assignment.
    - This keeps coworker chatter from bootstrapping into accidental tasks.
    """
    del state
    if isinstance(trigger, dict) and str(trigger.get("type") or "").strip().lower() == "social":
        return "social"
    return "social"


def source_channel_for_message_type(message_type: str) -> str:
    """Return the trigger source channel that matches one persisted message type."""
    return "chat" if str(message_type).strip().lower() == "social" else "work"
