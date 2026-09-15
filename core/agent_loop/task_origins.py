"""BossMod AI — Shared task origin mapping helpers."""

from __future__ import annotations

from typing import Any

import db

# Focus/DM ingress stays Focus-only even when a channel-origin task is active.
_FOCUS_ORIGIN_TRIGGER_TYPES = frozenset({"human_chat", "peer_message"})


def _nonempty_id(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def task_origin_channel_id(task: Any | None) -> str | None:
    """Return the shared-thread stamp from conversation Assign, if any.

    Same bar as origin mirrors: ``source_channel=channel`` plus
    ``notification_channel_id``. A leftover channel id on a Focus/API task
    does not count.
    """
    if task is None:
        return None
    if getattr(task, "source_channel", None) != "channel":
        return None
    return _nonempty_id(getattr(task, "notification_channel_id", None))


def consent_origin_channel_id(
    trigger: dict[str, Any] | None,
    *,
    task_id: str | None = None,
) -> str | None:
    """Channel where thread-born CLI / consent / resume cards must post.

    Prefer an explicit trigger ``channel_id`` (channel_message, already-stamped
    resume). Otherwise use the bound task's Assign origin stamp. Focus/DM
    triggers stay Focus-only.
    """
    if not isinstance(trigger, dict):
        trigger = {}
    found = _nonempty_id(trigger.get("channel_id"))
    if found:
        return found
    payload = trigger.get("payload")
    if isinstance(payload, dict):
        found = _nonempty_id(payload.get("channel_id"))
        if found:
            return found
    if str(trigger.get("type") or "") in _FOCUS_ORIGIN_TRIGGER_TYPES:
        return None
    resolved_task_id = _nonempty_id(trigger.get("task_id"))
    if not resolved_task_id and isinstance(payload, dict):
        resolved_task_id = _nonempty_id(payload.get("task_id"))
    if not resolved_task_id:
        resolved_task_id = _nonempty_id(task_id)
    if not resolved_task_id:
        return None
    return task_origin_channel_id(db.get_task(resolved_task_id))


def stamp_trigger_origin_channel(
    trigger: dict[str, Any] | None,
    *,
    task_id: str | None = None,
) -> None:
    """Copy the thread origin onto the live trigger so CLI/resume keep it."""
    if not isinstance(trigger, dict):
        return
    if _nonempty_id(trigger.get("channel_id")):
        return
    found = consent_origin_channel_id(trigger, task_id=task_id)
    if found:
        trigger["channel_id"] = found


def stamp_origin_channel_payload(payload: dict[str, Any], task: Any | None) -> None:
    """Put ``channel_id`` on a queued trigger payload for thread-origin work."""
    channel_id = task_origin_channel_id(task)
    if channel_id:
        payload["channel_id"] = channel_id


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
