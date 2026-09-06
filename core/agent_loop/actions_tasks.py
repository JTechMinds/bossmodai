"""Task-thread execution handlers (taskMessage, delegateTask).

Mechanical extract from actions.py (HA-STRUCT-P1-02).
Lifecycle wait/done/block/deleg/drop live in actions_lifecycle.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.actions_shared import (
    _count_action_tokens,
    _normalize_delegate_work_contract,
    _resolve_agent_by_id,
    _task_is_human_visible,
)
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.task_followups import (
    _append_task_stakeholder_reports,
    _task_message_attention_kind,
    _task_message_event_type,
)
from core.agent_loop.task_origins import (
    task_notification_channel_id_for_trigger,
    task_notification_policy_for_trigger,
    task_source_channel_for_trigger,
)
from core.agent_loop.role_contracts import evaluate_specialty_assignment
from core.agent_loop.task_roles import (
    default_task_owner_id,
    task_has_participant,
    task_thread_target,
)
from core.bm_cli.host_roots import PathOutsideRootsError
from core.models.message import HUMAN_SENDER_ID
from core.models import Agent, AgentState
from core.tasking.service import (
    append_task_event,
    create_or_bind_subtask,
    create_or_bind_task,
)
import db


async def _handle_task_message(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a task-thread event and only wake the other side when needed."""
    task_id = str(action.get("taskId") or "").strip()
    content = str(action.get("content") or "").strip()
    message_kind = str(action.get("messageKind") or "note").strip().lower()
    task = db.get_task(task_id)
    if task is None:
        return {"event": "world_feedback", "detail": f'Task "{task_id}" no longer exists.', "agent_name": agent.name}
    if not task_has_participant(task, agent_id=agent.id):
        return {
            "event": "world_feedback",
            "detail": "You can only write on task threads for tasks you participate in.",
            "agent_name": agent.name,
        }

    persisted = append_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type=_task_message_event_type(message_kind),
        content=content,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )

    target = task_thread_target(task, actor_id=agent.id)
    if target["kind"] == "human":
        message = db.create_message(
            from_agent=agent.id,
            to_agent=HUMAN_SENDER_ID,
            content=content,
            message_type="social",
            location_x=state.x,
            location_y=state.y,
            token_count=_count_action_tokens(agent, action, content),
        )
        return {
            "event": "message_sent",
            "detail": f"{agent.name} updated the human on task \"{task.title}\"",
            "agent_name": agent.name,
            "chat_message": {
                "agent_id": agent.id,
                "content": message.content,
                "from_type": "agent",
                "from_name": agent.name,
                "message_type": message.message_type,
                "message_id": message.id,
                "created_at": message.created_at,
            },
        }

    target_agent_id = target.get("agent_id")
    if not isinstance(target_agent_id, str) or not target_agent_id.strip():
        return {
            "event": "world_feedback",
            "detail": "No valid task-thread recipient is available for that task update.",
            "agent_name": agent.name,
        }
    target_agent = db.get_agent(target_agent_id)
    if target_agent is None:
        return {
            "event": "world_feedback",
            "detail": "The task-thread recipient no longer exists.",
            "agent_name": agent.name,
        }

    db.create_notification(
        agent_id=target_agent_id,
        task_id=task.id,
        kind="task_update",
        content=content,
        source_channel="task",
        policy="none",
        chat_visible=False,
        prompt_visibility=False,
    )
    attention_kind = _task_message_attention_kind(message_kind)
    if attention_kind is None:
        return {
            "event": "message_sent",
            "detail": f'{agent.name} added a passive {message_kind} update to "{task.title}" for {target_agent.name}',
            "agent_name": agent.name,
        }

    return {
        "event": "message_sent",
        "detail": f'{agent.name} requested a task-thread response from {target_agent.name} on "{task.title}"',
        "agent_name": agent.name,
        "trigger_requests": [
            build_task_follow_up_trigger(
                task,
                recipient_agent_id=target_agent_id,
                from_agent=agent.id,
                from_name=agent.name,
                content=content,
                attention_kind=attention_kind,
                source_task_event_id=persisted.id if persisted is not None else None,
                source_channel="work",
            )
        ],
    }


