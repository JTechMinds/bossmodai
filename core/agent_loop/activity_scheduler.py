"""BossMod AI — Centralized trigger scheduling around runtime activities."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.policies import get_trigger_policy
from core.agent_loop.task_roles import task_assignment_sender
from core.models import Activity, AgentState, Task
from core.tasking.resolution import OPEN_TASK_STATUSES

_INTERRUPT_TRIGGER_TYPES = {
    "human_chat",
    "peer_message",
    "task_follow_up",
    "session_message",
    "session_response",
    "channel_message",
    "channel_response",
    "watchdog_status_ping",
}


def can_dispatch_trigger(
    *,
    trigger_type: str,
    state: AgentState | None,
    active_activity: Activity | None,
) -> bool:
    """Return whether an agent is currently eligible for a trigger.

    Rules are documented on :class:`core.agent_loop.policies.TriggerPolicy`.
    ``task_assigned`` may claim during a live conversation; movement still waits.
    """
    if state is None:
        return False

    policy = get_trigger_policy(trigger_type)
    in_transit = state.status == "in_transit" or (
        active_activity is not None and active_activity.kind == "movement"
    )
    if policy.blocks_on_in_transit and in_transit:
        return False

    if trigger_type == "social":
        if state.status != "idle":
            return False
        return active_activity is None

    if policy.blocks_on_active_activity:
        return active_activity is None

    return True


def prepare_trigger_context(agent_id: str, trigger: dict[str, Any]) -> Activity | None:
    """Materialize any runtime activity needed before the turn starts."""
    active = activity_runtime.get_active_activity(agent_id)
    trigger_type = trigger.get("type")
    if trigger_type == "activity_resumed" and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task and task.assigned_to == agent_id:
            if active and active.kind == "work" and active.task_id == task.id:
                return active
            return activity_runtime.activate_work_activity(
                agent_id,
                task,
                title=task.title,
                detail=task.description,
                task_status="active",
                supersede_note="Paused for another live turn.",
            )
    if trigger_type in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task and task.status == "pending" and task.assigned_to == agent_id:
            if active and not (active.kind == "assignment" and active.task_id == task.id):
                return active
            return activity_runtime.start_assignment_activity(agent_id, task)
    return active


def persist_result_triggers(result: dict[str, Any]) -> list[Any]:
    """Write ``trigger_requests`` onto the durable queue.

    This is the same persist the dispatcher uses after a successful turn, so
    tests and live scenarios can enqueue assign/deliver follow-ups without
    inventing a second queue.
    """
    persisted: list[Any] = []
    for queued in result.get("trigger_requests") or []:
        if not isinstance(queued, dict):
            continue
        agent_id = queued.get("agent_id")
        trigger_type = queued.get("trigger_type")
        source_channel = queued.get("source_channel")
        payload = queued.get("payload")
        if not isinstance(agent_id, str) or not agent_id.strip():
            continue
        if not isinstance(trigger_type, str) or not trigger_type.strip():
            continue
        if not isinstance(source_channel, str) or not source_channel.strip():
            continue
        if not isinstance(payload, dict):
            payload = {}
        persisted.append(
            db.create_agent_trigger(
                agent_id=agent_id,
                trigger_type=trigger_type,
                source_channel=source_channel,
                payload=payload,
                task_id=queued.get("task_id"),
            )
        )
    return persisted


def assignment_wake_trigger(task: Task) -> dict[str, Any] | None:
    """Return a task_assigned spec when an open assigned task should wake the assignee.

    Used for both fresh creates and bind/reuse so a duplicate assign is not silent.
    """
    if not task.assigned_to:
        return None
    if task.status not in OPEN_TASK_STATUSES:
        return None
    return build_task_assigned_trigger(task)


def build_task_assigned_trigger(task: Task) -> dict[str, Any]:
    """Build the durable trigger used to present a pending task assignment."""
    sender = task_assignment_sender(task)

    return {
        "agent_id": task.assigned_to,
        "trigger_type": "task_assigned",
        "source_channel": "work",
        "task_id": task.id,
        "payload": {
            "task_title": task.title,
            "task_description": task.description or "",
            "project": task.project,
            "from_agent": sender["from_agent"],
            "from_name": sender["from_name"],
            "requester_id": sender["requester_id"],
            "requester_name": sender["requester_name"],
            "owner_id": sender["owner_id"],
            "owner_name": sender["owner_name"],
            "notification_channel_id": task.notification_channel_id,
        },
    }


def build_task_follow_up_trigger(
    task: Task,
    *,
    recipient_agent_id: str,
    from_agent: str | None,
    from_name: str,
    content: str,
    attention_kind: str,
    source_message_id: str | None = None,
    source_task_event_id: str | None = None,
    source_channel: str = "work",
) -> dict[str, Any]:
    """Build a task-thread trigger that explicitly requires a response."""
    payload: dict[str, Any] = {
        "task_title": task.title,
        "task_description": task.description or "",
        "task_status": task.status,
        "task_party": "assignee" if task.assigned_to == recipient_agent_id else "stakeholder",
        "attention_kind": attention_kind,
        "from_agent": from_agent,
        "from_name": from_name,
        "content": content,
        "source_message_id": source_message_id,
    }
    if source_task_event_id:
        payload["source_task_event_id"] = source_task_event_id
    return {
        "agent_id": recipient_agent_id,
        "trigger_type": "task_follow_up",
        "source_channel": source_channel,
        "task_id": task.id,
        "payload": payload,
    }


def build_task_update_trigger(
    task: Task,
    *,
    recipient_agent_id: str,
    from_agent: str | None,
    from_name: str,
    content: str,
    attention_kind: str,
    source_message_id: str | None = None,
    source_task_event_id: str | None = None,
    source_channel: str = "work",
) -> dict[str, Any]:
    """Build a task update trigger that does not require a response.

    This is for informational task updates (completion, blocker, status) that should
    wake the recipient to review the task thread without forcing a reply.
    """
    payload: dict[str, Any] = {
        "task_title": task.title,
        "task_description": task.description or "",
        "task_status": task.status,
        "task_party": "assignee" if task.assigned_to == recipient_agent_id else "stakeholder",
        "attention_kind": attention_kind,
        "from_agent": from_agent,
        "from_name": from_name,
        "content": content,
        "source_message_id": source_message_id,
    }
    if source_task_event_id:
        payload["source_task_event_id"] = source_task_event_id
    return {
        "agent_id": recipient_agent_id,
        "trigger_type": "task_update",
        "source_channel": source_channel,
        "task_id": task.id,
        "payload": payload,
    }


def build_activity_resume_trigger(activity: Activity, *, reason: str) -> dict[str, Any]:
    """Build the internal trigger used to continue an existing activity."""
    source_channel = "work" if activity.kind in {"assignment", "work"} else "chat"
    payload = {
        "content": reason,
        "activity_kind": activity.kind,
        "activity_title": activity.title,
    }
    if activity.task_id:
        task = db.get_task(activity.task_id)
        if task:
            payload["task_title"] = task.title
            payload["task_description"] = task.description or ""
    return {
        "agent_id": activity.agent_id,
        "trigger_type": "activity_resumed",
        "source_channel": source_channel,
        "task_id": activity.task_id,
        "payload": payload,
    }


def build_task_resume_trigger(task: Task, *, reason: str) -> dict[str, Any]:
    """Build a resume trigger for an open task when no active work activity is bound."""
    return {
        "agent_id": task.assigned_to,
        "trigger_type": "activity_resumed",
        "source_channel": "work",
        "task_id": task.id,
        "payload": {
            "content": reason,
            "activity_kind": "work",
            "activity_title": task.title,
            "task_title": task.title,
            "task_description": task.description or "",
        },
    }


def plan_post_turn_follow_up(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    initial_activity: Activity | None,
    final_activity: Activity | None,
    result: dict[str, Any],
    action: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return follow-up triggers after a turn completes."""
    planned: list[dict[str, Any]] = list(result.get("trigger_requests", []))
    if action is None:
        return planned

    trigger_type = trigger.get("type")
    action_name = action.get("action")
    if trigger_type in _INTERRUPT_TRIGGER_TYPES and action_name == "message":
        if final_activity and initial_activity and final_activity.id == initial_activity.id and final_activity.kind == "work":
            if not db.has_queued_trigger_matching(agent_id, trigger_types=list(_INTERRUPT_TRIGGER_TYPES)):
                planned.append(build_activity_resume_trigger(
                    final_activity,
                    reason=f'You sent the requested update. Resume work on "{final_activity.title or "your task"}".',
                ))

    return planned


