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
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_subtask, create_or_bind_task


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

def _task_from_creation(creation, *, agent_name: str) -> dict[str, Any]:
    """Unwrap a create-or-bind result into a task or honest clarify feedback."""
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
    return {"task": creation.task}

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
    """Return the accepted work task, creating it for direct chat requests."""
    if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task is None:
            raise ValueError("Assigned task no longer exists")
        return {"task": task}

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
        requester_id=requester_id,
        created_by=created_by,
        parent_task=parent_task,
    )

    if parent_task is not None:
        return _task_from_creation(
            create_or_bind_subtask(
                parent_task=parent_task,
                title=(decision.taskTitle or "").strip(),
                description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
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
            ),
            agent_name=agent.name,
        )
    return _task_from_creation(
        create_or_bind_task(
            title=(decision.taskTitle or "").strip(),
            description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
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
        ),
        agent_name=agent.name,
    )

def _ensure_deferred_task(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
):
    """Return the durable task that should remain pending after a defer decision."""
    if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
        task = db.get_task(trigger["task_id"])
        if task is None:
            raise ValueError("Assigned task no longer exists")
        return {"task": task}

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
        requester_id=requester_id,
        created_by=created_by,
        parent_task=parent_task,
    )
    if parent_task is not None:
        return _task_from_creation(
            create_or_bind_subtask(
                parent_task=parent_task,
                title=(decision.taskTitle or "").strip(),
                description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
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
            ),
            agent_name=agent.name,
        )
    return _task_from_creation(
        create_or_bind_task(
            title=(decision.taskTitle or "").strip(),
            description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
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
        ),
        agent_name=agent.name,
    )
