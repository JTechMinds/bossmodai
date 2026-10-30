"""Create, bind, or defer the durable work task for a decision."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.decision_contract import ConversationDecision
from core.agent_loop.deliverables import build_work_contract
from core.agent_loop.task_origins import (
    task_notification_channel_id_for_trigger,
    task_notification_policy_for_trigger,
    task_source_channel_for_trigger,
)
from core.agent_loop.task_roles import default_task_owner_id, task_requester_id_for_trigger
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.tasking.dependencies import is_released
from core.tasking.requests import append_task_request, request_messages_for_trigger
from core.tasking.service import append_task_event, create_or_bind_subtask, create_or_bind_task


def _unreleased_task_feedback(task: Task, *, agent_name: str) -> dict[str, Any] | None:
    """Refuse a decision on work its assignee has not been given; ``None`` when ``task`` is released.

    The boss can hold a pending task while its assignee is mid-decision, and
    a bind by title can land on an open held task. Acting on it would break
    the hold: ``held → accepted`` and ``held → declined`` are off the map,
    and a defer's move to ``pending`` would silently release it. The turn
    changes nothing and is told to leave the task.

    Returns:
        ``{"error_result": <world_feedback>}``, the shape the runtime returns
        as the turn result, or ``None`` when the task is the agent's to act on.
    """
    if is_released(task):
        return None
    return {
        "error_result": {
            "event": "world_feedback",
            "detail": f'"{task.title}" is not ready to start yet, so leave it for now.',
            "agent_name": agent_name,
        }
    }


def _ambiguous_match_feedback(
    *,
    agent_name: str,
    title: str,
    candidates,
    context: str,
) -> dict[str, Any]:
    """Return world_feedback asking the agent to clarify an ambiguous workstream match."""
    candidate_ids = ", ".join(item.id for item in candidates) or "none"
    return {
        "event": "world_feedback",
        "detail": (
            f'Multiple open tasks already match "{title}" ({candidate_ids}). {context}'
        ),
        "agent_name": agent_name,
        "expected_action": "clarify",
    }

def _save_turn_request(task: Task, trigger: dict[str, Any]) -> Task:
    """Save the message(s) this turn answered on ``task`` as its request, word for word.

    The agent's ``desc`` is its own brief on top of them
    (``core/tasking/requests.py``). It runs on the task an accept or defer
    lands on, before an accept's plan children are created, so each child's
    ``task_assigned`` wake already carries the request behind its parent;
    and outside any transaction, since the append opens its own.

    Raises:
        ValueError: The task vanished before its request was saved.
    """
    return append_task_request(task.id, request_messages_for_trigger(trigger))


def _task_from_creation(creation, *, agent_name: str, trigger: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a create-or-bind result into a task, honest clarify feedback, or on-hold feedback.

    A bind by title can land on an open held task (``held`` is an open
    status); that is refused. A task created here has no prerequisites, so
    it is never held. The task the agent may act on gets the turn's request
    saved on it (:func:`_save_turn_request`).
    """
    if creation.outcome == "clarify_ambiguous_match" or creation.task is None:
        title = (
            creation.resolution.candidates[0].title
            if creation.resolution.candidates
            else "this workstream"
        )
        return {
            "error_result": _ambiguous_match_feedback(
                agent_name=agent_name,
                title=title,
                candidates=creation.resolution.candidates,
                context="Clarify which existing task to use instead of creating a duplicate.",
            )
        }
    unreleased = _unreleased_task_feedback(creation.task, agent_name=agent_name)
    if unreleased is not None:
        return unreleased
    return {"task": _save_turn_request(creation.task, trigger)}

def _follow_up_parent_task_for_trigger(agent: Agent, trigger: dict[str, Any]):
    """Return the completed task that a revision-style follow-up should attach to."""
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    task = db.get_task(task_id)
    if task is None:
        return None
    if task.assigned_to != agent.id:
        return None
    if task.status != "complete":
        return None
    return task

def _inherited_follow_up_work_contract(parent_task, decision: ConversationDecision):
    """Reuse the completed task's deliverable contract when follow-up work omits a new one."""
    if parent_task is None or parent_task.work_contract is None or decision.deliverables:
        return None
    return parent_task.work_contract.model_dump()

def _persist_work_contract(task, agent: Agent, decision: ConversationDecision):
    """Persist a normalized durable work contract on the task when provided."""
    if not decision.deliverables:
        return task
    cli_state = db.ensure_agent_cli_state(agent.id)
    contract = build_work_contract(
        decision.deliverables,
        agent_storage_key=agent.storage_key,
        cwd=cli_state.cwd,
    )
    if contract is None:
        return task
    updated = db.update_task(task.id, work_contract=contract)
    return updated or task

