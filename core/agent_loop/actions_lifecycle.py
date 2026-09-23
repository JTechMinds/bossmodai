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
from core.agent_loop.soft_blocks import waiting_without_task_result
from core.agent_loop.activity_scheduler import (
    build_task_assigned_trigger,
    build_task_update_trigger,
)
from core.agent_loop.role_contracts import evaluate_specialty_assignment, resolve_done_claim
from core.agent_loop.task_followups import (
    _CHILD_UPDATES_TO_PARENT_EVENT_TYPES,
    _append_task_follow_up_message,
    _append_task_stakeholder_reports,
    _task_requires_conversational_follow_up,
)
from core.agent_loop.task_origin_mirrors import attach_operator_status_line
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
        return waiting_without_task_result(agent, trigger)
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    from core.bm_cli.host_path_consent import is_verbal_host_access_ask, verbal_host_access_steer

    if is_verbal_host_access_ask(reason) or is_verbal_host_access_ask(follow_up_message):
        return verbal_host_access_steer(agent)
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
        return waiting_without_task_result(agent, trigger)

    task = db.get_task(task_id)
    result = {
        "event": "status_changed",
        "detail": f'{agent.name} is waiting on "{task.title if task else "the current task"}"' + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "waiting",
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
    if task is not None:
        attach_operator_status_line(result, task=task, agent=agent, kind="waiting", reason=reason)
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
    done_claim, claim_error = resolve_done_claim(agent=agent, task=task, action=action)
    if claim_error:
        if task is not None:
            origin_kind = str(claim_error.get("origin_status_kind") or "blocked_claim")
            attach_operator_status_line(
                claim_error,
                task=task,
                agent=agent,
                kind=origin_kind,
            )
            append_task_event(
                task_id=task.id,
                author_type="system",
                author_name="BossMod",
                event_type="blocker",
                content=(
                    "Blocked — handoff needs a shared path"
                    if origin_kind == "blocked_peer_handoff"
                    else "Blocked — checkable claim missing"
                ),
                source_trigger_id=(trigger or {}).get("trigger_id"),
            )
        return claim_error

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
    task = db.get_task(task_id)
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
    if done_claim is not None:
        result["done_claim"] = done_claim.as_dict()
        result["chat_notification"]["done_claim"] = done_claim.as_dict()
    if task is not None:
        claim_note = ""
        if done_claim is not None:
            claim_bits = [done_claim.type]
            if done_claim.path:
                claim_bits.append(done_claim.path)
            if done_claim.evidence:
                claim_bits.append(done_claim.evidence)
            claim_note = f" Claim: {' — '.join(claim_bits)}."
        completion_event = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="completion",
            content=(summary or f'Completed "{task.title}".') + claim_note,
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
    handoff = _channel_handoff_kwargs(task, action, author_id=agent.id)
    next_cards = _named_next_work_cards(task, action, author_id=agent.id)
    if next_cards:
        handoff["work_bind_ids"] = [str(card.assigned_to) for card in next_cards if card.assigned_to]
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="completion_report" if parent is None else None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
        **handoff,
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
    if task is not None:
        attach_operator_status_line(
            result,
            task=task,
            agent=agent,
            kind="completion",
            reason=summary or None,
            claim=done_claim.as_dict() if done_claim is not None else None,
        )
        _open_origin_handoff_if_quiet(
            result,
            agent=agent,
            task=task,
            action=action,
            work_bind_ids=handoff.get("work_bind_ids"),
        )
        _queue_named_next_work(result, next_cards)
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
    from core.bm_cli.host_path_consent import is_verbal_host_access_ask, verbal_host_access_steer
    from core.bm_cli.nest_git_consent import named_nest_git_block_reason
    from core.bm_cli.shell_executor_consent import named_shell_executor_block_reason

    named_nest_why = named_nest_git_block_reason(agent, reason, task_id=task_id)
    named_shell_why = named_shell_executor_block_reason(agent, reason, task_id=task_id)
    if named_nest_why:
        reason = named_nest_why
    elif named_shell_why:
        reason = named_shell_why
    if is_verbal_host_access_ask(reason) or is_verbal_host_access_ask(follow_up_message):
        return verbal_host_access_steer(agent)
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
    if task is not None:
        # Profile shows blocked; origin thread uses Waiting so claim-Blocked stays exclusive.
        # Shell Executor deny is a named gate: origin must say Blocked — {why}.
        if named_nest_why:
            from core.agent_loop.blocked_origin import NEST_GIT_BLOCK_KIND

            attach_operator_status_line(
                result,
                task=task,
                agent=agent,
                kind=NEST_GIT_BLOCK_KIND,
                reason=reason,
            )
        elif named_shell_why:
            from core.agent_loop.blocked_origin import SHELL_EXECUTOR_BLOCK_KIND

            attach_operator_status_line(
                result,
                task=task,
                agent=agent,
                kind=SHELL_EXECUTOR_BLOCK_KIND,
                reason=reason,
            )
        else:
            attach_operator_status_line(result, task=task, agent=agent, kind="waiting", reason=reason)
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
    from core.floors import CROSS_FLOOR_DENY, peers_share_floor

    if not peers_share_floor(agent.id, target.id):
        return {"event": "world_feedback", "detail": CROSS_FLOOR_DENY, "agent_name": agent.name}
    original_task = db.get_task(task_id)
    if original_task is not None:
        evaluation = evaluate_specialty_assignment(
            assignee=target,
            title=original_task.title,
            description=original_task.description,
            teammates=[
                item for item in db.list_agents()
                if peers_share_floor(agent.id, item.id)
            ],
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
                "expected_action": "delegated",
            }
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
            "reason": (follow_up_message or "").strip(),
            "task_id": original_task.id if original_task else None,
            "source_channel": original_task.source_channel if original_task else "chat",
            "channel_id": original_task.notification_channel_id if original_task else None,
            "policy": original_task.notification_policy if original_task else "completion_blocked",
            "human_visible": _task_is_human_visible(original_task),
        },
    }
    if child and child.status == "pending":
        result["trigger_requests"] = [build_task_assigned_trigger(child)]
    handoff = _channel_handoff_kwargs(
        original_task,
        action,
        author_id=agent.id,
        delegate_id=target.id,
    )
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=original_task,
        content=follow_up_message,
        attention_kind="handoff",
        source_trigger_id=(trigger or {}).get("trigger_id"),
        **handoff,
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
    if original_task is not None:
        attach_operator_status_line(
            result,
            task=original_task,
            agent=agent,
            kind="rerouted",
            reason=(follow_up_message or "").strip() or None,
            target_name=target.name,
        )
        _open_origin_handoff_if_quiet(
            result,
            agent=agent,
            task=original_task,
            action=action,
            delegate_id=target.id,
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
    if task is not None:
        attach_operator_status_line(result, task=task, agent=agent, kind="cancelled", reason=reason)
    return result


def _named_next_work_cards(task: Any, action: dict[str, Any], *, author_id: str) -> list[Any]:
    """Pending cards the Board or structured next_owners name after Done.

    Soft-blocked and already-bound cards are not a new Work wake. A name
    with no pending card does not invent one.
    """
    if task is None or getattr(task, "source_channel", None) != "channel":
        return []
    channel_id = str(getattr(task, "notification_channel_id", None) or "").strip()
    if not channel_id:
        return []
    from core.tasking.board import next_board_task, pending_channel_card_for_owner

    author = (author_id or "").strip()
    owners: list[str] = []
    for agent_id in _handoff_pin_ids(action):
        if agent_id and agent_id != author and agent_id not in owners:
            owners.append(agent_id)
    board = next_board_task(task, author_id=author)
    board_owner = str(getattr(board, "assigned_to", None) or "").strip() if board is not None else ""
    if (
        board is not None
        and getattr(board, "status", None) == "pending"
        and board_owner
        and board_owner != author
        and board_owner not in owners
    ):
        owners.append(board_owner)
    cards: list[Any] = []
    seen: set[str] = set()
    exclude = {str(getattr(task, "id", "") or "")}
    from core.floors import on_floor, channel_floor_id as _channel_floor

    thread_floor = _channel_floor(channel_id)
    for owner in owners:
        if not thread_floor or not on_floor(owner, thread_floor):
            continue
        card = None
        if board is not None and board_owner == owner and getattr(board, "status", None) == "pending":
            card = board
        if card is None:
            card = pending_channel_card_for_owner(
                channel_id,
                owner,
                exclude_task_ids=exclude,
            )
        if card is None or getattr(card, "status", None) != "pending":
            continue
        if str(card.assigned_to or "").strip() != owner or card.id in seen:
            continue
        seen.add(card.id)
        cards.append(card)
    return cards


def _queue_named_next_work(result: dict[str, Any], cards: list[Any]) -> None:
    """Append one task_assigned wake per named pending card. Does not clear Soft-block."""
    if not cards:
        return
    queued = {
        str(item.get("task_id") or "")
        for item in (result.get("trigger_requests") or [])
        if isinstance(item, dict) and item.get("trigger_type") == "task_assigned"
    }
    requests = result.setdefault("trigger_requests", [])
    for card in cards:
        if card.id in queued:
            continue
        from core.agent_loop.activity_scheduler import assignment_wake_trigger

        if assignment_wake_trigger(card) is None:
            continue
        requests.append(build_task_assigned_trigger(card))
        queued.add(card.id)


def _handoff_pin_ids(action: dict[str, Any], *, delegate_id: str | None = None) -> list[str]:
    """Structured next_owners, then an explicit delegate target. No @ parse."""
    found: list[str] = []
    for agent_id in list(action.get("nextOwners") or []):
        token = str(agent_id or "").strip()
        if token and token not in found:
            found.append(token)
    delegate = (delegate_id or "").strip()
    if delegate and delegate not in found:
        found.append(delegate)
    return found


def _channel_handoff_kwargs(
    task: Any,
    action: dict[str, Any],
    *,
    author_id: str,
    delegate_id: str | None = None,
) -> dict[str, Any]:
    """Pins for a Done/handoff channel round. Empty when this task is not on a channel."""
    if task is None or getattr(task, "source_channel", None) != "channel":
        return {}
    if not str(getattr(task, "notification_channel_id", None) or "").strip():
        return {}
    from core.tasking.board import next_board_owner_id

    board = next_board_owner_id(task, author_id=author_id)
    return {
        "handoff": True,
        "required_ids": _handoff_pin_ids(action, delegate_id=delegate_id),
        "board_owner_ids": [board] if board else [],
    }


def _open_origin_handoff_if_quiet(
    result: dict[str, Any],
    *,
    agent: Agent,
    task: Any,
    action: dict[str, Any],
    delegate_id: str | None = None,
    work_bind_ids: list[str] | None = None,
) -> None:
    """Open one System AI round on the origin line when the share did not.

    A follow-up share already woke peers. This covers Done/handoff with no
    agent chat line. It does not post a second card.
    """
    if any(
        isinstance(item, dict) and item.get("trigger_type") == "channel_message"
        for item in (result.get("trigger_requests") or [])
    ):
        return
    posted = result.get("channel_message")
    if isinstance(posted, dict) and posted.get("author_type") == "agent" and posted.get("message_id"):
        return
    kwargs = _channel_handoff_kwargs(task, action, author_id=agent.id, delegate_id=delegate_id)
    if work_bind_ids:
        kwargs["work_bind_ids"] = list(work_bind_ids)
    if not kwargs:
        return
    channel_id = str(task.notification_channel_id).strip()
    origin = None
    for item in result.get("origin_status_messages") or []:
        if isinstance(item, dict) and item.get("message_id") and item.get("channel_id") == channel_id:
            origin = item
            break
    if origin is None:
        posted = result.get("channel_message")
        if isinstance(posted, dict) and posted.get("author_type") == "system" and posted.get("message_id"):
            origin = posted
    if origin is None:
        return
    from core.agent_loop.channel_rounds import start_channel_peer_round

    wakes = start_channel_peer_round(
        channel_id=channel_id,
        message_id=str(origin.get("message_id") or ""),
        content=str(origin.get("content") or ""),
        from_name=agent.name,
        author_type="system",
        exclude_agent_ids={agent.id},
        from_agent=agent.id,
        handoff=True,
        required_ids=kwargs.get("required_ids"),
        board_owner_ids=kwargs.get("board_owner_ids"),
        work_bind_ids=kwargs.get("work_bind_ids"),
    )
    if wakes:
        result.setdefault("trigger_requests", []).extend(wakes)
