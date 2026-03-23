"""BossMod AI — Deterministic chat receipts for silent human-facing actions."""

from __future__ import annotations

from typing import Any

import db
from core.models import Activity, Agent
from core.models.message import HUMAN_SENDER_ID

_DESTINATION_LABELS = {
    "desk": "desk",
    "meetingRoom": "Meeting Room",
    "breakRoom": "Break Room",
    "mainWorkspace": "Main Workspace",
    "southWorkspace": "South Workspace",
    "hallway": "hallway",
}

_HUMAN_VISIBLE_ACTIVITY_KINDS = {"conversation", "meeting"}
_RECEIPT_ACTIONS = {"walkTo", "attendMeeting", "remoteMeeting"}


def create_chat_receipt(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist and return a human-visible chat receipt when one is warranted."""
    text = build_chat_receipt_text(
        agent=agent,
        trigger=trigger,
        active_activity=active_activity,
        action=action,
        result=result,
    )
    if not text:
        return None

    message = db.create_message(
        from_agent=agent.id,
        to_agent=HUMAN_SENDER_ID,
        content=text,
        message_type="system",
    )
    return {
        "agent_id": agent.id,
        "content": message.content,
        "from_type": "system",
        "from_name": agent.name,
        "message_type": message.message_type,
        "message_id": message.id,
        "created_at": message.created_at,
    }


def build_chat_receipt_text(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    """Return the human-facing receipt text for a silent action."""
    if not _should_emit_receipt(trigger=trigger, active_activity=active_activity, action=action, result=result):
        return None

    action_name = action.get("action")
    if action_name == "walkTo":
        return f"{agent.name} is heading to the {_format_destination(action.get('destination'))}."
    if action_name == "attendMeeting":
        return f"{agent.name} joined the meeting."
    if action_name == "remoteMeeting":
        target_name = _resolve_target_name(result.get("detail", ""))
        if target_name:
            return f"{agent.name} started a remote meeting with {target_name}."
        return f"{agent.name} started a remote meeting."
    return None


def _should_emit_receipt(
    *,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    """Return whether the current turn/action should emit a chat receipt."""
    action_name = action.get("action")
    if action_name not in _RECEIPT_ACTIONS:
        return False
    if result.get("event") in {"agent_error", "guardian_violation", "world_feedback"}:
        return False

    trigger_type = trigger.get("type")
    if trigger_type == "human_chat":
        return True
    if trigger_type != "activity_resumed":
        return False
    if not active_activity or active_activity.kind not in _HUMAN_VISIBLE_ACTIVITY_KINDS:
        return False
    if action_name == "walkTo" and bool((active_activity.metadata or {}).get("acknowledged_by_reply")):
        return False
    return True


def _format_destination(destination: Any) -> str:
    """Humanize a runtime destination identifier."""
    if not isinstance(destination, str):
        return "destination"
    return _DESTINATION_LABELS.get(destination, destination)


def _resolve_target_name(detail: str) -> str | None:
    """Extract the meeting target name from the result detail string."""
    prefix = " started remote meeting with "
    if prefix not in detail:
        return None
    tail = detail.split(prefix, 1)[1]
    if ":" in tail:
        tail = tail.split(":", 1)[0]
    name = tail.strip()
    return name or None
