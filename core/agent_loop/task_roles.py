"""BossMod AI — Durable task participant and reporting helpers."""

from __future__ import annotations

from typing import Any

import db
from core.models import Task
from core.models.message import HUMAN_SENDER_ID


def task_requester_id_for_trigger(
    trigger: dict[str, Any],
    *,
    default_agent_id: str | None = None,
) -> str | None:
    """Resolve the durable requester for a task created from one trigger."""
    trigger_type = trigger.get("type")
    if trigger_type == "human_chat":
        return HUMAN_SENDER_ID
    if trigger_type in {"session_response", "channel_response"} and trigger.get("author_type") == "human":
        return HUMAN_SENDER_ID

    from_agent = _normalize_actor_id(trigger.get("from_agent"))
    if from_agent:
        return from_agent
    return _normalize_actor_id(default_agent_id)


def default_task_owner_id(
    *,
    assignee_id: str | None,
    requester_id: str | None,
    created_by: str | None,
    parent_task: Task | None = None,
) -> str | None:
    """Resolve the accountable owner for a task."""
    inherited_owner = _normalize_agent_id(parent_task.owner_id) if parent_task else None
    if inherited_owner:
        return inherited_owner

    for candidate in (assignee_id, requester_id, created_by):
        owner_id = _normalize_agent_id(candidate)
        if owner_id:
            return owner_id
    return None


def task_assignment_sender(task: Task) -> dict[str, str | None]:
    """Return the sender/owner labels used when presenting an assignment."""
    requester_id = _normalize_actor_id(task.requester_id) or _normalize_actor_id(task.created_by)
    owner_id = _normalize_agent_id(task.owner_id)

    requester_name = _display_name_for_actor(requester_id)
    owner_name = _display_name_for_actor(owner_id)

    from_agent = requester_id if requester_id and requester_id != HUMAN_SENDER_ID else None
    from_name = requester_name
    if from_name is None and owner_name and owner_id != task.assigned_to:
        from_agent = owner_id
        from_name = owner_name
    if from_name is None:
        from_name = "Task Queue"

    return {
        "from_agent": from_agent,
        "from_name": from_name,
        "requester_id": requester_id,
        "requester_name": requester_name,
        "owner_id": owner_id,
        "owner_name": owner_name,
    }


def task_assignment_reply_target(task: Task, *, assignee_id: str | None = None) -> dict[str, str | None]:
    """Resolve where assignment replies should go."""
    for candidate in (task.requester_id, task.owner_id, task.created_by):
        actor_id = _normalize_actor_id(candidate)
        if not actor_id or actor_id == assignee_id:
            continue
        if actor_id == HUMAN_SENDER_ID:
            return {"kind": "human", "agent_id": None}
        return {"kind": "agent", "agent_id": actor_id}
    return {"kind": "none", "agent_id": None}


def task_report_recipient_ids(task: Task, *, actor_id: str | None = None) -> list[str]:
    """Return the deduplicated agent stakeholders who should receive task updates."""
    recipients: list[str] = []
    for candidate in (task.requester_id, task.owner_id):
        agent_id = _normalize_agent_id(candidate)
        if not agent_id or agent_id == actor_id:
            continue
        if agent_id in recipients:
            continue
        recipients.append(agent_id)
    return recipients


def _normalize_actor_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_agent_id(value: Any) -> str | None:
    actor_id = _normalize_actor_id(value)
    if actor_id in (None, HUMAN_SENDER_ID):
        return None
    return actor_id


def _display_name_for_actor(actor_id: str | None) -> str | None:
    if actor_id is None:
        return None
    if actor_id == HUMAN_SENDER_ID:
        return "Human Operator"
    agent = db.get_agent(actor_id)
    if agent is None:
        return None
    return agent.name
