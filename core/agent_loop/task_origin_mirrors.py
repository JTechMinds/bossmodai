"""Event-driven origin-thread mirrors for task status changes.

When work moves, the originating channel or Focus thread must get Debra's
locked one-liner. Profile activity alone does not count. Heartbeats and
section-by-section writer updates must not post here.
"""

from __future__ import annotations

from typing import Any, Literal

import db
from core.agent_loop.notifications import ChatNotification, persist_channel_notification, persist_chat_notification
from core.agent_loop.task_origins import task_origin_channel_id
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID

OriginThread = Literal["channel", "chat"]

_BLOCKED_CLAIM_LINE = "Blocked — checkable claim missing"
_BLOCKED_HANDOFF_LINE = "Blocked — handoff needs a shared path"
_BLOCKED_NO_TASK_LINE = "Blocked — wait needs an active task"
_BLOCKED_NO_PROGRESS_LINE = "Blocked — no progress"
_BLOCKED_HOST_DENY_LINE = "Blocked — host deny"
_BLOCKED_SHELL_EXECUTOR_LINE = "Blocked — Shell Executor off — needs enable"


OPERATOR_CANCEL_REASON = "Operator cancelled"


def origin_thread_target(task: Any | None) -> OriginThread | None:
    """Return the operator-visible origin for one task, if any."""
    if task is None:
        return None
    if task_origin_channel_id(task):
        return "channel"
    source_channel = getattr(task, "source_channel", None)
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


def openable_done_claim_path(
    *,
    claim: dict[str, Any] | None = None,
    path: str | None = None,
) -> str | None:
    """Return a Done claim path only when peers can open it.

    Tests/proof tails are not paths. A path must be absolute so the Done doc
    link can use the same file-open path as other deliverables.
    """
    payload = claim if isinstance(claim, dict) else {}
    claim_type = str(payload.get("type") or "").strip().lower()
    if claim_type in {"tests", "proof"}:
        return None
    claim_path = str(payload.get("path") or path or "").strip()
    if claim_path.startswith("/"):
        return claim_path
    return None


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


def named_origin_line(agent: Agent, line: str) -> str:
    """Prefix Debra's locked origin one-liner with the agent's name."""
    text = (line or "").strip()
    name = str(getattr(agent, "name", None) or "").strip() or "Agent"
    prefix = f"{name} "
    if not text:
        return name
    if text.startswith(prefix):
        return text
    return f"{prefix}{text}"


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
        line = f"Created: {title}"
    elif kind == "accepted":
        line = f"Accepted: {title}"
    elif kind == "waiting":
        line = f"Waiting — {note}" if note else "Waiting"
    elif kind == "stalled":
        line = f"Stalled — {note}" if note else "Stalled"
    elif kind == "progress":
        target = (path or note or "").strip()
        if target.lower().startswith("writing "):
            line = f"Writing {target[8:].lstrip()}"
        elif target:
            line = f"Writing {target}"
        else:
            line = "Writing"
    elif kind == "declined":
        line = f"Declined — {note}" if note else "Declined"
    elif kind == "rerouted":
        name = (target_name or "").strip() or "another agent"
        if note and note.lower() in {
            f"delegated to {name}".lower(),
            f"handed off to {name}".lower(),
            f"rerouted to {name}".lower(),
        }:
            note = ""
        line = f"Rerouted to {name} — {note}" if note else f"Rerouted to {name}"
    elif kind == "cancelled":
        line = f"Cancelled — {note}" if note else "Cancelled"
    elif kind == "blocked_claim":
        line = _BLOCKED_CLAIM_LINE
    elif kind == "blocked_peer_handoff":
        line = _BLOCKED_HANDOFF_LINE
    elif kind == "blocked_no_task":
        line = _BLOCKED_NO_TASK_LINE
    elif kind == "blocked_no_progress":
        tag = (target_name or "").strip()
        if note and note.startswith(_BLOCKED_NO_PROGRESS_LINE):
            line = note
        elif tag:
            line = f"{_BLOCKED_NO_PROGRESS_LINE}. {tag}"
        else:
            line = _BLOCKED_NO_PROGRESS_LINE
    elif kind == "blocked_host_deny":
        tag = (target_name or "").strip()
        if note and note.startswith(_BLOCKED_HOST_DENY_LINE):
            line = note
        elif tag:
            line = f"{_BLOCKED_HOST_DENY_LINE}. {tag}"
        else:
            line = _BLOCKED_HOST_DENY_LINE
    elif kind == "blocked_shell_executor":
        tag = (target_name or "").strip()
        if note and note.startswith(_BLOCKED_SHELL_EXECUTOR_LINE):
            line = note
        elif tag:
            line = f"{_BLOCKED_SHELL_EXECUTOR_LINE}. {tag}"
        else:
            line = _BLOCKED_SHELL_EXECUTOR_LINE
    elif kind == "completion":
        label = format_done_claim_label(claim=claim, path=path, evidence=reason)
        line = f"Done — {label}"
    elif note:
        line = note
    else:
        line = f"Accepted: {title}"
    return named_origin_line(agent, line)


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
    desk_path = openable_done_claim_path(claim=claim, path=path) if kind == "completion" else None
    return persist_origin_status_line(
        task=task,
        agent=agent,
        content=content,
        kind=kind,
        desk_path=desk_path,
    )


def persist_unbound_status_line(
    *,
    agent: Agent,
    content: str,
    kind: str,
    channel_id: str | None = None,
) -> dict[str, Any]:
    """Persist a locked one-liner when no task is bound. Dedupes the same line."""
    text = (content or "").strip()
    if not text:
        return {}
    _ = kind
    scoped = (channel_id or "").strip() or None
    if scoped:
        if db.is_channel_archived(scoped):
            return {}
        recent = db.list_channel_messages(scoped, limit=8)
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
                policy="completion_blocked",
                prompt_visibility=False,
                channel_id=scoped,
            ),
        )
        if not notification:
            return {}
        return {"channel_message": notification}
    if _chat_already_has_line(agent.id, None, text):
        return {}
    chat_message = persist_chat_notification(
        agent,
        ChatNotification(
            kind="task_update",
            content=text,
            source_channel="chat",
            policy="completion_blocked",
            prompt_visibility=False,
        ),
    )
    return {"chat_message": chat_message}


def persist_origin_status_line(
    *,
    task: Any,
    agent: Agent,
    content: str,
    kind: str,
    desk_path: str | None = None,
) -> dict[str, Any]:
    """Persist one system status line on the origin thread. No peer wake."""
    text = (content or "").strip()
    target = origin_thread_target(task)
    if not text or target is None:
        return {}
    notice_kind = "completion" if kind == "completion" else "task_update"
    open_path = (desk_path or "").strip() or None
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
                kind=notice_kind,
                content=text,
                source_channel="channel",
                policy=str(getattr(task, "notification_policy", None) or "completion_blocked"),
                prompt_visibility=False,
                task_id=getattr(task, "id", None),
                desk_path=open_path,
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
            kind=notice_kind,
            content=text,
            source_channel=str(getattr(task, "source_channel", None) or "chat"),
            policy=str(getattr(task, "notification_policy", None) or "completion_blocked"),
            prompt_visibility=False,
            task_id=getattr(task, "id", None),
            desk_path=open_path,
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
    """Post {Name} Created: {task} on the origin thread when this thread spawned the work."""
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
