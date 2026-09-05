"""BossMod AI — Shared meeting/channel response-round SQL.

Meeting and channel queues are the same state machine. They differ by table
name and parent foreign key (`session_id` vs `channel_id`). Domain façades in
`meeting_response_rounds` and `channel_response_rounds` bind those names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.models import (
    ChannelResponseCandidate,
    ChannelResponseRound,
    MeetingResponseCandidate,
    MeetingResponseRound,
)
from db.crud import execute, fetch_all, fetch_one, insert_returning, query_one

_CANDIDATE_COLUMNS = "id, round_id, agent_id, status, queue_position, created_at, updated_at, completed_at"


@dataclass(frozen=True, slots=True)
class ResponseRoundSchema:
    """Table and model binding for one shared-response queue family."""

    rounds_table: str
    candidates_table: str
    parent_fk: str
    round_model: type
    candidate_model: type

    @property
    def round_columns(self) -> str:
        return f"id, {self.parent_fk}, source_message_id, status, created_at, updated_at, completed_at"


MEETING_RESPONSE_ROUNDS = ResponseRoundSchema(
    rounds_table="meeting_response_rounds",
    candidates_table="meeting_response_candidates",
    parent_fk="session_id",
    round_model=MeetingResponseRound,
    candidate_model=MeetingResponseCandidate,
)

CHANNEL_RESPONSE_ROUNDS = ResponseRoundSchema(
    rounds_table="channel_response_rounds",
    candidates_table="channel_response_candidates",
    parent_fk="channel_id",
    round_model=ChannelResponseRound,
    candidate_model=ChannelResponseCandidate,
)


def create_round(
    schema: ResponseRoundSchema,
    *,
    parent_id: str,
    source_message_id: str,
) -> Any:
    """Create a new active response round for one shared message."""
    return insert_returning(
        f"""
        INSERT INTO {schema.rounds_table} ({schema.parent_fk}, source_message_id, status)
        VALUES ($1, $2, 'active')
        RETURNING {schema.round_columns}
        """,
        [parent_id, source_message_id],
        schema.round_model,
    )


def get_round(schema: ResponseRoundSchema, round_id: str) -> Any | None:
    """Return one response round by id."""
    return fetch_one(
        f"SELECT {schema.round_columns} FROM {schema.rounds_table} WHERE id = $1",
        [round_id],
        schema.round_model,
    )


def update_round(
    schema: ResponseRoundSchema,
    round_id: str,
    *,
    status: str | None = None,
    completed_at: datetime | None = None,
) -> Any | None:
    """Update one response round."""
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if status is not None:
        fields["status"] = status
    if completed_at is not None:
        fields["completed_at"] = completed_at
    if len(fields) == 1:
        return get_round(schema, round_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [round_id]
    execute(
        f"""
        UPDATE {schema.rounds_table}
        SET {assignments}
        WHERE id = ${len(params)}
        """,
        params,
    )
    return get_round(schema, round_id)


def complete_round(schema: ResponseRoundSchema, round_id: str) -> Any | None:
    """Mark one response round completed."""
    return update_round(
        schema,
        round_id,
        status="completed",
        completed_at=datetime.now(timezone.utc),
    )


def create_candidate(schema: ResponseRoundSchema, *, round_id: str, agent_id: str) -> Any:
    """Insert one candidate row for an agent in a response round."""
    return insert_returning(
        f"""
        INSERT INTO {schema.candidates_table} (round_id, agent_id, status)
        VALUES ($1, $2, 'pending')
        RETURNING {_CANDIDATE_COLUMNS}
        """,
        [round_id, agent_id],
        schema.candidate_model,
    )


def get_candidate(schema: ResponseRoundSchema, *, round_id: str, agent_id: str) -> Any | None:
    """Return one agent's candidate row for a response round."""
    return fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM {schema.candidates_table}
        WHERE round_id = $1 AND agent_id = $2
        """,
        [round_id, agent_id],
        schema.candidate_model,
    )


def list_candidates(schema: ResponseRoundSchema, round_id: str) -> list[Any]:
    """Return all response candidates for a round in queue order."""
    return fetch_all(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM {schema.candidates_table}
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
        schema.candidate_model,
    )


def update_candidate(
    schema: ResponseRoundSchema,
    *,
    round_id: str,
    agent_id: str,
    status: str | None = None,
    queue_position: int | None = None,
    completed_at: datetime | None = None,
) -> Any | None:
    """Update one response candidate."""
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if status is not None:
        fields["status"] = status
    if queue_position is not None:
        fields["queue_position"] = queue_position
    if completed_at is not None:
        fields["completed_at"] = completed_at
    if len(fields) == 1:
        return get_candidate(schema, round_id=round_id, agent_id=agent_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [round_id, agent_id]
    return fetch_one(
        f"""
        UPDATE {schema.candidates_table}
        SET {assignments}
        WHERE round_id = ${len(params) - 1} AND agent_id = ${len(params)}
        RETURNING {_CANDIDATE_COLUMNS}
        """,
        params,
        schema.candidate_model,
    )


def reserve_slot(schema: ResponseRoundSchema, *, round_id: str, agent_id: str) -> Any | None:
    """Queue one candidate and assign the next response position."""
    current = get_candidate(schema, round_id=round_id, agent_id=agent_id)
    if current is None or current.status != "pending":
        return current
    row = query_one(
        f"""
        SELECT COALESCE(MAX(queue_position), 0) AS max_pos
        FROM {schema.candidates_table}
        WHERE round_id = $1
        """,
        [round_id],
    )
    next_position = int(row["max_pos"]) + 1 if row else 1
    return update_candidate(
        schema,
        round_id=round_id,
        agent_id=agent_id,
        status="queued",
        queue_position=next_position,
    )


def mark_observed(schema: ResponseRoundSchema, *, round_id: str, agent_id: str) -> Any | None:
    """Mark one candidate as having read the message without replying."""
    return update_candidate(
        schema,
        round_id=round_id,
        agent_id=agent_id,
        status="observed",
        completed_at=datetime.now(timezone.utc),
    )


def mark_responded(schema: ResponseRoundSchema, *, round_id: str, agent_id: str) -> Any | None:
    """Mark one candidate as having completed their queued response."""
    return update_candidate(
        schema,
        round_id=round_id,
        agent_id=agent_id,
        status="responded",
        completed_at=datetime.now(timezone.utc),
    )


def get_active_responding(schema: ResponseRoundSchema, round_id: str) -> Any | None:
    """Return the currently active responding candidate, if any."""
    return fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM {schema.candidates_table}
        WHERE round_id = $1 AND status = 'responding'
        ORDER BY queue_position ASC NULLS LAST, created_at ASC
        LIMIT 1
        """,
        [round_id],
        schema.candidate_model,
    )


