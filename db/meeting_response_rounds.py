"""BossMod AI — Shared meeting response-round CRUD façade."""

from __future__ import annotations

from datetime import datetime

from core.models import MeetingResponseCandidate, MeetingResponseRound
import db.response_rounds as shared

_SCHEMA = shared.MEETING_RESPONSE_ROUNDS


def create_meeting_response_round(*, session_id: str, source_message_id: str) -> MeetingResponseRound:
    """Create a new active response round for one meeting message."""
    return shared.create_round(_SCHEMA, parent_id=session_id, source_message_id=source_message_id)


def get_meeting_response_round(round_id: str) -> MeetingResponseRound | None:
    """Return one response round by id."""
    return shared.get_round(_SCHEMA, round_id)


def update_meeting_response_round(
    round_id: str,
    *,
    status: str | None = None,
    completed_at: datetime | None = None,
) -> MeetingResponseRound | None:
    """Update one response round."""
    return shared.update_round(_SCHEMA, round_id, status=status, completed_at=completed_at)


def complete_meeting_response_round(round_id: str) -> MeetingResponseRound | None:
    """Mark one response round completed."""
    return shared.complete_round(_SCHEMA, round_id)


def create_meeting_response_candidate(*, round_id: str, agent_id: str) -> MeetingResponseCandidate:
    """Insert one candidate row for an agent in a response round."""
    return shared.create_candidate(_SCHEMA, round_id=round_id, agent_id=agent_id)


def get_meeting_response_candidate(*, round_id: str, agent_id: str) -> MeetingResponseCandidate | None:
    """Return one agent's candidate row for a response round."""
    return shared.get_candidate(_SCHEMA, round_id=round_id, agent_id=agent_id)


def list_meeting_response_candidates(round_id: str) -> list[MeetingResponseCandidate]:
    """Return all response candidates for a round in queue order."""
    return shared.list_candidates(_SCHEMA, round_id)


def update_meeting_response_candidate(
    *,
    round_id: str,
    agent_id: str,
    status: str | None = None,
    queue_position: int | None = None,
    completed_at: datetime | None = None,
) -> MeetingResponseCandidate | None:
    """Update one response candidate."""
    return shared.update_candidate(
        _SCHEMA,
        round_id=round_id,
        agent_id=agent_id,
        status=status,
        queue_position=queue_position,
        completed_at=completed_at,
    )


def reserve_response_slot(*, round_id: str, agent_id: str) -> MeetingResponseCandidate | None:
    """Queue one candidate and assign the next response position."""
    return shared.reserve_slot(_SCHEMA, round_id=round_id, agent_id=agent_id)


def mark_candidate_observed(*, round_id: str, agent_id: str) -> MeetingResponseCandidate | None:
    """Mark one candidate as having read the message without replying."""
    return shared.mark_observed(_SCHEMA, round_id=round_id, agent_id=agent_id)


def mark_candidate_responded(*, round_id: str, agent_id: str) -> MeetingResponseCandidate | None:
    """Mark one candidate as having completed their queued response."""
    return shared.mark_responded(_SCHEMA, round_id=round_id, agent_id=agent_id)


def get_active_responding_candidate(round_id: str) -> MeetingResponseCandidate | None:
    """Return the currently active responding candidate, if any."""
    return shared.get_active_responding(_SCHEMA, round_id)


def activate_next_response_candidate(round_id: str) -> MeetingResponseCandidate | None:
    """Promote the earliest queued candidate into the active response slot."""
    return shared.activate_next_candidate(_SCHEMA, round_id)


def maybe_complete_meeting_response_round(round_id: str) -> MeetingResponseRound | None:
    """Complete the round once no pending, queued, or responding candidates remain."""
    return shared.maybe_complete_round(_SCHEMA, round_id)


def delete_meeting_response_rounds(session_id: str) -> None:
    """Delete all response-round rows for one meeting session."""
    shared.delete_rounds_for_parent(_SCHEMA, session_id)