def _resolve_or_create_work_task(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
):
    """Return the accepted work task, creating it for direct chat requests.

    A task wake binds its own task. An accept that names one of the agent's
    open tasks (``data.task.id``) binds that task instead of creating one;
    see :func:`_bind_named_task`. Anything else creates or binds by title.
    Whichever task that is, work not yet released to the agent (held, or
    blocked on a failed prerequisite) comes back as on-hold feedback
    instead (:func:`_unreleased_task_feedback`).

    A new task's description is the agent's brief (``desc``) alone, or none;
    the requester's own words are saved on the task this creates or binds,
    as its request (:func:`_save_turn_request`). A task wake answers no
    message, so it saves none.
    """
    if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task is None:
            raise ValueError("Assigned task no longer exists")
        return _unreleased_task_feedback(task, agent_name=agent.name) or {"task": task}
    if decision.taskId:
        return _bind_named_task(agent, trigger, decision)

    origin_attachment_ids = trigger.get("attachment_ids")

    created_by = agent.id
    if trigger.get("type") == "human_chat":
        created_by = HUMAN_SENDER_ID
    elif trigger.get("type") == "session_response":
        if trigger.get("author_type") == "human":
            created_by = HUMAN_SENDER_ID
        elif trigger.get("from_agent"):
            created_by = trigger["from_agent"]
    elif trigger.get("type") == "channel_response":
        if trigger.get("author_type") == "human":
            created_by = HUMAN_SENDER_ID
        elif trigger.get("from_agent"):
            created_by = trigger["from_agent"]
    requester_id = task_requester_id_for_trigger(trigger, default_agent_id=agent.id)
    parent_task = _follow_up_parent_task_for_trigger(agent, trigger)
    owner_id = default_task_owner_id(
        assignee_id=agent.id,
        parent_task=parent_task,
    )

    if parent_task is not None:
        return _task_from_creation(
            create_or_bind_subtask(
                parent_task=parent_task,
                title=(decision.taskTitle or "").strip(),
                description=(decision.taskDescription or "").strip() or None,
                project=parent_task.project,
                assigned_to=agent.id,
                requester_id=requester_id,
                owner_id=owner_id,
                created_by=created_by,
                work_contract=_inherited_follow_up_work_contract(parent_task, decision),
                source_channel=task_source_channel_for_trigger(trigger),
                notification_policy=task_notification_policy_for_trigger(trigger),
                notification_channel_id=task_notification_channel_id_for_trigger(trigger),
                audit_author_name=agent.name,
                audit_author_type="agent",
                audit_author_agent_id=agent.id,
                audit_source_trigger_id=trigger.get("trigger_id"),
                origin_attachment_ids=origin_attachment_ids,
            ),
            agent_name=agent.name,
            trigger=trigger,
        )
    return _task_from_creation(
        create_or_bind_task(
            title=(decision.taskTitle or "").strip(),
            description=(decision.taskDescription or "").strip() or None,
            project=None,
            assigned_to=agent.id,
            requester_id=requester_id,
            owner_id=owner_id,
            created_by=created_by,
            parent_task_id=None,
            work_contract=_inherited_follow_up_work_contract(parent_task, decision),
            source_channel=task_source_channel_for_trigger(trigger),
            notification_policy=task_notification_policy_for_trigger(trigger),
            notification_channel_id=task_notification_channel_id_for_trigger(trigger),
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=trigger.get("trigger_id"),
            origin_attachment_ids=origin_attachment_ids,
        ),
        agent_name=agent.name,
        trigger=trigger,
    )

