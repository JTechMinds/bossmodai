"""BossMod AI — Materialize direct-turn decisions into commitments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import (
    build_activity_resume_trigger,
    build_task_assigned_trigger,
    build_task_follow_up_trigger,
    build_task_resume_trigger,
)
from core.agent_loop.channel_rounds import begin_channel_response, finalize_channel_response, observe_channel_message
from core.agent_loop.deliverables import build_work_contract
from core.agent_loop.message_delivery import (
    resolve_peer_message_type,
    source_channel_for_message_type,
)
from core.agent_loop.meeting_rounds import begin_session_response, finalize_session_response, observe_session_message
from core.agent_loop.task_origins import (
    task_notification_channel_id_for_trigger,
    task_notification_policy_for_trigger,
    task_source_channel_for_trigger,
)
from core.agent_loop.task_roles import (
    default_task_owner_id,
    task_assignment_reply_target,
    task_requester_id_for_trigger,
)
from core.agent_loop.decision_contract import ConversationDecision
from core.models import Agent, AgentState
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import append_task_event, create_or_bind_subtask, create_or_bind_task
from core.world.tilemap import get_room_at


def apply_decision(
    decision_payload: dict[str, Any],
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Apply a parsed conversation decision and return the turn result."""
    decision = ConversationDecision.model_validate(decision_payload)
    active_work = activity_runtime.get_active_work_activity(agent.id)

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} handled the direct request",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    trigger = _prepare_shared_response_trigger(agent, trigger, decision, result)
    if trigger is None:
        return result

    if decision.decision == "observe":
        result["detail"] = f"{agent.name} chose to observe"
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=False)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "answer":
        result["detail"] = f"{agent.name} answered the request"
        if trigger.get("type") == "task_follow_up" and trigger.get("task_id"):
            append_task_event(
                task_id=trigger["task_id"],
                author_type="agent",
                author_agent_id=agent.id,
                author_name=agent.name,
                event_type="answer",
                content=decision.reply or "",
                source_trigger_id=trigger.get("trigger_id"),
            )
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
            _resume_waiting_work_after_task_attention(
                result=result,
                agent=agent,
                trigger=trigger,
                decision=decision,
                active_work=active_work,
            )
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "clarify":
        result["detail"] = f"{agent.name} asked for clarification"
        if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
            append_task_event(
                task_id=trigger["task_id"],
                author_type="agent",
                author_agent_id=agent.id,
                author_name=agent.name,
                event_type="clarification",
                content=decision.reply or decision.detail or "",
                source_trigger_id=trigger.get("trigger_id"),
            )
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "cancel":
        if not active_work or not active_work.task_id:
            result["event"] = "agent_error"
            result["detail"] = f"{agent.name} tried to cancel work, but no active task was available"
            return result
        task = db.get_task(active_work.task_id)
        db.update_task(
            active_work.task_id,
            status="abandoned",
            status_note="Cancelled by human request.",
            completion_summary=None,
            watchdog_pinged_at=None,
        )
        activity_runtime.complete_activity(active_work.id, detail="Cancelled by human request.")
        result["detail"] = (
            f'{agent.name} cancelled active work on "{task.title}"'
            if task
            else f"{agent.name} cancelled the active task"
        )
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "decline":
        if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
            _complete_assignment_if_present(agent.id)
            db.update_task(
                trigger["task_id"],
                status="declined",
                status_note=(decision.reply or decision.detail or "Assignment declined."),
                watchdog_pinged_at=None,
            )
            append_task_event(
                task_id=trigger["task_id"],
                author_type="agent",
                author_agent_id=agent.id,
                author_name=agent.name,
                event_type="status_update",
                content=decision.reply or decision.detail or "Assignment declined.",
                source_trigger_id=trigger.get("trigger_id"),
            )
        result["detail"] = f"{agent.name} declined the request"
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "defer":
        if decision.commitmentKind == "work":
            task = _ensure_deferred_task(agent, trigger, decision)
            status_note = (decision.reply or decision.detail or "").strip() or None
            task = db.update_task(
                task.id,
                status="pending",
                status_note=status_note,
                completion_summary=None,
                watchdog_pinged_at=None,
            ) or task
            append_task_event(
                task_id=task.id,
                author_type="agent",
                author_agent_id=agent.id,
                author_name=agent.name,
                event_type="status_update",
                content=status_note or f'Deferred "{task.title}".',
                source_trigger_id=trigger.get("trigger_id"),
            )
            result["detail"] = f'{agent.name} deferred "{task.title}"'
            result.setdefault("activity_extra", {})["task_title"] = task.title
        else:
            result["detail"] = f"{agent.name} deferred the request"
        _complete_assignment_if_present(agent.id)
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.commitmentKind == "work":
        plan_resolution = _resolve_work_execution_plan(agent, decision)
        if plan_resolution.get("error_result"):
            return plan_resolution["error_result"]
        task = _resolve_or_create_work_task(agent, trigger, decision)
        task = _persist_work_contract(task, agent, decision)
        delegated_children = _materialize_work_execution_plan(
            agent=agent,
            parent_task=task,
            trigger=trigger,
            plan_resolution=plan_resolution,
            result=result,
        )
        work_activity = activity_runtime.activate_work_activity(
            agent.id,
            task,
            title=task.title,
            detail=task.description,
            task_status="accepted",
            supersede_note="Paused for newer accepted work.",
            metadata=_build_initial_work_metadata(
                agent,
                state,
                decision.reply,
                plan_mode=str(plan_resolution.get("mode") or "self"),
                delegated_task_ids=[child.id for child in delegated_children],
            ),
        )
        result["detail"] = f'{agent.name} accepted work on "{task.title}"'
        append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="status_update",
            content=decision.reply or f'Accepted work on "{task.title}".',
            source_trigger_id=trigger.get("trigger_id"),
        )
        result.setdefault("activity_extra", {})["task_title"] = task.title
        if _should_queue_initial_work_resume(task=task, plan_mode=str(plan_resolution.get("mode") or "self")):
            result["trigger_requests"].append(
                build_activity_resume_trigger(
                    work_activity,
                    reason=_build_initial_work_reason(state, task.title),
                )
            )
        _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.commitmentKind == "meeting":
        meeting = activity_runtime.begin_commitment_activity(
            agent.id,
            kind="meeting",
            title=decision.title or "Direct meeting",
            detail=decision.detail or trigger.get("content"),
            metadata={
                "preferred_destination": decision.destination,
                "source_channel": trigger.get("source_channel", "chat"),
                "acknowledged_by_reply": bool(decision.reply and decision.reply.strip()),
            },
            reason="Replaced by a newer accepted meeting.",
        )
        result["detail"] = f"{agent.name} accepted the meeting request"
        result["trigger_requests"].append(
            build_activity_resume_trigger(
                meeting,
                reason="Follow through on the accepted meeting.",
            )
        )
        _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.commitmentKind == "break":
        pause = activity_runtime.begin_commitment_activity(
            agent.id,
            kind="break",
            title=decision.title or "Break",
            detail=decision.detail or trigger.get("content"),
            metadata={
                "preferred_destination": "breakRoom",
                "acknowledged_by_reply": bool(decision.reply and decision.reply.strip()),
            },
            reason="Replaced by a newer accepted break.",
        )
        result["detail"] = f"{agent.name} accepted the break request"
        result["trigger_requests"].append(
            build_activity_resume_trigger(
                pause,
                reason="Follow through on the accepted break.",
            )
        )
        _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        _attach_reply_artifacts(result, agent, state, trigger, decision)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    conversation = activity_runtime.begin_commitment_activity(
        agent.id,
        kind="conversation",
        title=decision.title or "Direct conversation",
        detail=decision.detail or trigger.get("content"),
        metadata={
            "preferred_destination": decision.destination,
            "source_channel": trigger.get("source_channel", "chat"),
            "acknowledged_by_reply": bool(decision.reply and decision.reply.strip()),
        },
        reason="Replaced by a newer direct request.",
    )
    result["detail"] = f"{agent.name} accepted the direct request"
    result["trigger_requests"].append(
        build_activity_resume_trigger(
            conversation,
            reason="Follow through on the accepted direct request.",
        )
    )
    _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
    _attach_reply_artifacts(result, agent, state, trigger, decision)
    _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
    return result


