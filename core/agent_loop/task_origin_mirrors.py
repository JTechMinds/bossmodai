"""Event-driven origin-thread mirrors for task status changes.

When work moves (accept, Writing path, waiting, stalled, complete/claim),
the originating channel or Focus thread must get a short system one-liner.
Profile activity alone does not count. Heartbeats and section-by-section
writer updates must not post here.
"""

from __future__ import annotations

from typing import Any, Literal

import db
from core.agent_loop.notifications import ChatNotification, persist_channel_notification, persist_chat_notification
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID

OriginThread = Literal["channel", "chat"]


def origin_thread_target(task: Any | None) -> OriginThread | None:
    """Return the operator-visible origin for one task, if any."""
    if task is None:
        return None
    channel_id = getattr(task, "notification_channel_id", None)
    source_channel = getattr(task, "source_channel", None)
    if isinstance(channel_id, str) and channel_id.strip() and source_channel == "channel":
        return "channel"
    if getattr(task, "requester_id", None) != HUMAN_SENDER_ID:
        return None
    if source_channel not in {"chat", "api"}:
        return None
    if (getattr(task, "notification_policy", None) or "none") == "none":
        return None
    return "chat"


def format_origin_status_line(
    *,
    kind: str,
    agent: Agent,
    task: Any,
    reason: str | None = None,
    path: str | None = None,
) -> str:
    """Return the one-liner handed to the origin thread (outcome, not edits)."""
    title = str(getattr(task, "title", None) or "the task").strip() or "the task"
    note = (reason or "").strip()
    if kind == "accepted":
        return f'{agent.name} accepted "{title}".'
    if kind == "waiting":
        if note:
            return f'{agent.name} is waiting on "{title}": {note}'
        return f'{agent.name} is waiting on "{title}".'
    if kind == "stalled":
        if note:
            return f'{agent.name} stalled on "{title}": {note}'
        return f'{agent.name} stalled on "{title}".'
    if kind == "progress":
        target = (path or note or "").strip()
        if target:
            return f"Writing {target}" if not target.lower().startswith("writing ") else target
        return f'{agent.name} is writing "{title}".'
    if kind == "completion":
        if note:
            return f'{agent.name} finished "{title}". {note}'.strip()
        return f'{agent.name} finished "{title}".'
    if note:
        return f'{agent.name} updated "{title}": {note}'
    return f'{agent.name} updated "{title}".'


def persist_origin_status_line(
    *,
    task: Any,
    agent: Agent,
    content: str,
    kind: str,
) -> dict[str, Any]:
    """Persist one system status line on the origin thread. No peer wake."""
    text = (content or "").strip()
    target = origin_thread_target(task)
    if not text or target is None:
        return {}
    if target == "channel":
        channel_id = str(task.notification_channel_id).strip()
        latest = db.get_latest_channel_message(channel_id)
        if latest is not None and (latest.content or "").strip() == text:
            return {}
        notification = persist_channel_notification(
            agent,
            ChatNotification(
                kind="task_update",
                content=text,
                source_channel="channel",
                policy=str(getattr(task, "notification_policy", None) or "completion_blocked"),
                prompt_visibility=False,
                task_id=getattr(task, "id", None),
                channel_id=channel_id,
            ),
        )
        return {"channel_message": notification}

    if _chat_already_has_line(agent.id, getattr(task, "id", None), text):
        return {}
    chat_message = persist_chat_notification(
        agent,
        ChatNotification(
            kind="task_update",
            content=text,
            source_channel=str(getattr(task, "source_channel", None) or "chat"),
            policy=str(getattr(task, "notification_policy", None) or "completion_blocked"),
            prompt_visibility=False,
            task_id=getattr(task, "id", None),
        ),
    )
    return {"chat_message": chat_message}


def attach_origin_status_line_if_silent(
    result: dict[str, Any],
    *,
    task: Any,
    agent: Agent,
    content: str,
    kind: str,
) -> None:
    """Post a fallback origin line only when this turn has not spoken yet."""
    if result.get("channel_message") or result.get("chat_message"):
        return
    posted = persist_origin_status_line(task=task, agent=agent, content=content, kind=kind)
    if posted.get("channel_message"):
        result["channel_message"] = posted["channel_message"]
    if posted.get("chat_message"):
        result["chat_message"] = posted["chat_message"]


def _chat_already_has_line(agent_id: str, task_id: str | None, content: str) -> bool:
    """Return whether the same origin line was just persisted for this agent."""
    for note in db.list_notifications(agent_id=agent_id, limit=8):
        if task_id and note.task_id != task_id:
            continue
        if (note.content or "").strip() == content:
            return True
    return False
