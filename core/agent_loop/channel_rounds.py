"""BossMod AI — Shared channel response-round coordination."""

from __future__ import annotations

from typing import Any

import db
from core.models import Agent


def observe_channel_message(
    agent: Agent,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Mark one shared-channel message as observed without replying."""
    round_id = str(trigger.get("round_id") or "").strip()
    channel_id = str(trigger.get("channel_id") or "").strip()
    if not round_id or not channel_id:
        return {
            "event": "agent_error",
            "detail": f"{agent.name} could not evaluate the shared channel message",
            "agent_name": agent.name,
            "trigger_requests": [],
        }

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} reviewed the shared channel message",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    db.mark_channel_candidate_observed(round_id=round_id, agent_id=agent.id)
    db.maybe_complete_channel_response_round(round_id)
    result["detail"] = f"{agent.name} chose to observe the shared channel"
    return result


def begin_channel_response(
    agent: Agent,
    trigger: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Queue one shared-channel responder and report whether they can speak now."""
    round_id = str(trigger.get("round_id") or "").strip()
    channel_id = str(trigger.get("channel_id") or "").strip()
    if not round_id or not channel_id:
        return (
            {
                "event": "agent_error",
                "detail": f"{agent.name} could not evaluate the shared channel message",
                "agent_name": agent.name,
                "trigger_requests": [],
            },
            False,
        )

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} reviewed the shared channel message",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    queued = db.reserve_channel_response_slot(round_id=round_id, agent_id=agent.id)
    if queued is None:
        result["detail"] = f"{agent.name} could not join the shared channel reply queue"
        return result, False

    responding = db.activate_next_channel_response_candidate(round_id)
    if responding and responding.agent_id == agent.id:
        result["detail"] = f"{agent.name} joined the shared reply queue and is up next"
        return result, True
    else:
        result["detail"] = f"{agent.name} joined the shared reply queue"
        return result, False


def finalize_channel_response(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
) -> list[dict[str, Any]]:
    """Advance the shared channel reply queue after one responder finishes."""
    round_id = str(trigger.get("round_id") or "").strip()
    if not round_id:
        return []

    if responded:
        db.mark_channel_candidate_responded(round_id=round_id, agent_id=agent_id)
    else:
        db.mark_channel_candidate_observed(round_id=round_id, agent_id=agent_id)

    next_candidate = db.activate_next_channel_response_candidate(round_id)
    db.maybe_complete_channel_response_round(round_id)
    if next_candidate is None:
        return []
    return [
        _build_channel_response_trigger(
            agent_id=next_candidate.agent_id,
            trigger=trigger,
        )
    ]


def _build_channel_response_trigger(*, agent_id: str, trigger: dict[str, Any]) -> dict[str, Any]:
    """Build the durable trigger for the next queued shared-channel responder."""
    return {
        "agent_id": agent_id,
        "trigger_type": "channel_response",
        "source_channel": "channel",
        "payload": {
            "content": trigger.get("content", ""),
            "channel_id": trigger.get("channel_id"),
            "round_id": trigger.get("round_id"),
            "from_name": trigger.get("from_name", "Human Operator"),
            "author_type": trigger.get("author_type", "human"),
            "from_agent": trigger.get("from_agent"),
            "source_message_id": trigger.get("source_message_id"),
            "channel_name": trigger.get("channel_name"),
        },
    }