async def _handle_delegate_task(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an explicit task assignment for another agent."""
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "agent_error", "detail": "No valid delegate target specified", "agent_name": agent.name}
    if target.id == agent.id:
        return {"event": "agent_error", "detail": "Cannot delegate a task to yourself", "agent_name": agent.name}

    task_title = str(action.get("taskTitle") or "").strip()
    task_description = str(action.get("taskDescription") or "").strip()
    evaluation = evaluate_specialty_assignment(
        assignee=target,
        title=task_title,
        description=task_description,
        teammates=db.list_agents(),
        confirm=bool(action.get("confirmSpecialtyMismatch")),
    )
    if evaluation.deny:
        return {
            "event": "world_feedback",
            "detail": evaluation.warning,
            "agent_name": agent.name,
            "specialty_warning": evaluation.warning,
            "suggested_assignees": [
                {"id": item.id, "name": item.name, "role": item.role}
                for item in evaluation.suggested
            ],
            "expected_action": "delegateTask",
        }

    parent_task_id = activity_runtime.get_active_task_id(agent.id)
    parent_task = db.get_task(parent_task_id) if parent_task_id else None
    if parent_task is not None:
        owner_id = str(parent_task.owner_id or "").strip()
        if owner_id and owner_id != agent.id:
            owner = db.get_agent(owner_id)
            owner_name = owner.name if owner is not None else owner_id
            return {
                "event": "world_feedback",
                "detail": (
                    "Only the task owner can delegate new child tasks under an existing delegated workstream. "
                    f'This task is owned by {owner_name}. Use "taskmsg" (kind=question/review) on this task thread '
                    "to ask for clarification or request a new delegation."
                ),
                "agent_name": agent.name,
                "task_id": parent_task.id,
                "expected_actions": ["taskMessage"],
            }

    project = action.get("project")
    project_name = str(project).strip() if isinstance(project, str) and project.strip() else (parent_task.project if parent_task else None)
    try:
        work_contract = _normalize_delegate_work_contract(agent=agent, action=action)
    except PathOutsideRootsError as exc:
        return {
            "event": "world_feedback",
            "detail": str(exc),
            "agent_name": agent.name,
        }
    source_channel = task_source_channel_for_trigger(trigger or {}) or (parent_task.source_channel if parent_task else None)
    notification_policy = task_notification_policy_for_trigger(trigger or {}) or (parent_task.notification_policy if parent_task else None)
    notification_channel_id = (
        task_notification_channel_id_for_trigger(trigger or {}) or (parent_task.notification_channel_id if parent_task else None)
    )
    owner_id = default_task_owner_id(
        assignee_id=target.id,
        requester_id=agent.id,
        created_by=agent.id,
        parent_task=parent_task,
    )

    if parent_task is not None:
        creation = create_or_bind_subtask(
            parent_task=parent_task,
            title=task_title,
            description=task_description,
            project=project_name,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=owner_id,
            created_by=agent.id,
            work_contract=work_contract,
            source_channel=source_channel,
            notification_policy=notification_policy,
            notification_channel_id=notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    else:
        creation = create_or_bind_task(
            title=task_title,
            description=task_description,
            project=project_name,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=owner_id,
            created_by=agent.id,
            parent_task_id=None,
            work_contract=work_contract,
            source_channel=source_channel,
            notification_policy=notification_policy,
            notification_channel_id=notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    task = creation.task

    if creation.outcome == "clarify_ambiguous_match" or task is None:
        candidate_ids = ", ".join(item.id for item in creation.resolution.candidates) or "none"
        return {
            "event": "world_feedback",
            "detail": (
                f'Multiple open tasks with {target.name} already match "{task_title}" ({candidate_ids}). '
                "Clarify which existing task to use instead of delegating a duplicate assignment."
            ),
            "agent_name": agent.name,
            "expected_action": "taskMessage",
        }

    if creation.outcome != "create_new_task":
        return {
            "event": "world_feedback",
            "detail": (
                f'There is already an open task thread with {target.name} on "{task.title}" ({task.id}). '
                'Use "taskmsg" with that task id instead of delegating a duplicate assignment.'
            ),
            "agent_name": agent.name,
            "task_id": task.id,
            "expected_action": "taskMessage",
        }

    result = {
        "event": "status_changed",
        "detail": f'{agent.name} assigned "{task.title}" to {target.name}',
        "agent_name": agent.name,
        "trigger_requests": [build_task_assigned_trigger(task)] if task.status == "pending" else [],
        "chat_notification": {
            "kind": "handoff",
            "task_title": task.title,
            "target_name": target.name,
            "task_id": parent_task.id if parent_task else task.id,
            "source_channel": task.source_channel or "peer",
            "channel_id": task.notification_channel_id,
            "policy": task.notification_policy or "none",
            "human_visible": _task_is_human_visible(task),
        },
    }
    if evaluation.warning:
        result["specialty_warning"] = evaluation.warning
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=f'Assigned subtask "{task.title}" to {target.name}.',
    )
    append_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type="assignment",
        content=f'Assigned "{task.title}" to {target.name}.',
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    return result
