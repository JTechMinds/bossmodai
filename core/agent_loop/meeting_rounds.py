"""BossMod AI — Shared meeting response-round coordination."""

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

_MEETING_ROUNDS = SharedRoundBinding(
    parent_key="session_id",
    label="meeting",
    queue_fail_label="shared response queue",
    trigger_type="session_response",
    source_channel="chat",
    extra_payload_key="meeting_title",
    mark_observed=db.mark_candidate_observed,
    mark_responded=db.mark_candidate_responded,
    reserve=db.reserve_response_slot,
    activate_next=db.activate_next_response_candidate,
    maybe_complete=db.maybe_complete_meeting_response_round,
)


def observe_session_message(
    agent: Agent,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Mark one shared meeting message as observed without replying."""
    return observe_shared_message(agent, trigger, _MEETING_ROUNDS)


def begin_session_response(
    agent: Agent,
    trigger: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Queue one shared-meeting responder and report whether they can speak now."""
    return begin_shared_response(agent, trigger, _MEETING_ROUNDS)


def finalize_session_response(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
) -> list[dict[str, Any]]:
    """Advance the response queue after one shared-session reply turn ends."""
    return finalize_shared_response(
        agent_id=agent_id,
        trigger=trigger,
        responded=responded,
        binding=_MEETING_ROUNDS,
    )
