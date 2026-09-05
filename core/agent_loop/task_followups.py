"""Task follow-up and stakeholder-report helpers for execution actions.

Mechanical extract from actions.py (HA-STRUCT-P1-02). Decision-runtime
follow-up persist is a different contract and is left in place.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.activity_scheduler import (
    build_task_follow_up_trigger,
    build_task_update_trigger,
)
from core.agent_loop.task_roles import (
    task_assignment_reply_target,
    task_report_recipient_ids,
)
from core.models.message import HUMAN_SENDER_ID
from core.models import Agent, AgentState
from core.tasking.service import append_task_event
import db


def _append_task_stakeholder_reports(
    *,
    result: dict[str, Any],
    actor: Agent,
    state: AgentState,
    task: Any | None,
    content: str,
    skip_recipient_ids: set[str] | None = None,
    attention_kind: str | None = None,
    source_task_event_id: str | None = None,
) -> None:
    """Send durable task updates to the requesting/owning agents."""
    if task is None or not isinstance(content, str) or not content.strip():
        return

    skipped = skip_recipient_ids or set()
    recipients = task_report_recipient_ids(task, actor_id=actor.id)
    if not recipients:
        return

    trigger_requests = result.setdefault("trigger_requests", [])
    for recipient_id in recipients:
        db.create_notification(
            agent_id=recipient_id,
            task_id=task.id,
            kind="task_update",
            content=content.strip(),
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
    if attention_kind is None:
        return

    reply_target = task_assignment_reply_target(task, assignee_id=actor.id)
    if reply_target["kind"] != "agent" or not reply_target["agent_id"]:
        return
    if reply_target["agent_id"] in skipped:
        return
    effective_kind = _effective_attention_kind(attention_kind, content)
    requires_response = _attention_kind_requires_response(effective_kind) or _content_asks_question(content)
    builder = build_task_follow_up_trigger if requires_response else build_task_update_trigger
    trigger_requests.append(
        builder(
            task,
            recipient_agent_id=reply_target["agent_id"],
            from_agent=actor.id,
            from_name=actor.name,
            content=content.strip(),
            attention_kind=str(effective_kind or "").strip() or "task_update",
            source_task_event_id=source_task_event_id,
            source_channel="work",
        )
    )


def _task_requires_conversational_follow_up(task: Any | None, *, actor_id: str) -> bool:
    """Return whether the current task should send a natural follow-up reply."""
    if task is None:
        return False
    if task.source_channel not in {"chat", "peer", "meeting", "channel"}:
        return False
    if task.notification_channel_id and task.source_channel == "channel":
        return True
    target = task_assignment_reply_target(task, assignee_id=actor_id)
    return target["kind"] in {"human", "agent"}


def _append_task_follow_up_message(
    *,
    result: dict[str, Any],
    actor: Agent,
    state: AgentState,
    task: Any | None,
    content: str | None,
    attention_kind: str | None = None,
    source_trigger_id: str | None = None,
) -> set[str]:
    """Persist one natural follow-up message for a task lifecycle update."""
    if task is None or not isinstance(content, str) or not content.strip():
        return set()

    if task.notification_channel_id and task.source_channel == "channel":
        message = db.create_channel_message(
            channel_id=task.notification_channel_id,
            author_type="agent",
            author_agent_id=actor.id,
            author_name=actor.name,
            content=content.strip(),
            source_channel="channel",
        )
        result["channel_message"] = {
            "channel_id": task.notification_channel_id,
            "content": message.content,
            "author_type": message.author_type,
            "author_name": actor.name,
            "message_id": message.id,
            "created_at": message.created_at,
        }
        return set()

    target = task_assignment_reply_target(task, assignee_id=actor.id)
    if target["kind"] == "human":
        message = db.create_message(
            from_agent=actor.id,
            to_agent=HUMAN_SENDER_ID,
            content=content.strip(),
            message_type="social",
            location_x=state.x,
            location_y=state.y,
        )
        result["chat_message"] = {
            "agent_id": actor.id,
            "content": message.content,
            "from_type": "agent",
            "from_name": actor.name,
            "message_type": message.message_type,
            "message_id": message.id,
            "created_at": message.created_at,
        }
        return set()

    if target["kind"] == "agent" and target["agent_id"]:
        db.create_notification(
            agent_id=target["agent_id"],
            task_id=task.id,
            kind="task_update",
            content=content.strip(),
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
        persisted = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=actor.id,
            author_name=actor.name,
            event_type="answer" if attention_kind == "completion_report" else "status_update",
            content=content.strip(),
            source_trigger_id=source_trigger_id,
        )
        if attention_kind is None:
            return {target["agent_id"]}
        effective_kind = _effective_attention_kind(attention_kind, content)
        requires_response = _attention_kind_requires_response(effective_kind) or _content_asks_question(content)
        builder = build_task_follow_up_trigger if requires_response else build_task_update_trigger
        result.setdefault("trigger_requests", []).append(
            builder(
                task,
                recipient_agent_id=target["agent_id"],
                from_agent=actor.id,
                from_name=actor.name,
                content=content.strip(),
                attention_kind=str(effective_kind or "").strip() or "task_update",
                source_task_event_id=persisted.id if persisted is not None else None,
                source_channel="work",
            )
        )
        return {target["agent_id"]}

    return set()


def _task_message_event_type(message_kind: str) -> str:
    """Map execution task-message kinds onto durable task-event types."""
    if message_kind == "question":
        return "clarification"
    if message_kind == "review":
        return "status_update"
    if message_kind == "status":
        return "status_update"
    return "comment"


def _task_message_attention_kind(message_kind: str) -> str | None:
    """Return the attention kind for one task-thread execution message."""
    if message_kind == "question":
        return "question"
    if message_kind == "review":
        return "review_request"
    return None


_CHILD_UPDATES_TO_PARENT_EVENT_TYPES = {
    "completion": "status_update",
    "blocker": "status_update",
}

_RESPONSE_REQUIRED_ATTENTION_KINDS = {
    "question",
    "review_request",
    "decision_needed",
    "clarification_requested",
}


def _attention_kind_requires_response(attention_kind: str | None) -> bool:
    """Return whether one task attention kind should require a follow-up response."""
    return str(attention_kind or "").strip().lower() in _RESPONSE_REQUIRED_ATTENTION_KINDS


def _content_asks_question(content: str | None) -> bool:
    """Heuristic: treat content with a question mark as requiring a response."""
    return isinstance(content, str) and "?" in content


def _effective_attention_kind(attention_kind: str | None, content: str | None) -> str | None:
    """Prefer explicit question attention when the message asks one."""
    if attention_kind is None:
        return None
    if _content_asks_question(content):
        return "question"
    return attention_kind