def plan_arrival_follow_up(agent_id: str, resumed_activity: Activity | None, room_name: str) -> list[dict[str, Any]]:
    """Plan the next turn after movement finishes."""
    if resumed_activity is None:
        return []
    if db.has_open_trigger_matching(agent_id, trigger_types=["activity_resumed"], task_id=resumed_activity.task_id):
        return []

    if resumed_activity.kind == "work":
        task = db.get_task(resumed_activity.task_id) if resumed_activity.task_id else None
        title = task.title if task else (resumed_activity.title or "your task")
        reason = f'You arrived at {_arrival_label(agent_id, room_name)}. Continue work on "{title}".'
    elif resumed_activity.kind == "assignment":
        reason = f'You arrived at {room_name}. Review the assignment and choose the next step.'
    elif resumed_activity.kind == "break":
        reason = f'You arrived at {room_name}. Continue the break.'
    elif resumed_activity.kind == "conversation":
        reason = f'You arrived at {room_name}. Continue the conversation.'
    elif resumed_activity.kind == "meeting":
        reason = f'You arrived at {room_name}. Continue the meeting.'
    else:
        reason = f'You arrived at {room_name}. Continue the current activity.'

    return [build_activity_resume_trigger(resumed_activity, reason=reason)]


def _arrival_label(agent_id: str, room_name: str) -> str:
    """Render a more precise arrival label when the agent reached their desk."""
    if room_name != "Main Workspace":
        return room_name

    state = db.get_agent_state(agent_id)
    agent = db.get_agent(agent_id)
    if state is None or agent is None:
        return room_name
    if agent.desk_x is None or agent.desk_y is None:
        return room_name
    if (state.x, state.y) == (agent.desk_x, agent.desk_y):
        return "your desk"
    return room_name
