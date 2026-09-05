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
