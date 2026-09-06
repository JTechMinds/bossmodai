"""BossMod AI — Shared channel response-round coordination."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.response_rounds import (
    SharedRoundBinding,
    begin_shared_response,
    finalize_shared_response,
    observe_shared_message,
)
from core.models import Agent

_CHANNEL_ROUNDS = SharedRoundBinding(
    parent_key="channel_id",
    label="channel",
    queue_fail_label="shared channel reply queue",
    trigger_type="channel_response",
    source_channel="channel",
    extra_payload_key="channel_name",
    mark_observed=db.mark_channel_candidate_observed,
    mark_responded=db.mark_channel_candidate_responded,
    reserve=db.reserve_channel_response_slot,
    activate_next=db.activate_next_channel_response_candidate,
    maybe_complete=db.maybe_complete_channel_response_round,
)


def start_channel_peer_round(
    *,
    channel_id: str,
    message_id: str,
    content: str,
    from_name: str,
    author_type: str,
    exclude_agent_ids: set[str] | frozenset[str] | None = None,
    from_agent: str | None = None,
    channel_name: str | None = None,
) -> list[dict[str, Any]]:
    """Open a new channel response round so peers can react to one message.

    Human ingress wakes every member. Agent deliverable / follow-up posts
    exclude the author. In-round ``channel_response`` replies must not call
    this — ``finalize_channel_response`` already advances that queue, and a
    nested peer round would re-wake people who already spoke.
    """
    excluded = {
        item.strip()
        for item in (exclude_agent_ids or set())
        if isinstance(item, str) and item.strip()
    }
    peer_ids: list[str] = []
    for member in db.list_channel_member_details(channel_id):
        agent_id = member.get("id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            continue
        if agent_id in excluded:
            continue
        peer_ids.append(agent_id)
    if not peer_ids:
        return []

    resolved_name = (channel_name or "").strip()
    if not resolved_name:
        channel = db.get_channel(channel_id)
        resolved_name = channel.name if channel else ""

    round_record = db.create_channel_response_round(
        channel_id=channel_id,
        source_message_id=message_id,
    )
    payload: dict[str, Any] = {
        "content": content,
        "channel_id": channel_id,
        "round_id": round_record.id,
        "from_name": from_name,
        "author_type": author_type,
        "source_message_id": message_id,
        "channel_name": resolved_name,
    }
    if isinstance(from_agent, str) and from_agent.strip():
        payload["from_agent"] = from_agent

    triggers: list[dict[str, Any]] = []
    for agent_id in peer_ids:
        db.create_channel_response_candidate(round_id=round_record.id, agent_id=agent_id)
        triggers.append(
            {
                "agent_id": agent_id,
                "trigger_type": "channel_message",
                "source_channel": "channel",
                "payload": dict(payload),
            }
        )
    return triggers


def observe_channel_message(
    agent: Agent,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Mark one shared-channel message as observed without replying."""
    return observe_shared_message(agent, trigger, _CHANNEL_ROUNDS)


def begin_channel_response(
    agent: Agent,
    trigger: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Queue one shared-channel responder and report whether they can speak now."""
    return begin_shared_response(agent, trigger, _CHANNEL_ROUNDS)


def finalize_channel_response(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
) -> list[dict[str, Any]]:
    """Advance the shared channel reply queue after one responder finishes."""
    return finalize_shared_response(
        agent_id=agent_id,
        trigger=trigger,
        responded=responded,
        binding=_CHANNEL_ROUNDS,
    )
