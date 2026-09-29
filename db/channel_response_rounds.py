"""BossMod AI — Shared channel response-round CRUD façade."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from core.models import ChannelResponseCandidate, ChannelResponseRound
from db.crud import query
import db.response_rounds as shared

logger = logging.getLogger(__name__)

_SCHEMA = shared.CHANNEL_RESPONSE_ROUNDS
_OPEN_CANDIDATE_STATUSES = frozenset({"pending", "queued", "responding"})


def create_channel_response_round(*, channel_id: str, source_message_id: str) -> ChannelResponseRound:
    """Create a new active response round for one shared channel message."""
    return shared.create_round(_SCHEMA, parent_id=channel_id, source_message_id=source_message_id)


def get_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Return one response round by id."""
    return shared.get_round(_SCHEMA, round_id)


def list_channel_response_rounds(
    channel_id: str,
    *,
    status: str | None = None,
) -> list[ChannelResponseRound]:
    """Return response rounds for one thread, newest first."""
    return shared.list_rounds_for_parent(_SCHEMA, channel_id, status=status)


def get_channel_response_round_for_source(
    *,
    channel_id: str,
    source_message_id: str,
) -> ChannelResponseRound | None:
    """Return the newest round stamped with one thread tip, if any."""
    return shared.get_round_for_source(
        _SCHEMA,
        parent_id=channel_id,
        source_message_id=source_message_id,
    )


def update_channel_response_round(
    round_id: str,
    *,
    status: str | None = None,
    completed_at: datetime | None = None,
) -> ChannelResponseRound | None:
    """Update one response round."""
    return shared.update_round(_SCHEMA, round_id, status=status, completed_at=completed_at)


def complete_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Mark one response round completed."""
    return shared.complete_round(_SCHEMA, round_id)


def create_channel_response_candidate(*, round_id: str, agent_id: str) -> ChannelResponseCandidate:
    """Insert one candidate row for an agent in a shared response round."""
    return shared.create_candidate(_SCHEMA, round_id=round_id, agent_id=agent_id)


def get_channel_response_candidate(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Return one agent's candidate row for a response round."""
    return shared.get_candidate(_SCHEMA, round_id=round_id, agent_id=agent_id)


def list_channel_response_candidates(round_id: str) -> list[ChannelResponseCandidate]:
    """Return all response candidates for a round in queue order."""
    return shared.list_candidates(_SCHEMA, round_id)


def update_channel_response_candidate(
    *,
    round_id: str,
    agent_id: str,
    status: str | None = None,
    queue_position: int | None = None,
    completed_at: datetime | None = None,
) -> ChannelResponseCandidate | None:
    """Update one response candidate."""
    return shared.update_candidate(
        _SCHEMA,
        round_id=round_id,
        agent_id=agent_id,
        status=status,
        queue_position=queue_position,
        completed_at=completed_at,
    )


def reserve_channel_response_slot(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Queue one candidate and assign the next response position."""
    return shared.reserve_slot(_SCHEMA, round_id=round_id, agent_id=agent_id)


def mark_channel_candidate_observed(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Mark one candidate as having read the message without replying."""
    return shared.mark_observed(_SCHEMA, round_id=round_id, agent_id=agent_id)


def mark_channel_candidate_responded(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Mark one candidate as having completed their queued response."""
    return shared.mark_responded(_SCHEMA, round_id=round_id, agent_id=agent_id)


def get_active_responding_channel_candidate(round_id: str) -> ChannelResponseCandidate | None:
    """Return the currently active responding candidate, if any."""
    return shared.get_active_responding(_SCHEMA, round_id)


def activate_next_channel_response_candidate(round_id: str) -> ChannelResponseCandidate | None:
    """Promote the earliest queued candidate into the active response slot."""
    return shared.activate_next_candidate(_SCHEMA, round_id)


def maybe_complete_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Complete the round once no pending, queued, or responding candidates remain."""
    return shared.maybe_complete_round(_SCHEMA, round_id)


def close_orphaned_channel_rounds(*, created_before: datetime) -> int:
    """Complete every active channel round that nothing can advance any more.

    A round advances only when a turn bound to it (payload ``round_id``)
    finishes. An active round with no ``queued`` or ``claimed`` trigger for
    it is dead: its open candidates are marked observed and the round is
    completed, so it no longer reads as a busy thread. Run once at worker
    start, after stale claims are requeued, so recovered triggers count as
    live.

    Args:
        created_before: Only rounds created before this instant are
            considered. Another process may have just created a round whose
            wake triggers it has not persisted yet; such a round is not dead.

    Returns:
        The number of rounds completed.
    """
    live_round_ids: set[str] = set()
    for row in query("SELECT payload FROM agent_triggers WHERE status IN ('queued', 'claimed')"):
        round_id = str(_payload(row.get("payload")).get("round_id") or "").strip()
        if round_id:
            live_round_ids.add(round_id)
    closed = 0
    for row in query(
        "SELECT id FROM channel_response_rounds "
        "WHERE status = 'active' AND datetime(created_at) < datetime($1)",
        [created_before],
    ):
        round_id = str(row["id"])
        if round_id in live_round_ids:
            continue
        for candidate in list_channel_response_candidates(round_id):
            if str(candidate.status or "") in _OPEN_CANDIDATE_STATUSES:
                mark_channel_candidate_observed(round_id=round_id, agent_id=candidate.agent_id)
        complete_channel_response_round(round_id)
        closed += 1
    if closed:
        logger.info("Closed %d orphaned channel response round(s) with no live trigger", closed)
    return closed


def _payload(raw: object) -> dict:
    """Decode one trigger payload. A payload that is not a JSON object names no round."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_channel_round_meta(round_id: str) -> dict:
    """Return round index, dispatch mode, step-outs, and reserved mentions."""
    meta = shared.channel_round_meta(round_id)
    if meta is None:
        return {
            "round_index": 1,
            "dispatch_mode": "fanout",
            "stepped_out": [],
            "next_mentions": [],
            "router_mode": "fallback",
            "pinned_ids": [],
            "work_binds": [],
            "work_bind_ids": [],
        }
    return meta


def set_channel_round_meta(
    round_id: str,
    *,
    round_index: int | None = None,
    dispatch_mode: str | None = None,
    stepped_out: list[str] | None = None,
    next_mentions: list[str] | None = None,
    router_mode: str | None = None,
    pinned_ids: list[str] | None = None,
    work_binds: list[dict[str, str]] | None = None,
) -> dict:
    """Persist channel round orchestration fields."""
    meta = shared.set_channel_round_meta(
        round_id,
        round_index=round_index,
        dispatch_mode=dispatch_mode,
        stepped_out=stepped_out,
        next_mentions=next_mentions,
        router_mode=router_mode,
        pinned_ids=pinned_ids,
        work_binds=work_binds,
    )
    return meta or get_channel_round_meta(round_id)
