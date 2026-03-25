"""BossMod AI — Shared channel response-round CRUD."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models import ChannelResponseCandidate, ChannelResponseRound
from db.crud import execute, fetch_all, fetch_one, insert_returning, query_one

_ROUND_COLUMNS = "id, channel_id, source_message_id, status, created_at, updated_at, completed_at"
_CANDIDATE_COLUMNS = "id, round_id, agent_id, status, queue_position, created_at, updated_at, completed_at"


def create_channel_response_round(*, channel_id: str, source_message_id: str) -> ChannelResponseRound:
    """Create a new active response round for one shared channel message."""
    return insert_returning(
        f"""
        INSERT INTO channel_response_rounds (channel_id, source_message_id, status)
        VALUES ($1, $2, 'active')
        RETURNING {_ROUND_COLUMNS}
        """,
        [channel_id, source_message_id],
        ChannelResponseRound,
    )


def get_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Return one response round by id."""
    return fetch_one(
        f"SELECT {_ROUND_COLUMNS} FROM channel_response_rounds WHERE id = $1",
        [round_id],
        ChannelResponseRound,
    )


def update_channel_response_round(
    round_id: str,
    *,
    status: str | None = None,
    completed_at: datetime | None = None,
) -> ChannelResponseRound | None:
    """Update one response round."""
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if status is not None:
        fields["status"] = status
    if completed_at is not None:
        fields["completed_at"] = completed_at
    if len(fields) == 1:
        return get_channel_response_round(round_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [round_id]
    execute(
        f"""
        UPDATE channel_response_rounds
        SET {assignments}
        WHERE id = ${len(params)}
        """,
        params,
    )
    return get_channel_response_round(round_id)


def complete_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Mark one response round completed."""
    return update_channel_response_round(
        round_id,
        status="completed",
        completed_at=datetime.now(timezone.utc),
    )


def create_channel_response_candidate(*, round_id: str, agent_id: str) -> ChannelResponseCandidate:
    """Insert one candidate row for an agent in a shared response round."""
    return insert_returning(
        f"""
        INSERT INTO channel_response_candidates (round_id, agent_id, status)
        VALUES ($1, $2, 'pending')
        RETURNING {_CANDIDATE_COLUMNS}
        """,
        [round_id, agent_id],
        ChannelResponseCandidate,
    )


def get_channel_response_candidate(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Return one agent's candidate row for a response round."""
    return fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM channel_response_candidates
        WHERE round_id = $1 AND agent_id = $2
        """,
        [round_id, agent_id],
        ChannelResponseCandidate,
    )


def list_channel_response_candidates(round_id: str) -> list[ChannelResponseCandidate]:
    """Return all response candidates for a round in queue order."""
    return fetch_all(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM channel_response_candidates
        WHERE round_id = $1
        ORDER BY
            CASE status
                WHEN 'responding' THEN 0
                WHEN 'queued' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'responded' THEN 3
                WHEN 'observed' THEN 4
                ELSE 9
            END,
            queue_position ASC NULLS LAST,
            created_at ASC
        """,
        [round_id],
        ChannelResponseCandidate,
    )


def update_channel_response_candidate(
    *,
    round_id: str,
    agent_id: str,
    status: str | None = None,
    queue_position: int | None = None,
    completed_at: datetime | None = None,
) -> ChannelResponseCandidate | None:
    """Update one response candidate."""
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if status is not None:
        fields["status"] = status
    if queue_position is not None:
        fields["queue_position"] = queue_position
    if completed_at is not None:
        fields["completed_at"] = completed_at
    if len(fields) == 1:
        return get_channel_response_candidate(round_id=round_id, agent_id=agent_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [round_id, agent_id]
    return fetch_one(
        f"""
        UPDATE channel_response_candidates
        SET {assignments}
        WHERE round_id = ${len(params) - 1} AND agent_id = ${len(params)}
        RETURNING {_CANDIDATE_COLUMNS}
        """,
        params,
        ChannelResponseCandidate,
    )


def reserve_channel_response_slot(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Queue one candidate and assign the next response position."""
    current = get_channel_response_candidate(round_id=round_id, agent_id=agent_id)
    if current is None or current.status != "pending":
        return current
    row = query_one(
        """
        SELECT COALESCE(MAX(queue_position), 0) AS max_pos
        FROM channel_response_candidates
        WHERE round_id = $1
        """,
        [round_id],
    )
    next_position = int(row["max_pos"]) + 1 if row else 1
    return update_channel_response_candidate(
        round_id=round_id,
        agent_id=agent_id,
        status="queued",
        queue_position=next_position,
    )


def mark_channel_candidate_observed(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Mark one candidate as having read the message without replying."""
    return update_channel_response_candidate(
        round_id=round_id,
        agent_id=agent_id,
        status="observed",
        completed_at=datetime.now(timezone.utc),
    )


def mark_channel_candidate_responded(*, round_id: str, agent_id: str) -> ChannelResponseCandidate | None:
    """Mark one candidate as having completed their queued response."""
    return update_channel_response_candidate(
        round_id=round_id,
        agent_id=agent_id,
        status="responded",
        completed_at=datetime.now(timezone.utc),
    )


def get_active_responding_channel_candidate(round_id: str) -> ChannelResponseCandidate | None:
    """Return the currently active responding candidate, if any."""
    return fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM channel_response_candidates
        WHERE round_id = $1 AND status = 'responding'
        ORDER BY queue_position ASC NULLS LAST, created_at ASC
        LIMIT 1
        """,
        [round_id],
        ChannelResponseCandidate,
    )


def activate_next_channel_response_candidate(round_id: str) -> ChannelResponseCandidate | None:
    """Promote the earliest queued candidate into the active response slot."""
    if get_active_responding_channel_candidate(round_id) is not None:
        return None
    next_candidate = fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM channel_response_candidates
        WHERE round_id = $1 AND status = 'queued'
        ORDER BY queue_position ASC NULLS LAST, created_at ASC
        LIMIT 1
        """,
        [round_id],
        ChannelResponseCandidate,
    )
    if next_candidate is None:
        return None
    return update_channel_response_candidate(
        round_id=round_id,
        agent_id=next_candidate.agent_id,
        status="responding",
    )


def maybe_complete_channel_response_round(round_id: str) -> ChannelResponseRound | None:
    """Complete the round once no pending, queued, or responding candidates remain."""
    row = query_one(
        """
        SELECT COUNT(*) AS open_count
        FROM channel_response_candidates
        WHERE round_id = $1 AND status IN ('pending', 'queued', 'responding')
        """,
        [round_id],
    )
    if row and int(row["open_count"]) == 0:
        return complete_channel_response_round(round_id)
    return get_channel_response_round(round_id)
