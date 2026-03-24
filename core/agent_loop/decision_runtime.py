"""BossMod AI — Materialize direct-turn decisions into commitments."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import build_activity_resume_trigger
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
    """Apply a parsed direct-request decision and return the turn result."""
    decision = ConversationDecision.model_validate(decision_payload)
    active_work = activity_runtime.get_active_work_activity(agent.id)

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} handled the direct request",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    if decision.decision == "answer":
        result["detail"] = f"{agent.name} answered the request"
        _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        return result

    if decision.decision == "clarify":
        result["detail"] = f"{agent.name} asked for clarification"
        _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        return result

    if decision.decision == "defer":
        if decision.commitmentKind == "work":
            task = _ensure_deferred_task(agent, trigger, decision)
            result["detail"] = f'{agent.name} deferred "{task.title}"'
            result.setdefault("activity_extra", {})["task_title"] = task.title
        else:
            result["detail"] = f"{agent.name} deferred the request"
        _complete_assignment_if_present(agent.id)
        _resume_previous_work_if_needed(result, active_work)
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
        return result

    if decision.commitmentKind == "work":
        task = _resolve_or_create_work_task(agent, trigger, decision)
        work_activity = activity_runtime.activate_work_activity(
            agent.id,
            task,
            title=task.title,
            detail=task.description,
            task_status="accepted",
            supersede_note="Paused for newer accepted work.",
            metadata={
                "preferred_destination": "desk",
                "acknowledged_by_reply": bool(decision.reply and decision.reply.strip()),
            },
        )
        result["detail"] = f'{agent.name} accepted work on "{task.title}"'
        result.setdefault("activity_extra", {})["task_title"] = task.title
        result["trigger_requests"].append(
            build_activity_resume_trigger(
                work_activity,
                reason=_build_initial_work_reason(state, task.title),
            )
        )
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
        _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
    _attach_reply_artifacts(result, agent, state, trigger, decision.reply)
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
    if trigger_type == "human_chat":
        target_id = HUMAN_SENDER_ID
        from_type = "agent"
        message_type = "work" if state.status == "work_active" else "social"
    elif trigger_type == "peer_message":
        target_id = trigger.get("from_agent")
        from_type = None
        message_type = "social" if state.status == "social_active" else "work"
        if not target_id:
            return {}
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
                "source_channel": "chat" if message_type == "social" else "work",
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
    elif trigger.get("type") == "peer_message" and trigger.get("from_agent"):
        created_by = trigger["from_agent"]

    return db.create_task(
        title=(decision.taskTitle or "").strip(),
        description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
        assigned_to=agent.id,
        created_by=created_by,
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

    created_by = HUMAN_SENDER_ID if trigger.get("type") == "human_chat" else trigger.get("from_agent") or agent.id
    return db.create_task(
        title=(decision.taskTitle or "").strip(),
        description=(decision.taskDescription or trigger.get("content") or "").strip() or None,
        assigned_to=agent.id,
        created_by=created_by,
    )


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


def _complete_assignment_if_present(agent_id: str) -> None:
    """Complete the active assignment wrapper, if one exists."""
    active = activity_runtime.get_active_activity(agent_id)
    if active and active.kind == "assignment":
        activity_runtime.complete_activity(active.id, detail=active.detail)
