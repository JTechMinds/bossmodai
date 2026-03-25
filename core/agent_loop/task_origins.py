"""BossMod AI — Shared task origin mapping helpers."""

from __future__ import annotations

from typing import Any


def task_source_channel_for_trigger(trigger: dict[str, Any]) -> str | None:
    """Map an originating trigger to a durable task source channel."""
    trigger_type = trigger.get("type")
    if trigger_type == "human_chat":
        return "chat"
    if trigger_type == "peer_message":
        return "peer"
    if trigger_type == "session_response":
        return "meeting"
    if trigger_type == "channel_response":
        return "channel"
    if trigger_type == "task_assigned":
        return None
    return None


def task_notification_policy_for_trigger(trigger: dict[str, Any]) -> str | None:
    """Map an originating trigger to a durable task notification policy."""
    trigger_type = trigger.get("type")
    if trigger_type == "human_chat":
        return "completion_blocked"
    if trigger_type == "peer_message":
        return "none"
    if trigger_type == "session_response":
        return "completion_blocked" if trigger.get("author_type") == "human" else "none"
    if trigger_type == "channel_response":
        return "completion_blocked" if trigger.get("author_type") == "human" else "none"
    return None


def task_notification_channel_id_for_trigger(trigger: dict[str, Any]) -> str | None:
    """Return the shared channel target for later task notifications, if any."""
    if trigger.get("type") != "channel_response":
        return None
    channel_id = trigger.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    return channel_id