def summarize_decision(decision_payload: dict[str, Any]) -> str:
    """Return a concise diagnostic label for a decision turn."""
    decision = ConversationDecision.model_validate(decision_payload)
    commitment = decision.commitmentKind
    return f'{decision.decision}({commitment})'


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
            matches = by_name.get(delegation.agentName.strip().lower(), [])
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
) -> list[Any]:
    """Persist delegated child tasks from an accepted work plan and queue assignee triggers."""
    delegated_children: list[Any] = []
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
        child = creation.task
        delegated_children.append(child)
        if child.status == "pending":
            result["trigger_requests"].append(build_task_assigned_trigger(child))
    return delegated_children


def _prepare_shared_response_trigger(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Prepare shared conversation queueing and return the effective trigger to apply."""
    trigger_type = trigger.get("type")
    if trigger_type not in {"session_message", "channel_message"}:
        return trigger

    if trigger_type == "session_message":
        if decision.decision == "observe":
            observed_result = observe_session_message(agent, trigger)
            result.update(observed_result)
            return None
        queued_result, active_now = begin_session_response(agent, trigger)
        if not active_now:
            result.update(queued_result)
            return None
        return {**trigger, "type": "session_response"}

    if decision.decision == "observe":
        observed_result = observe_channel_message(agent, trigger)
        result.update(observed_result)
        return None
    queued_result, active_now = begin_channel_response(agent, trigger)
    if not active_now:
        result.update(queued_result)
        return None
    return {**trigger, "type": "channel_response"}


def _append_shared_response_follow_up(
    result: dict[str, Any],
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
) -> None:
    """Advance any shared reply queue after the current responder finishes."""
    trigger_type = trigger.get("type")
    if trigger_type == "session_response":
        result["trigger_requests"].extend(
            finalize_session_response(
                agent_id=agent_id,
                trigger=trigger,
                responded=responded,
            )
        )
    elif trigger_type == "channel_response":
        result["trigger_requests"].extend(
            finalize_channel_response(
                agent_id=agent_id,
                trigger=trigger,
                responded=responded,
            )
        )


def _persist_reply(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> dict[str, Any]:
    """Persist and broadcast a direct-turn reply, if any."""
    reply = decision.reply
    if not reply or not reply.strip():
        return {}

    trigger_type = trigger.get("type")
    if trigger_type in {"task_assigned", "task_follow_up"}:
        return _persist_task_follow_up_reply(agent, state, trigger, decision)
    if trigger_type == "human_chat":
        target_id = HUMAN_SENDER_ID
        from_type = "agent"
        message_type = "work" if state.status in {"work_active", "waiting", "blocked"} else "social"
    elif trigger_type == "session_response":
        session_id = trigger.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return {}
        message = db.create_meeting_session_message(
            session_id=session_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=reply.strip(),
            source_channel="meeting",
        )
        return {
            "meeting_message": {
                "agent_id": agent.id,
                "session_id": session_id,
                "content": message.content,
                "author_type": "agent",
                "author_name": agent.name,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }
    elif trigger_type == "channel_response":
        channel_id = trigger.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id.strip():
            return {}
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=reply.strip(),
            source_channel="channel",
        )
        return {
            "channel_message": {
                "channel_id": channel_id,
                "content": message.content,
                "author_type": "agent",
                "author_name": agent.name,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }
    elif trigger_type == "peer_message":
        target_id = trigger.get("from_agent")
        from_type = None
        if not target_id:
            return {}
        message_type = resolve_peer_message_type(state=state, trigger=trigger)
    else:
        return {}

    message = db.create_message(
        from_agent=agent.id,
        to_agent=target_id,
        content=reply.strip(),
        message_type=message_type,
        location_x=state.x,
        location_y=state.y,
    )

    if trigger_type == "human_chat":
        return {
            "chat_message": {
                "agent_id": agent.id,
                "content": message.content,
                "from_type": from_type,
                "from_name": agent.name,
                "message_type": message.message_type,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }

    return {
        "trigger_requests": [
            {
                "agent_id": target_id,
                "trigger_type": "peer_message",
                "source_channel": source_channel_for_message_type(message_type),
                "payload": {
                    "content": message.content,
                    "from_agent": agent.id,
                    "from_name": agent.name,
                    "message_type": message.message_type,
                    "source_message_id": message.id,
                },
            }
        ]
    }
def _task_turn_requires_response(
    *,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> bool:
    """Return whether a task-thread reply should wake the other participant."""
    trigger_type = str(trigger.get("type") or "")
    if trigger_type == "task_assigned":
        return decision.decision in {"clarify", "defer", "decline"}

    task_status = str(trigger.get("task_status") or "").strip().lower()
    task_party = str(trigger.get("task_party") or "").strip().lower()
    pending_assignee_turn = task_status == "pending" and task_party == "assignee"
    if pending_assignee_turn:
        return decision.decision in {"clarify", "defer", "decline"}
    pending_stakeholder_turn = task_status == "pending" and task_party == "stakeholder"
    if pending_stakeholder_turn:
        return decision.decision in {"answer", "clarify"}
    return decision.decision == "clarify"


def _task_turn_attention_kind(
    *,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> str:
    """Return the follow-up attention reason for a task-thread reply."""
    task_status = str(trigger.get("task_status") or "").strip().lower()
    task_party = str(trigger.get("task_party") or "").strip().lower()
    if task_status == "pending" and task_party == "stakeholder" and decision.decision == "answer":
        return "decision_needed"
    if decision.decision == "clarify":
        return "clarification_requested"
    if decision.decision in {"defer", "decline"}:
        return "decision_needed"
    return str(trigger.get("attention_kind") or "task_response")


def _persist_task_follow_up_reply(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> dict[str, Any]:
    """Persist a reply inside the canonical task-bound follow-up lane."""
    reply = (decision.reply or "").strip()
    if not reply:
        return {}
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return {}
    task = db.get_task(task_id)
    if task is None:
        return {}
    requires_response = _task_turn_requires_response(trigger=trigger, decision=decision)
    attention_kind = _task_turn_attention_kind(trigger=trigger, decision=decision)

    if trigger.get("type") == "task_follow_up" and trigger.get("from_agent"):
        target_agent_id = str(trigger["from_agent"]).strip()
        if target_agent_id:
            message = db.create_message(
                from_agent=agent.id,
                to_agent=target_agent_id,
                content=reply,
                message_type="work",
                location_x=state.x,
                location_y=state.y,
            )
            db.create_notification(
                agent_id=target_agent_id,
                task_id=task.id,
                kind="task_update",
                content=reply,
                source_channel="task",
                policy="none",
                chat_visible=False,
                prompt_visibility=False,
            )
            if not requires_response:
                return {}
            return {
                "trigger_requests": [
                    build_task_follow_up_trigger(
                        task,
                        recipient_agent_id=target_agent_id,
                        from_agent=agent.id,
                        from_name=agent.name,
                        content=message.content,
                        attention_kind=attention_kind,
                        source_message_id=message.id,
                        source_channel="work",
                    )
                ]
            }

    if task.notification_channel_id and task.source_channel == "channel":
        message = db.create_channel_message(
            channel_id=task.notification_channel_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=reply,
            source_channel="channel",
        )
        return {
            "channel_message": {
                "channel_id": task.notification_channel_id,
                "content": message.content,
                "author_type": "agent",
                "author_name": agent.name,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }

    reply_target = task_assignment_reply_target(task, assignee_id=agent.id)

    if reply_target["kind"] == "human":
        message = db.create_message(
            from_agent=agent.id,
            to_agent=HUMAN_SENDER_ID,
            content=reply,
            message_type="social",
            location_x=state.x,
            location_y=state.y,
        )
        return {
            "chat_message": {
                "agent_id": agent.id,
                "content": message.content,
                "from_type": "agent",
                "from_name": agent.name,
                "message_type": message.message_type,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }

    if reply_target["kind"] == "agent" and reply_target["agent_id"]:
        message = db.create_message(
            from_agent=agent.id,
            to_agent=reply_target["agent_id"],
            content=reply,
            message_type="work",
            location_x=state.x,
            location_y=state.y,
        )
        db.create_notification(
            agent_id=reply_target["agent_id"],
            task_id=task.id,
            kind="task_update",
            content=reply,
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
        if not requires_response:
            return {}
        return {
            "trigger_requests": [
                build_task_follow_up_trigger(
                    task,
                    recipient_agent_id=reply_target["agent_id"],
                    from_agent=agent.id,
                    from_name=agent.name,
                    content=message.content,
                    attention_kind=attention_kind,
                    source_message_id=message.id,
                    source_channel="work",
                )
            ]
        }

    return {}


def _attach_reply_artifacts(
    result: dict[str, Any],
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> None:
    """Persist reply side effects after the decision state change succeeds."""
    reply_artifacts = _persist_reply(agent, state, trigger, decision)
    if reply_artifacts.get("chat_message"):
        result["chat_message"] = reply_artifacts["chat_message"]
    if reply_artifacts.get("meeting_message"):
        result["meeting_message"] = reply_artifacts["meeting_message"]
    if reply_artifacts.get("channel_message"):
        result["channel_message"] = reply_artifacts["channel_message"]
    result["trigger_requests"].extend(reply_artifacts.get("trigger_requests", []))


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
        return task

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
        return create_or_bind_subtask(
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
        ).task
    return create_or_bind_task(
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
    ).task


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
        return task

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
        return create_or_bind_subtask(
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
        ).task
    return create_or_bind_task(
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
    ).task


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

def _resume_previous_work_if_needed(result: dict[str, Any], active_work: Any | None) -> None:
    """Queue a work resume trigger after a direct interruption if work stayed active."""
    if active_work is None:
        return
    if any(item.get("trigger_type") == "activity_resumed" for item in result["trigger_requests"]):
        return
    result["trigger_requests"].append(
        build_activity_resume_trigger(
            active_work,
            reason=f'Resume work on "{active_work.title or "your task"}".',
        )
    )


def _resume_waiting_work_after_task_attention(
    *,
    result: dict[str, Any],
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
    active_work: Any | None,
) -> None:
    """Resume a waiting/blocked task when a task-attention reply resolves the dependency."""
    if active_work is not None:
        return
    if trigger.get("type") != "task_follow_up" or decision.decision != "answer":
        return

    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return
    attention_kind = str(trigger.get("attention_kind") or "").strip().lower()
    task = db.get_task(task_id)
    if task is None:
        return

    if attention_kind in {"question", "review_request"}:
        target_agent_id = str(trigger.get("from_agent") or "").strip()
        if (
            target_agent_id
            and task.assigned_to == target_agent_id
            and task.status in {"waiting", "blocked"}
            and not db.has_open_trigger_matching(target_agent_id, trigger_types=["activity_resumed"], task_id=task.id)
        ):
            result["trigger_requests"].append(
                build_task_resume_trigger(
                    task,
                    reason=f'You received the task update you needed on "{task.title}". Continue the work.',
                )
            )
        return

    if attention_kind not in {"completion_report", "blocker", "handoff", "abandoned"}:
        return

    parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
    if (
        parent is not None
        and parent.assigned_to == agent.id
        and parent.status in {"waiting", "blocked"}
        and not db.has_open_trigger_matching(agent.id, trigger_types=["activity_resumed"], task_id=parent.id)
    ):
        result["trigger_requests"].append(
            build_task_resume_trigger(
                parent,
                reason=f'You received an update on "{task.title}". Continue "{parent.title}".',
            )
        )
        return

    if (
        task.assigned_to == agent.id
        and task.status in {"waiting", "blocked"}
        and not db.has_open_trigger_matching(agent.id, trigger_types=["activity_resumed"], task_id=task.id)
    ):
        result["trigger_requests"].append(
            build_task_resume_trigger(
                task,
                reason=f'You received the task update you needed on "{task.title}". Continue the work.',
            )
        )


def _record_watchdog_reply_if_needed(*, agent_id: str, trigger: dict[str, Any], reply: str | None) -> None:
    """Refresh task liveness when the agent answers a watchdog ping."""
    if trigger.get("type") != "watchdog_status_ping":
        return
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return

    now = datetime.now(timezone.utc)
    status_note = (reply or "").strip() or None
    update_fields: dict[str, Any] = {
        "watchdog_pinged_at": None,
        "last_heartbeat_at": now,
        "last_activity": now,
    }
    if status_note:
        update_fields["status_note"] = status_note
    db.update_task(task_id, **update_fields)

    active = activity_runtime.get_active_work_activity(agent_id)
    if active and active.task_id == task_id and status_note:
        db.update_activity(active.id, detail=status_note)


def _complete_assignment_if_present(agent_id: str) -> None:
    """Complete the active assignment wrapper, if one exists."""
    active = activity_runtime.get_active_activity(agent_id)
    if active and active.kind == "assignment":
        activity_runtime.complete_activity(active.id, detail=active.detail)
