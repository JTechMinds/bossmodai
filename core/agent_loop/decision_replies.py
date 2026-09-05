"""Persist decision replies and advance shared meeting/channel response queues."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.activity_scheduler import build_task_follow_up_trigger
from core.agent_loop.channel_rounds import begin_channel_response, finalize_channel_response, observe_channel_message
from core.agent_loop.decision_contract import ConversationDecision
from core.agent_loop.meeting_rounds import begin_session_response, finalize_session_response, observe_session_message
from core.agent_loop.message_delivery import (
    resolve_peer_message_type,
    source_channel_for_message_type,
)
from core.agent_loop.task_roles import task_assignment_reply_target
from core.models import Agent, AgentState
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import append_task_event
from core.tasking.transitions import transition_task


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

    if trigger_type == "peer_message":
        incoming_type = str(trigger.get("message_type") or "").strip().lower()
        incoming_content = str(trigger.get("content") or "")
        try:
            incoming_depth = int(trigger.get("reply_chain_depth") or 0)
        except (TypeError, ValueError):
            incoming_depth = 0
        reply_intent = str(getattr(decision, "intentKind", "") or "").strip().lower()
        should_wake = (
            incoming_type in {"work", "meeting"}
            or decision.decision in {"clarify", "accept", "decline"}
            or reply_intent == "question"
            or (incoming_depth <= 0 and "?" in incoming_content)
        )
        if incoming_type == "social" and incoming_depth > 0 and should_wake:
            should_wake = False
        if not should_wake:
            return {}

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
                    "in_reply_to_message_id": trigger.get("source_message_id"),
                    "reply_chain_depth": incoming_depth + 1 if trigger_type == "peer_message" else 0,
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

    attention_kind = str(trigger.get("attention_kind") or "").strip().lower()
    if attention_kind in {"question", "review_request"}:
        return decision.decision == "clarify"

    task_status = str(trigger.get("task_status") or "").strip().lower()
    task_party = str(trigger.get("task_party") or "").strip().lower()
    pending_assignee_turn = task_status == "pending" and task_party == "assignee"
    if pending_assignee_turn:
        return decision.decision in {"clarify", "defer", "decline"}
    pending_stakeholder_turn = task_status == "pending" and task_party == "stakeholder"
    if pending_stakeholder_turn:
        return decision.decision == "clarify"
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

def _clarification_streak(task_id: str, *, limit: int = 50) -> list[Any]:
    """Return the trailing clarification streak for a task thread (oldest-first)."""
    events = db.list_task_events(task_id, limit=limit)
    streak: list[Any] = []
    for event in reversed(events):
        if event.event_type != "clarification":
            break
        streak.append(event)
    streak.reverse()
    return streak

def _block_task_for_clarification_loop(
    *,
    task,
    latest_question: str,
    source_trigger_id: str | None,
    streak_len: int,
) -> str:
    snippet = (latest_question or "").strip()
    if len(snippet) > 280:
        snippet = snippet[:277] + "..."
    note = (
        f"Blocked: clarification loop detected ({streak_len} consecutive clarifications without progress). "
        "Consolidate required details into one decision. "
        f"Latest clarification: {snippet or '-'}"
    )
    transition_task(
        task.id,
        "blocked",
        reason=note,
        actor="BossMod",
        source_trigger_id=source_trigger_id,
        status_note=note,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    append_task_event(
        task_id=task.id,
        author_type="system",
        author_name="BossMod",
        event_type="blocker",
        content=note,
        source_trigger_id=source_trigger_id,
    )
    for notify_target in (getattr(task, "owner_id", None), getattr(task, "requester_id", None)):
        if not isinstance(notify_target, str) or not notify_target:
            continue
        if notify_target in {HUMAN_SENDER_ID, task.assigned_to}:
            continue
        db.create_notification(
            agent_id=notify_target,
            task_id=task.id,
            kind="task_update",
            content=note,
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
    return note

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
    if decision.decision == "clarify" and requires_response:
        streak = _clarification_streak(task.id, limit=60)
        tail_size = 6
        tail = streak[-tail_size:] if len(streak) >= tail_size else []
        actors = {event.author_agent_id for event in tail if getattr(event, "author_agent_id", None)}
        if tail and len(actors) >= 2:
            note = _block_task_for_clarification_loop(
                task=task,
                latest_question=reply,
                source_trigger_id=trigger.get("trigger_id"),
                streak_len=len(streak),
            )
            reply_target = task_assignment_reply_target(task, assignee_id=agent.id)
            if reply_target["kind"] == "human":
                message = db.create_message(
                    from_agent=agent.id,
                    to_agent=HUMAN_SENDER_ID,
                    content=note,
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
            return {}

    event_type = "comment"
    if decision.decision == "clarify":
        event_type = "clarification"
    elif decision.decision == "answer":
        event_type = "answer"
    persisted = append_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type=event_type,
        content=reply,
        source_trigger_id=trigger.get("trigger_id"),
    )

    if trigger.get("type") == "task_follow_up" and trigger.get("from_agent"):
        target_agent_id = str(trigger["from_agent"]).strip()
        if target_agent_id:
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
                        content=reply,
                        attention_kind=attention_kind,
                        source_task_event_id=persisted.id if persisted is not None else None,
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
                    content=reply,
                    attention_kind=attention_kind,
                    source_task_event_id=persisted.id if persisted is not None else None,
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
