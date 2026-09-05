"""Task-lifecycle execution handlers (wait, done, block, deleg, drop).

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.actions_shared import (
    _resolve_agent_by_id,
    _resolve_task_lifecycle_target,
    _task_is_human_visible,
)
from core.agent_loop.activity_scheduler import (
    build_task_assigned_trigger,
    build_task_update_trigger,
)
from core.agent_loop.deliverables import missing_deliverables, summarize_deliverable
from core.agent_loop.task_followups import (
    _CHILD_UPDATES_TO_PARENT_EVENT_TYPES,
    _append_task_follow_up_message,
    _append_task_stakeholder_reports,
    _task_requires_conversational_follow_up,
)
from core.agent_loop.task_roles import default_task_owner_id
from core.models import Agent, AgentState
from core.tasking.service import append_task_event, create_or_bind_subtask, list_open_child_tasks
from core.tasking.transitions import transition_task
import db


async def _handle_waiting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pause the current task in a waiting state until another event resumes it."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="waiting")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "wait" action.',
            "agent_name": agent.name,
        }

    paused = activity_runtime.pause_active_work(agent.id, reason or "Waiting on a dependency.", task_status="waiting")
    if paused is None:
        return {"event": "agent_error", "detail": '"wait" requires an active task', "agent_name": agent.name}

    task = db.get_task(task_id)
    result = {
        "event": "status_changed",
        "detail": f'{agent.name} is waiting on "{task.title if task else "the current task"}"' + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
    }
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind=None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Waiting on "{task.title}": {reason}' if task and reason else f'Waiting on "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind=None,
    )
    return result


async def _handle_complete(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as complete."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="complete")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    summary = action.get("summary", "")
    active = activity_runtime.get_active_work_activity(agent.id)
    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if task is not None:
        open_children = [
            child
            for child in list_open_child_tasks(parent_task_id=task.id)
            if child.status not in {"blocked", "stalled"}
        ]
        if open_children:
            child_titles = ", ".join(f'"{child.title}"' for child in open_children[:3])
            if len(open_children) > 3:
                child_titles += ", ..."
            return {
                "event": "world_feedback",
                "detail": (
                    "This coordination task still has open delegated child work. "
                    f"Resolve or replan {child_titles} before completing the parent task."
                ),
                "agent_name": agent.name,
                "task_ids": [child.id for child in open_children],
            }
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "done" action.',
            "agent_name": agent.name,
        }
    pending_deliverables = missing_deliverables(
        agent_id=agent.id,
        agent_storage_key=agent.storage_key,
        task=task,
    )
    if pending_deliverables:
        first = summarize_deliverable(pending_deliverables[0])
        return {
            "event": "world_feedback",
            "detail": f'Required deliverable missing: {first}. Satisfy all declared deliverables before complete.',
            "agent_name": agent.name,
            "missing_deliverables": [item.model_dump() for item in pending_deliverables],
        }

    transition_task(
        task_id,
        "complete",
        reason=summary or "Task completed.",
        actor=agent.name,
        actor_type="agent",
        actor_agent_id=agent.id,
        source_trigger_id=(trigger or {}).get("trigger_id"),
        completion_summary=summary or None,
        status_note=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=summary or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} completed task" + (f" — {summary}" if summary else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "completion",
            "task_title": task.title if task else "task",
            "deliverables": [item.model_dump() for item in (task.work_contract.deliverables if task and task.work_contract else [])],
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    if task is not None:
        completion_event = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="completion",
            content=summary or f'Completed "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
        parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
        if parent is not None:
            completion_detail = None
            if isinstance(follow_up_message, str) and follow_up_message.strip():
                completion_detail = follow_up_message.strip()
            elif summary:
                completion_detail = summary
            parent_note = (
                f'Child task "{task.title}" completed by {agent.name}: {completion_detail}'.strip()
                if completion_detail
                else f'Child task "{task.title}" completed by {agent.name}.'
            )
            parent_event = append_task_event(
                task_id=parent.id,
                author_type="system",
                author_name="BossMod",
                event_type=_CHILD_UPDATES_TO_PARENT_EVENT_TYPES["completion"],
                content=parent_note,
                source_trigger_id=(trigger or {}).get("trigger_id"),
            )
            if parent.assigned_to and parent.assigned_to != agent.id:
                result.setdefault("trigger_requests", []).append(
                    build_task_update_trigger(
                        parent,
                        recipient_agent_id=parent.assigned_to,
                        from_agent=agent.id,
                        from_name=agent.name,
                        content=parent_note,
                        attention_kind="completion_report",
                        source_task_event_id=parent_event.id if parent_event is not None else None,
                        source_channel="work",
                    )
                )
    else:
        completion_event = None
        parent = None
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="completion_report" if parent is None else None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Completed "{task.title}": {summary}' if task and summary else f'Completed "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind="completion_report" if parent is None else None,
        source_task_event_id=completion_event.id if completion_event is not None else None,
    )
    return result


async def _handle_blocked(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as blocked."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="blocked")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "block" action.',
            "agent_name": agent.name,
        }
    transition_task(
        task_id,
        "blocked",
        reason=reason or "Blocked.",
        actor=agent.name,
        actor_type="agent",
        actor_agent_id=agent.id,
        source_trigger_id=(trigger or {}).get("trigger_id"),
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.pause_active_work(agent.id, reason or "Blocked.", task_status="blocked")
    if active is None:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} blocked" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "blocked",
            "task_title": task.title if task else "task",
            "reason": reason,
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    parent = None
    if task is not None:
        blocker_event = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="blocker",
            content=reason or f'Blocked on "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
        if task.parent_task_id:
            parent = db.get_task(task.parent_task_id)
            if parent is not None:
                blocker_detail = None
                if isinstance(follow_up_message, str) and follow_up_message.strip():
                    blocker_detail = follow_up_message.strip()
                elif reason:
                    blocker_detail = reason
                parent_note = (
                    f'Child task "{task.title}" blocked by {agent.name}: {blocker_detail}'.strip()
                    if blocker_detail
                    else f'Child task "{task.title}" blocked by {agent.name}.'
                )
                parent_event = append_task_event(
                    task_id=parent.id,
                    author_type="system",
                    author_name="BossMod",
                    event_type=_CHILD_UPDATES_TO_PARENT_EVENT_TYPES["blocker"],
                    content=parent_note,
                    source_trigger_id=(trigger or {}).get("trigger_id"),
                )
                if parent.assigned_to and parent.assigned_to != agent.id:
                    result.setdefault("trigger_requests", []).append(
                        build_task_update_trigger(
                            parent,
                            recipient_agent_id=parent.assigned_to,
                            from_agent=agent.id,
                            from_name=agent.name,
                            content=parent_note,
                            attention_kind="blocker",
                            source_task_event_id=parent_event.id if parent_event is not None else None,
                            source_channel="work",
                        )
                    )
    else:
        blocker_event = None
        parent = None
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="blocker" if parent is None else None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Blocked on "{task.title}": {reason}' if task and reason else f'Blocked on "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind="blocker" if parent is None else None,
        source_task_event_id=blocker_event.id if blocker_event is not None else None,
    )
    return result


async def _handle_delegated(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delegate current task to another agent."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="delegated")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "status_changed", "detail": "No valid delegate target specified", "agent_name": agent.name}
    original_task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(original_task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "deleg" action.',
            "agent_name": agent.name,
        }

    transition_task(
        task_id,
        "delegated",
        reason=f"Delegated to {target.name}",
        actor=agent.name,
        actor_type="agent",
        actor_agent_id=agent.id,
        source_trigger_id=(trigger or {}).get("trigger_id"),
        status_note=f"Delegated to {target.name}",
        watchdog_pinged_at=None,
    )

    # Create a child task for the target agent (vision doc: delegation
    # creates a formal task record with its own watchdog)
    if original_task:
        child = create_or_bind_subtask(
            parent_task=original_task,
            title=original_task.title,
            description=original_task.description,
            project=original_task.project,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=default_task_owner_id(
                assignee_id=target.id,
                requester_id=agent.id,
                created_by=agent.id,
                parent_task=original_task,
            ),
            created_by=agent.id,
            work_contract=original_task.work_contract,
            source_channel=original_task.source_channel,
            notification_policy=original_task.notification_policy,
            notification_channel_id=original_task.notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        ).task
    else:
        child = None

    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=f"Delegated to {target.name}")
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} delegated task to {target.name}",
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "handoff",
            "task_title": original_task.title if original_task else "task",
            "target_name": target.name,
            "task_id": original_task.id if original_task else None,
            "source_channel": original_task.source_channel if original_task else "chat",
            "channel_id": original_task.notification_channel_id if original_task else None,
            "policy": original_task.notification_policy if original_task else "completion_blocked",
            "human_visible": _task_is_human_visible(original_task),
        },
    }
    if child and child.status == "pending":
        result["trigger_requests"] = [build_task_assigned_trigger(child)]
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=original_task,
        content=follow_up_message,
        attention_kind="handoff",
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=original_task,
        content=(
            f'Delegated "{original_task.title}" to {target.name}.'
            if original_task
            else ""
        ),
        skip_recipient_ids=skipped,
    )
    return result


async def _handle_abandoned(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Abandon current task."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="abandoned")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "drop" action.',
            "agent_name": agent.name,
        }
    transition_task(
        task_id,
        "abandoned",
        reason=reason or "Task abandoned.",
        actor=agent.name,
        actor_type="agent",
        actor_agent_id=agent.id,
        source_trigger_id=(trigger or {}).get("trigger_id"),
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=reason or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} abandoned task" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "abandoned",
            "task_title": task.title if task else "task",
            "reason": reason,
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="abandoned",
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Abandoned "{task.title}": {reason}' if task and reason else f'Abandoned "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
    )
    return result
