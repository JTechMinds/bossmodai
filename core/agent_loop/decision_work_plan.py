"""Resolve and materialize delegated work-execution plans."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.decision_contract import ConversationDecision
from core.agent_loop.decision_task_bind import _ambiguous_match_feedback
from core.agent_loop.deliverables import build_work_contract
from core.models import Agent, AgentState
from core.tasking.service import create_or_bind_subtask
from core.world.tilemap import get_room_at


class _WorkPlanMaterializeAborted(Exception):
    """Abort child materialization so the surrounding transaction rolls back."""

    def __init__(self, error_result: dict[str, Any]) -> None:
        self.error_result = error_result
        super().__init__("work plan materialization aborted")

def _build_initial_work_reason(state: AgentState, task_title: str) -> str:
    """Build the first execution prompt for a newly accepted work commitment."""
    room = get_room_at(state.x, state.y)
    if room and room.get("room_type") == "workspace":
        return f'Begin work on "{task_title}".'
    room_name = room["name"] if room else "your current location"
    return (
        f'You accepted work on "{task_title}" while in {room_name}. '
        "If you are not already at a workspace, walk to your desk first. "
        "Then continue the task."
    )

def _build_initial_work_metadata(
    agent: Agent,
    state: AgentState,
    reply: str | None,
    *,
    plan_mode: str = "self",
    delegated_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build work-activity metadata for a newly accepted work commitment.

    Only set a desk preference when the agent is not already in a valid
    workspace. This keeps the planner aligned with the actual work rule and
    avoids wasting a turn on `walkTo desk` while already at the desk/workspace.
    """
    metadata: dict[str, Any] = {
        "acknowledged_by_reply": bool(reply and reply.strip()),
        "work_plan_mode": plan_mode,
    }
    delegated_ids = [task_id for task_id in (delegated_task_ids or []) if isinstance(task_id, str) and task_id.strip()]
    if delegated_ids:
        metadata["delegated_task_ids"] = delegated_ids
    room = get_room_at(state.x, state.y)
    if not room or room.get("room_type") != "workspace":
        metadata["preferred_destination"] = "desk"
        return metadata

    if agent.desk_x is not None and agent.desk_y is not None:
        if (state.x, state.y) != (agent.desk_x, agent.desk_y):
            return metadata

    return metadata

def _should_queue_initial_work_resume(*, task, plan_mode: str) -> bool:
    """Return whether a newly accepted task should immediately receive a work-resume turn."""
    if plan_mode != "delegate":
        return True
    return task.work_contract is not None

def _resolve_work_execution_plan(agent: Agent, decision: ConversationDecision) -> dict[str, Any]:
    """Resolve one accepted work plan into concrete teammate targets before side effects."""
    plan = decision.executionPlan
    if plan is None:
        return {"mode": "self", "delegations": []}

    agents = db.list_agents()
    by_id = {item.id: item for item in agents}
    by_name: dict[str, list[Agent]] = {}
    for item in agents:
        by_name.setdefault(item.name.strip().lower(), []).append(item)

    resolved: list[dict[str, Any]] = []
    for delegation in plan.delegations:
        target = None
        if delegation.agentId and delegation.agentId.strip():
            target = by_id.get(delegation.agentId.strip())
        if target is None and delegation.agentName and delegation.agentName.strip():
            requested_name = delegation.agentName.strip().lower()
            matches = by_name.get(requested_name, [])
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                return {
                    "error_result": {
                        "event": "world_feedback",
                        "detail": (
                            f'More than one teammate is named "{delegation.agentName}". '
                            "Use the exact teammate from the roster or task board."
                        ),
                        "agent_name": agent.name,
                    }
                }
            elif not matches:
                prefix_matches = [
                    item
                    for key, items in by_name.items()
                    if key.startswith(requested_name)
                    for item in items
                ]
                if len(prefix_matches) == 1:
                    target = prefix_matches[0]
                elif len(prefix_matches) > 1:
                    options = ", ".join(sorted({item.name for item in prefix_matches}))
                    return {
                        "error_result": {
                            "event": "world_feedback",
                            "detail": (
                                f'More than one teammate matches "{delegation.agentName}". '
                                f"Be specific. Matching teammates: {options}."
                            ),
                            "agent_name": agent.name,
                        }
                    }
        if target is None:
            requested = delegation.agentName or delegation.agentId or "that teammate"
            available = ", ".join(sorted(item.name for item in agents if item.id != agent.id))
            return {
                "error_result": {
                    "event": "world_feedback",
                    "detail": (
                        f'No teammate named "{requested}" is available for delegated work. '
                        f"Available teammates: {available or 'none'}."
                    ),
                    "agent_name": agent.name,
                }
            }
        if target.id == agent.id:
            return {
                "error_result": {
                    "event": "world_feedback",
                    "detail": "Delegated child tasks must target another teammate, not yourself.",
                    "agent_name": agent.name,
                }
            }
        resolved.append(
            {
                "agent": target,
                "taskTitle": delegation.taskTitle.strip(),
                "taskDescription": (delegation.taskDescription or "").strip() or None,
                "deliverables": delegation.deliverables,
            }
        )
    return {"mode": plan.mode, "delegations": resolved}

def _materialize_work_execution_plan(
    *,
    agent: Agent,
    parent_task,
    trigger: dict[str, Any],
    plan_resolution: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist delegated child tasks from an accepted work plan and queue assignee triggers.

    Child inserts run in one transaction. If a later child hits
    ``clarify_ambiguous_match``, earlier new rows roll back so the failed
    plan does not leave orphan assignments (PR #9 follow-up).
    """
    try:
        with db.transaction():
            delegated_children, pending_triggers = _persist_work_execution_children(
                agent=agent,
                parent_task=parent_task,
                trigger=trigger,
                plan_resolution=plan_resolution,
            )
    except _WorkPlanMaterializeAborted as exc:
        return {"error_result": exc.error_result}

    result["trigger_requests"].extend(pending_triggers)
    return {"children": delegated_children}

def _persist_work_execution_children(
    *,
    agent: Agent,
    parent_task,
    trigger: dict[str, Any],
    plan_resolution: dict[str, Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Insert/bind each planned child. Raises to roll back the whole batch."""
    delegated_children: list[Any] = []
    pending_triggers: list[dict[str, Any]] = []
    for delegation in plan_resolution.get("delegations") or []:
        target = delegation["agent"]
        child_cli_state = db.ensure_agent_cli_state(target.id)
        work_contract = build_work_contract(
            delegation.get("deliverables"),
            agent_storage_key=target.storage_key,
            cwd=child_cli_state.cwd,
        )
        creation = create_or_bind_subtask(
            parent_task=parent_task,
            title=delegation["taskTitle"],
            description=delegation.get("taskDescription"),
            project=parent_task.project,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=agent.id,
            created_by=agent.id,
            work_contract=work_contract,
            source_channel=parent_task.source_channel,
            notification_policy=parent_task.notification_policy,
            notification_channel_id=parent_task.notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=trigger.get("trigger_id"),
        )
        if creation.outcome == "clarify_ambiguous_match" or creation.task is None:
            raise _WorkPlanMaterializeAborted(
                _ambiguous_match_feedback(
                    agent_name=agent.name,
                    title=delegation["taskTitle"],
                    candidates=creation.resolution.candidates,
                    context=(
                        f'Clarify which existing task with {target.name} to use '
                        "instead of delegating a duplicate assignment."
                    ),
                )
            )
        child = creation.task
        delegated_children.append(child)
        if child.status == "pending":
            pending_triggers.append(build_task_assigned_trigger(child))
    return delegated_children, pending_triggers