def _bind_named_task(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> dict[str, Any]:
    """Bind the open task the agent named with ``data.task.id``; its description is never rewritten.

    A task's description is written once, by whoever created the task (the
    boss in the Assign form, a teammate's brief, a schedule's instructions,
    or the agent's own ``desc`` when its accept created the task), and
    afterwards only the boss edits it (``PATCH /api/tasks/{id}``). An accept
    by id therefore adds the message this turn answers to the task's request
    (:func:`_save_turn_request`), and a non-empty ``desc`` that differs from
    the description is noted on the task thread as the agent's
    ``Working brief: <desc>`` comment. Why: on 2026-10-30 an accept by id
    overwrote the boss's Assign-form description with the agent's one-line
    ``desc``, written after it had seen only a 120-character board summary
    of that text; the work followed the one-liner and the boss's words were
    lost.

    ``validate_decision_for_trigger`` already checked that the id names an
    open task assigned to this agent. A task the boss held since validation
    is refused first, so nothing on it changes: no comment and no request.
    The accept path then activates the bound task: it resumes paused work,
    or pauses a different current task.

    Args:
        agent: The accepting agent; it authors the working-brief comment.
        trigger: The turn's live trigger. Its message(s) are added to the
            request, and its ``trigger_id`` is the comment's source.
        decision: The accept; ``taskId`` names the task and
            ``taskDescription`` is the agent's ``desc``.

    Returns:
        ``{"task": <the task as stored after its request is saved>}``, or
        ``{"error_result": <world_feedback>}`` for a task not yet released
        to the agent (:func:`_unreleased_task_feedback`).

    Raises:
        ValueError: The task vanished after validation, or before its
            request was saved.
    """
    task_id = str(decision.taskId or "").strip()
    task = db.get_task(task_id)
    if task is None:
        raise ValueError(f"Named task {task_id} no longer exists")
    unreleased = _unreleased_task_feedback(task, agent_name=agent.name)
    if unreleased is not None:
        return unreleased
    brief = (decision.taskDescription or "").strip()
    if brief and brief != (task.description or "").strip():
        append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="comment",
            content=f"Working brief: {brief}",
            source_trigger_id=trigger.get("trigger_id"),
        )
    # Saved after the comment: it re-reads the task, so the row returned
    # carries the activity time the comment stamped.
    return {"task": _save_turn_request(task, trigger)}

def _ensure_deferred_task(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
):
    """Return the durable task that should remain pending after a defer decision.

    Work not yet released to the agent comes back as on-hold feedback
    instead: deferring it would move a held task to ``pending`` and so
    release it (:func:`_unreleased_task_feedback`).

    As on accept, a new task's description is the agent's brief alone, the
    requester's words are saved on it as its request
    (:func:`_save_turn_request`), and it keeps the files attached to the
    message it came from (``origin_attachment_ids``), so the work starts
    later with them.
    """
    if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task is None:
            raise ValueError("Assigned task no longer exists")
        return _unreleased_task_feedback(task, agent_name=agent.name) or {"task": task}

    if trigger.get("type") == "human_chat":
        created_by = HUMAN_SENDER_ID
    elif trigger.get("type") == "channel_response" and trigger.get("author_type") == "human":
        created_by = HUMAN_SENDER_ID
    else:
        created_by = trigger.get("from_agent") or agent.id
    requester_id = task_requester_id_for_trigger(trigger, default_agent_id=agent.id)
    parent_task = _follow_up_parent_task_for_trigger(agent, trigger)
    owner_id = default_task_owner_id(
        assignee_id=agent.id,
        parent_task=parent_task,
    )
    if parent_task is not None:
        return _task_from_creation(
            create_or_bind_subtask(
                parent_task=parent_task,
                title=(decision.taskTitle or "").strip(),
                description=(decision.taskDescription or "").strip() or None,
                project=parent_task.project,
                assigned_to=agent.id,
                requester_id=requester_id,
                owner_id=owner_id,
                created_by=created_by,
                work_contract=_inherited_follow_up_work_contract(parent_task, decision),
                source_channel=task_source_channel_for_trigger(trigger),
                notification_policy=task_notification_policy_for_trigger(trigger),
                notification_channel_id=task_notification_channel_id_for_trigger(trigger),
                audit_author_name=agent.name,
                audit_author_type="agent",
                audit_author_agent_id=agent.id,
                audit_source_trigger_id=trigger.get("trigger_id"),
                origin_attachment_ids=trigger.get("attachment_ids"),
            ),
            agent_name=agent.name,
            trigger=trigger,
        )
    return _task_from_creation(
        create_or_bind_task(
            title=(decision.taskTitle or "").strip(),
            description=(decision.taskDescription or "").strip() or None,
            project=None,
            assigned_to=agent.id,
            requester_id=requester_id,
            owner_id=owner_id,
            created_by=created_by,
            parent_task_id=None,
            work_contract=_inherited_follow_up_work_contract(parent_task, decision),
            source_channel=task_source_channel_for_trigger(trigger),
            notification_policy=task_notification_policy_for_trigger(trigger),
            notification_channel_id=task_notification_channel_id_for_trigger(trigger),
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=trigger.get("trigger_id"),
            origin_attachment_ids=trigger.get("attachment_ids"),
        ),
        agent_name=agent.name,
        trigger=trigger,
    )
