"""Event-driven origin-thread mirrors for task status changes.

When work moves, the originating channel or Focus thread must get Debra's
locked one-liner. Profile activity alone does not count. Heartbeats and
section-by-section writer updates must not post here.
"""

from __future__ import annotations

from typing import Any, Literal

import db
from core.agent_loop.notifications import ChatNotification, persist_channel_notification, persist_chat_notification
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID

OriginThread = Literal["channel", "chat"]

_BLOCKED_CLAIM_LINE = "Blocked — checkable claim missing"


OPERATOR_CANCEL_REASON = "Operator cancelled"


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


def short_reason(text: str | None, *, fallback: str = "") -> str:
    """Collapse a reason to a single short operator phrase."""
    note = " ".join((text or "").strip().split())
    if not note:
        return fallback
    if len(note) > 80:
        return note[:77] + "..."
    return note


def format_done_claim_label(
    *,
    claim: dict[str, Any] | None = None,
    path: str | None = None,
    evidence: str | None = None,
) -> str:
    """Return the Done one-liner tail: claim path or tests evidence."""
    payload = claim if isinstance(claim, dict) else {}
    claim_type = str(payload.get("type") or "").strip().lower()
    claim_path = str(payload.get("path") or path or "").strip()
    claim_evidence = str(payload.get("evidence") or evidence or "").strip()
    if claim_type == "tests":
        return short_reason(claim_evidence, fallback="tests") or "tests"
    if claim_path:
        return claim_path
    if claim_evidence:
        return short_reason(claim_evidence)
    if claim_type:
        return claim_type
    return "done"


def format_origin_status_line(
    *,
    kind: str,
    agent: Agent,
    task: Any,
    reason: str | None = None,
    path: str | None = None,
    target_name: str | None = None,
    claim: dict[str, Any] | None = None,
) -> str:
    """Return Debra's locked operator one-liner for the origin thread."""
    title = str(getattr(task, "title", None) or "the task").strip() or "the task"
    note = short_reason(reason)
    if kind == "created":
        return f"Created: {title}"
    if kind == "accepted":
        return f"Accepted: {title}"
    if kind == "waiting":
        return f"Waiting — {note}" if note else "Waiting"
    if kind == "stalled":
        return f"Stalled — {note}" if note else "Stalled"
    if kind == "progress":
        target = (path or note or "").strip()
        if target.lower().startswith("writing "):
            return target
        if target:
            return f"Writing {target}"
        return "Writing"
    if kind == "declined":
        return f"Declined — {note}" if note else "Declined"
    if kind == "rerouted":
        name = (target_name or "").strip() or "another agent"
        if note and note.lower() in {
            f"delegated to {name}".lower(),
            f"handed off to {name}".lower(),
            f"rerouted to {name}".lower(),
        }:
            note = ""
        if note:
            return f"Rerouted to {name} — {note}"
        return f"Rerouted to {name}"
    if kind == "cancelled":
        return f"Cancelled — {note}" if note else "Cancelled"
    if kind == "blocked_claim":
        return _BLOCKED_CLAIM_LINE
    if kind == "completion":
        label = format_done_claim_label(claim=claim, path=path, evidence=reason)
        return f"Done — {label}"
    if note:
        return note
    return f"Accepted: {title}"


def mirror_origin_status(
    *,
    task: Any,
    agent: Agent,
    kind: str,
    reason: str | None = None,
    path: str | None = None,
    target_name: str | None = None,
    claim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format and persist one locked origin-thread status line."""
    content = format_origin_status_line(
        kind=kind,
        agent=agent,
        task=task,
        reason=reason,
        path=path,
        target_name=target_name,
        claim=claim,
    )
    return persist_origin_status_line(task=task, agent=agent, content=content, kind=kind)


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
        if db.is_channel_archived(channel_id):
            return {}
        # Same-titled parent/child Created lines, and per-task Cancelled lines, must not collapse.
        if kind not in {"created", "cancelled"}:
            recent = db.list_channel_messages(channel_id, limit=8)
            if any(
                item.author_type == "system" and (item.content or "").strip() == text
                for item in recent
            ):
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
        if not notification:
            return {}
        return {"channel_message": notification}

    if kind != "created" and _chat_already_has_line(agent.id, getattr(task, "id", None), text):
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


def attach_operator_status_line(
    result: dict[str, Any],
    *,
    task: Any,
    agent: Agent,
    kind: str,
    reason: str | None = None,
    path: str | None = None,
    target_name: str | None = None,
    claim: dict[str, Any] | None = None,
) -> None:
    """Always persist the locked operator line; do not clobber an agent reply."""
    posted = mirror_origin_status(
        task=task,
        agent=agent,
        kind=kind,
        reason=reason,
        path=path,
        target_name=target_name,
        claim=claim,
    )
    extras = result.setdefault("origin_status_messages", [])
    if posted.get("channel_message"):
        extras.append(posted["channel_message"])
        if not result.get("channel_message"):
            result["channel_message"] = posted["channel_message"]
    if posted.get("chat_message"):
        extras.append(posted["chat_message"])
        if not result.get("chat_message"):
            result["chat_message"] = posted["chat_message"]


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


def _origin_author_agent(task: Any) -> Agent | None:
    """Return the agent used to persist a Created line, if any."""
    for candidate in (getattr(task, "assigned_to", None), getattr(task, "created_by", None)):
        if not candidate or candidate == HUMAN_SENDER_ID:
            continue
        agent = db.get_agent(candidate)
        if agent is not None:
            return agent
    return None


def mirror_task_created(task: Any) -> dict[str, Any]:
    """Post Created: {task} on the origin thread when this thread spawned the work."""
    if task is None or origin_thread_target(task) is None:
        return {}
    agent = _origin_author_agent(task)
    if agent is None:
        return {}
    return mirror_origin_status(task=task, agent=agent, kind="created")


def mirror_task_cancelled_by_operator(task: Any) -> dict[str, Any]:
    """Post Cancelled — Operator cancelled on the origin thread."""
    if task is None or origin_thread_target(task) is None:
        return {}
    agent = _origin_author_agent(task)
    if agent is None:
        return {}
    return mirror_origin_status(
        task=task,
        agent=agent,
        kind="cancelled",
        reason=OPERATOR_CANCEL_REASON,
    )
