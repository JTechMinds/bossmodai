"""Host order: post operator ``say`` before running Talk/status/channel actions.

When a decision envelope has both a non-empty ``say`` and actions, the thread
must see the bubble before CLI / Board side effects run. Fail closed if that
``say`` cannot post — do not silently burn a long action with no thread signal.

Actions-only envelopes post nothing early (thinking… until a later ``say``).
Work wakes are not forced to ack.
"""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.decision_contract import ConversationDecision
from core.models import Agent, AgentState
from core.models.channel import ChannelArchivedError
from core.models.message import HUMAN_SENDER_ID

# Talk / 1:1 status / shared channel (and meeting) decision surfaces.
TALK_STATUS_CHANNEL_TYPES = frozenset(
    {
        "human_chat",
        "channel_message",
        "channel_response",
        "session_message",
        "session_response",
    }
)


def is_talk_status_channel_trigger(trigger: dict[str, Any]) -> bool:
    """Return whether this wake is a Talk / status / channel decision surface."""
    return str(trigger.get("type") or "") in TALK_STATUS_CHANNEL_TYPES


def decision_has_side_effect_actions(decision: ConversationDecision) -> bool:
    """Return whether the decision carries Board / commitment actions (not say-only)."""
    if decision.decision in {"accept", "cancel", "decline", "defer"}:
        return True
    return (decision.commitmentKind or "none") != "none"


def should_post_say_before_actions(
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> bool:
    """True when host must flush non-empty say before commitment side effects."""
    if not is_talk_status_channel_trigger(trigger):
        return False
    if not (decision.reply or "").strip():
        return False
    return decision_has_side_effect_actions(decision)


def merge_say_artifacts(result: dict[str, Any], artifacts: dict[str, Any]) -> None:
    """Copy persisted say artifacts onto the turn result (once)."""
    if artifacts.get("chat_message"):
        result["chat_message"] = artifacts["chat_message"]
    if artifacts.get("meeting_message"):
        result["meeting_message"] = artifacts["meeting_message"]
    if artifacts.get("channel_message"):
        result["channel_message"] = artifacts["channel_message"]
    result.setdefault("trigger_requests", []).extend(artifacts.get("trigger_requests") or [])


def say_already_posted(result: dict[str, Any]) -> bool:
    """Return whether operator-visible say was already attached this turn."""
    return bool(
        result.get("chat_message")
        or result.get("channel_message")
        or result.get("meeting_message")
    )


def persist_operator_say(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    say: str | None,
) -> dict[str, Any] | None:
    """Persist operator-visible say for Talk/status/channel.

    Returns:
      - ``{}`` when there is nothing to post early (blank say, or wrong surface)
      - artifact dict when the bubble was persisted
      - ``None`` when say was required and could not post (fail closed)
    """
    text = (say or "").strip()
    if not text:
        return {}
    if not is_talk_status_channel_trigger(trigger):
        return {}

    trigger_type = str(trigger.get("type") or "")
    if trigger_type == "human_chat":
        message_type = (
            "work" if state.status in {"work_active", "waiting", "blocked"} else "social"
        )
        message = db.create_message(
            from_agent=agent.id,
            to_agent=HUMAN_SENDER_ID,
            content=text,
            message_type=message_type,
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

    if trigger_type in {"channel_message", "channel_response"}:
        channel_id = trigger.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id.strip():
            return None
        if db.is_channel_archived(channel_id):
            return None
        try:
            message = db.create_channel_message(
                channel_id=channel_id,
                author_type="agent",
                author_agent_id=agent.id,
                author_name=agent.name,
                content=text,
                source_channel="channel",
            )
        except ChannelArchivedError:
            return None
        return {
            "channel_message": {
                "channel_id": channel_id,
                "content": message.content,
                "author_type": "agent",
                "author_name": agent.name,
                "author_agent_id": agent.id,
                "message_id": message.id,
                "created_at": message.created_at,
            }
        }

    if trigger_type in {"session_message", "session_response"}:
        session_id = trigger.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        message = db.create_meeting_session_message(
            session_id=session_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=text,
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

    return {}


def early_say_fail_result(agent: Agent, *, detail: str | None = None) -> dict[str, Any]:
    """Turn result when required say could not post before actions."""
    return {
        "event": "agent_error",
        "detail": detail
        or f"{agent.name} could not post say before actions; actions were not run",
        "agent_name": agent.name,
        "trigger_requests": [],
    }