def activate_next_candidate(schema: ResponseRoundSchema, round_id: str) -> Any | None:
    """Promote the earliest queued candidate into the active response slot."""
    if get_active_responding(schema, round_id) is not None:
        return None
    next_candidate = fetch_one(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM {schema.candidates_table}
        WHERE round_id = $1 AND status = 'queued'
        ORDER BY queue_position ASC NULLS LAST, created_at ASC
        LIMIT 1
        """,
        [round_id],
        schema.candidate_model,
    )
    if next_candidate is None:
        return None
    return update_candidate(
        schema,
        round_id=round_id,
        agent_id=next_candidate.agent_id,
        status="responding",
    )


def maybe_complete_round(schema: ResponseRoundSchema, round_id: str) -> Any | None:
    """Complete the round once no pending, queued, or responding candidates remain."""
    row = query_one(
        f"""
        SELECT COUNT(*) AS open_count
        FROM {schema.candidates_table}
        WHERE round_id = $1 AND status IN ('pending', 'queued', 'responding')
        """,
        [round_id],
    )
    if row and int(row["open_count"]) == 0:
        return complete_round(schema, round_id)
    return get_round(schema, round_id)


def delete_rounds_for_parent(schema: ResponseRoundSchema, parent_id: str) -> None:
    """Delete all response-round rows for one parent session or channel."""
    execute(
        f"""
        DELETE FROM {schema.candidates_table}
        WHERE round_id IN (
            SELECT id FROM {schema.rounds_table} WHERE {schema.parent_fk} = $1
        )
        """,
        [parent_id],
    )
    execute(f"DELETE FROM {schema.rounds_table} WHERE {schema.parent_fk} = $1", [parent_id])
