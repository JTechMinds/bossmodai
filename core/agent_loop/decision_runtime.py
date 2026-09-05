"""Materialize a parsed conversation decision into commitments.

Public entrypoints: apply_decision, summarize_decision. Collaborators live
in decision_work_plan, decision_task_bind, decision_replies, and
decision_resume.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import build_activity_resume_trigger
from core.agent_loop.decision_contract import ConversationDecision
from core.agent_loop.decision_replies import (
    _append_shared_response_follow_up,
    _attach_reply_artifacts,
    _prepare_shared_response_trigger,
)
from core.agent_loop.decision_resume import (
    _complete_assignment_if_present,
    _record_watchdog_reply_if_needed,
    _resume_previous_work_if_needed,
    _resume_waiting_work_after_task_attention,
    _resume_waiting_work_after_task_update,
)
from core.agent_loop.decision_task_bind import (
    _ensure_deferred_task,
    _persist_work_contract,
    _resolve_or_create_work_task,
)
from core.agent_loop.decision_work_plan import (
    _build_initial_work_metadata,
    _build_initial_work_reason,
    _materialize_work_execution_plan,
    _resolve_work_execution_plan,
    _should_queue_initial_work_resume,
)
from core.models import Agent, AgentState
from core.tasking.transitions import transition_task


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
            _resume_waiting_work_after_task_update(
                result=result,
                agent_id=agent.id,
                trigger=trigger,
            )
        _record_watchdog_reply_if_needed(agent_id=agent.id, trigger=trigger, reply=decision.reply)
        return result

    if decision.decision == "answer":
        result["detail"] = f"{agent.name} answered the request"
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
        transition_task(
            active_work.task_id,
            "abandoned",
            reason="Cancelled by human request.",
            actor=agent.name,
            actor_type="agent",
            actor_agent_id=agent.id,
            source_trigger_id=trigger.get("trigger_id"),
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
        if trigger.get("type") == "meeting_invite":
            session_id = str(trigger.get("session_id") or "").strip()
            if session_id:
                now = datetime.now(timezone.utc)
                reason = (decision.reply or decision.detail or "Declined.").strip()
                participant = db.get_meeting_session_participant(session_id, agent.id)
                if participant is None:
                    db.upsert_meeting_session_participant(
                        session_id=session_id,
                        agent_id=agent.id,
                        state="declined",
                        required=True,
                        reason=reason,
                    )
                db.update_meeting_session_participant_state(
                    session_id=session_id,
                    agent_id=agent.id,
                    state="declined",
                    reason=reason,
                    responded_at=now,
                )
                db.create_meeting_session_message(
                    session_id=session_id,
                    author_type="system",
                    author_name="BossMod",
                    content=f"{agent.name} declined the meeting invite: {reason}",
                    source_channel="meeting",
                )
                meta = db.get_meeting_session_meta(session_id)
                host_id = str((meta or {}).get("host_agent_id") or "").strip()
                if host_id and host_id != agent.id:
                    result["trigger_requests"].append(
                        {
                            "agent_id": host_id,
                            "trigger_type": "activity_resumed",
                            "source_channel": "meeting",
                            "payload": {
                                "content": f'{agent.name} declined the meeting invite: {reason}',
                                "activity_kind": "meeting",
                                "activity_title": str(trigger.get("meeting_title") or "Meeting"),
                                "session_id": session_id,
                            },
                        }
                    )
        if trigger.get("type") in {"task_assigned", "task_follow_up"} and trigger.get("task_id"):
            _complete_assignment_if_present(agent.id)
            transition_task(
                trigger["task_id"],
                "declined",
                reason=decision.reply or decision.detail or "Assignment declined.",
                actor=agent.name,
                actor_type="agent",
                actor_agent_id=agent.id,
                source_trigger_id=trigger.get("trigger_id"),
                status_note=(decision.reply or decision.detail or "Assignment declined."),
                watchdog_pinged_at=None,
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
            bound = _ensure_deferred_task(agent, trigger, decision)
            if bound.get("error_result"):
                return bound["error_result"]
            task = bound["task"]
            status_note = (decision.reply or decision.detail or "").strip() or None
            task = transition_task(
                task.id,
                "pending",
                reason=status_note or f'Deferred "{task.title}".',
                actor=agent.name,
                actor_type="agent",
                actor_agent_id=agent.id,
                source_trigger_id=trigger.get("trigger_id"),
                status_note=status_note,
                completion_summary=None,
                watchdog_pinged_at=None,
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
        bound = _resolve_or_create_work_task(agent, trigger, decision)
        if bound.get("error_result"):
            return bound["error_result"]
        task = bound["task"]
        task = _persist_work_contract(task, agent, decision)
        materialized = _materialize_work_execution_plan(
            agent=agent,
            parent_task=task,
            trigger=trigger,
            plan_resolution=plan_resolution,
            result=result,
        )
        if materialized.get("error_result"):
            return materialized["error_result"]
        delegated_children = materialized["children"]
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
        if trigger.get("type") == "meeting_invite":
            session_id = str(trigger.get("session_id") or "").strip()
            if session_id:
                now = datetime.now(timezone.utc)
                participant = db.get_meeting_session_participant(session_id, agent.id)
                if participant is None:
                    db.upsert_meeting_session_participant(
                        session_id=session_id,
                        agent_id=agent.id,
                        state="accepted",
                        required=True,
                    )
                db.update_meeting_session_participant_state(
                    session_id=session_id,
                    agent_id=agent.id,
                    state="accepted",
                    responded_at=now,
                )
                db.create_meeting_session_message(
                    session_id=session_id,
                    author_type="system",
                    author_name="BossMod",
                    content=f"{agent.name} accepted the meeting invite.",
                    source_channel="meeting",
                )
        meeting = activity_runtime.begin_commitment_activity(
            agent.id,
            kind="meeting",
            title=decision.title or "Direct meeting",
            detail=decision.detail or trigger.get("content"),
            metadata={
                "preferred_destination": decision.destination,
                "source_channel": trigger.get("source_channel", "chat"),
                "acknowledged_by_reply": bool(decision.reply and decision.reply.strip()),
                "session_id": str(trigger.get("session_id") or "").strip() or None,
                "meeting_mode": str(trigger.get("meeting_mode") or "").strip() or None,
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
