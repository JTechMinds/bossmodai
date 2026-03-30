"""BossMod AI — Materialize direct-turn decisions into commitments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import build_activity_resume_trigger
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
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "clarify":
        result["detail"] = f"{agent.name} asked for clarification"
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "decline":
        if trigger.get("type") == "task_assigned" and trigger.get("task_id"):
            _complete_assignment_if_present(agent.id)
            db.update_task(
                trigger["task_id"],
                status="declined",
                status_note=(decision.reply or decision.detail or "Assignment declined."),
                watchdog_pinged_at=None,
            )
        result["detail"] = f"{agent.name} declined the request"
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
            result["detail"] = f'{agent.name} deferred "{task.title}"'
            result.setdefault("activity_extra", {})["task_title"] = task.title
        else:
            result["detail"] = f"{agent.name} deferred the request"
        _complete_assignment_if_present(agent.id)
        if trigger.get("type") in {"session_response", "channel_response"}:
            _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        else:
            _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.commitmentKind == "work":
        task = _resolve_or_create_work_task(agent, trigger, decision)
        task = _persist_work_contract(task, agent, decision)
        work_activity = activity_runtime.activate_work_activity(
            agent.id,
            task,
            title=task.title,
            detail=task.description,
            task_status="accepted",
            supersede_note="Paused for newer accepted work.",
            metadata=_build_initial_work_metadata(agent, state, decision.reply),
        )
        result["detail"] = f'{agent.name} accepted work on "{task.title}"'
        result.setdefault("activity_extra", {})["task_title"] = task.title
        result["trigger_requests"].append(
            build_activity_resume_trigger(
                work_activity,
                reason=_build_initial_work_reason(state, task.title),
            )
        )
        _append_shared_response_follow_up(result, agent_id=agent.id, trigger=trigger, responded=True)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
    _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
) -> dict[str, Any]:
    """Build work-activity metadata for a newly accepted work commitment.

    Only set a desk preference when the agent is not already in a valid
    workspace. This keeps the planner aligned with the actual work rule and
    avoids wasting a turn on `walkTo desk` while already at the desk/workspace.
    """
    metadata: dict[str, Any] = {
        "acknowledged_by_reply": bool(reply and reply.strip()),
    }
    room = get_room_at(state.x, state.y)
    if not room or room.get("room_type") != "workspace":
        metadata["preferred_destination"] = "desk"
        return metadata

    if agent.desk_x is not None and agent.desk_y is not None:
        if (state.x, state.y) != (agent.desk_x, agent.desk_y):
            return metadata

    return metadata


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
    reply: str | None,
) -> dict[str, Any]:
    """Persist and broadcast a direct-turn reply, if any."""
    if not reply or not reply.strip():
        return {}

    trigger_type = trigger.get("type")
    if trigger_type == "task_assigned":
        return _persist_assignment_reply(agent, state, trigger, reply)
    if trigger_type == "human_chat":
        target_id = HUMAN_SENDER_ID
        from_type = "agent"
        message_type = "work" if state.status == "work_active" else "social"
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


def _persist_assignment_reply(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    reply: str,
) -> dict[str, Any]:
    """Persist a reply to an explicit task assignment using the task's source metadata."""
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return {}
    task = db.get_task(task_id)
    if task is None:
        return {}

    if task.notification_channel_id and task.source_channel == "channel":
        message = db.create_channel_message(
            channel_id=task.notification_channel_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=reply.strip(),
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
            content=reply.strip(),
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
            content=reply.strip(),
            message_type="social",
            location_x=state.x,
            location_y=state.y,
        )
        return {
            "trigger_requests": [
                {
                    "agent_id": reply_target["agent_id"],
                    "trigger_type": "peer_message",
                    "source_channel": "chat",
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

    return {}


def _attach_reply_artifacts(
    result: dict[str, Any],
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    reply: str | None,
) -> None:
    """Persist reply side effects after the decision state change succeeds."""
    reply_artifacts = _persist_reply(agent, state, trigger, reply)
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
    if trigger.get("type") == "task_assigned" and trigger.get("task_id"):
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
    elif trigger.get("type") == "peer_message" and trigger.get("from_agent"):
        created_by = trigger["from_agent"]
    requester_id = task_requester_id_for_trigger(trigger, default_agent_id=agent.id)
    parent_task = _follow_up_parent_task_for_trigger(agent, trigger)
    owner_id = default_task_owner_id(
        assignee_id=agent.id,
        requester_id=requester_id,
        created_by=created_by,
        parent_task=parent_task,
    )

    return db.create_task(
        title=(decision.taskTitle or "").strip(),
        description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
        assigned_to=agent.id,
        requester_id=requester_id,
        owner_id=owner_id,
        created_by=created_by,
        parent_task_id=parent_task.id if parent_task else None,
        work_contract=_inherited_follow_up_work_contract(parent_task, decision),
        source_channel=task_source_channel_for_trigger(trigger),
        notification_policy=task_notification_policy_for_trigger(trigger),
        notification_channel_id=task_notification_channel_id_for_trigger(trigger),
    )


def _ensure_deferred_task(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
):
    """Return the durable task that should remain pending after a defer decision."""
    if trigger.get("type") == "task_assigned" and trigger.get("task_id"):
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
    return db.create_task(
        title=(decision.taskTitle or "").strip(),
        description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
        assigned_to=agent.id,
        requester_id=requester_id,
        owner_id=owner_id,
        created_by=created_by,
        parent_task_id=parent_task.id if parent_task else None,
        work_contract=_inherited_follow_up_work_contract(parent_task, decision),
        source_channel=task_source_channel_for_trigger(trigger),
        notification_policy=task_notification_policy_for_trigger(trigger),
        notification_channel_id=task_notification_channel_id_for_trigger(trigger),
    )


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
