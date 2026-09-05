"""BossMod AI — Meeting orchestration helpers (invites + context packets)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.crud import execute, insert_returning_dict, query, query_one


_META_COLUMNS = (
    "session_id, host_agent_id, meeting_mode, phase, context_packet_id, kickoff_round_id, created_at, updated_at"
)
_CONTEXT_COLUMNS = "id, session_id, summary, payload_json, created_at"


def create_meeting_context_packet(*, session_id: str, summary: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one immutable meeting context packet."""
    now = datetime.now(timezone.utc)
    return insert_returning_dict(
        f"""
        INSERT INTO meeting_context_packets (session_id, summary, payload_json, created_at)
        VALUES ($1, $2, $3, $4)
        RETURNING {_CONTEXT_COLUMNS}
        """,
        [session_id, summary.strip(), json.dumps(payload, default=str), now],
    )


def get_meeting_context_packet(packet_id: str) -> dict[str, Any] | None:
    """Return one meeting context packet by id."""
    return query_one(
        f"SELECT {_CONTEXT_COLUMNS} FROM meeting_context_packets WHERE id = $1",
        [packet_id],
    )


def get_meeting_session_meta(session_id: str) -> dict[str, Any] | None:
    """Return the orchestration meta row for a meeting session."""
    return query_one(
        f"SELECT {_META_COLUMNS} FROM meeting_session_meta WHERE session_id = $1",
        [session_id],
    )


def upsert_meeting_session_meta(
    *,
    session_id: str,
    host_agent_id: str,
    meeting_mode: str,
    phase: str,
    context_packet_id: str | None = None,
) -> dict[str, Any]:
    """Create or update the orchestration meta row for a meeting session."""
    now = datetime.now(timezone.utc)
    execute(
        """
        INSERT INTO meeting_session_meta (
            session_id,
            host_agent_id,
            meeting_mode,
            phase,
            context_packet_id,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $6)
        ON CONFLICT(session_id)
        DO UPDATE SET
            host_agent_id = excluded.host_agent_id,
            meeting_mode = excluded.meeting_mode,
            phase = excluded.phase,
            context_packet_id = COALESCE(excluded.context_packet_id, meeting_session_meta.context_packet_id),
            updated_at = excluded.updated_at
        """,
        [session_id, host_agent_id, meeting_mode, phase, context_packet_id, now],
    )
    row = get_meeting_session_meta(session_id)
    if row is None:
        raise RuntimeError("Failed to upsert meeting session meta row")
    return row


def update_meeting_session_meta(
    session_id: str,
    *,
    phase: str | None = None,
    context_packet_id: str | None = None,
    kickoff_round_id: str | None = None,
) -> dict[str, Any] | None:
    """Update one meeting session meta row."""
    now = datetime.now(timezone.utc)
    fields: dict[str, object] = {"updated_at": now}
    if phase is not None:
        fields["phase"] = phase
    if context_packet_id is not None:
        fields["context_packet_id"] = context_packet_id
    if kickoff_round_id is not None:
        fields["kickoff_round_id"] = kickoff_round_id

    if len(fields) == 1:
        return get_meeting_session_meta(session_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [session_id]
    execute(
        f"""
        UPDATE meeting_session_meta
        SET {assignments}
        WHERE session_id = ${len(params)}
        """,
        params,
    )
    return get_meeting_session_meta(session_id)


def list_meeting_session_participants(session_id: str) -> list[dict[str, Any]]:
    """Return participants for one meeting session."""
    return query(
        """
        SELECT session_id, agent_id, required, state, reason, invited_at, responded_at, arrived_at, last_pinged_at, updated_at
        FROM meeting_session_participants
        WHERE session_id = $1
        ORDER BY updated_at ASC
        """,
        [session_id],
    )


def get_meeting_session_participant(session_id: str, agent_id: str) -> dict[str, Any] | None:
    """Return one participant row."""
    return query_one(
        """
        SELECT session_id, agent_id, required, state, reason, invited_at, responded_at, arrived_at, last_pinged_at, updated_at
        FROM meeting_session_participants
        WHERE session_id = $1 AND agent_id = $2
        """,
        [session_id, agent_id],
    )


def upsert_meeting_session_participant(
    *,
    session_id: str,
    agent_id: str,
    state: str,
    required: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create or update a participant row, preserving first invited_at by default."""
    now = datetime.now(timezone.utc)
    execute(
        """
        INSERT INTO meeting_session_participants (
            session_id,
            agent_id,
            required,
            state,
            reason,
            invited_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $6)
        ON CONFLICT(session_id, agent_id)
        DO UPDATE SET
            required = excluded.required,
            state = excluded.state,
            reason = COALESCE(excluded.reason, meeting_session_participants.reason),
            updated_at = excluded.updated_at
        """,
        [session_id, agent_id, 1 if required else 0, state, reason, now],
    )
    row = get_meeting_session_participant(session_id, agent_id)
    if row is None:
        raise RuntimeError("Failed to upsert meeting participant row")
    return row


def update_meeting_session_participant_state(
    *,
    session_id: str,
    agent_id: str,
    state: str,
    reason: str | None = None,
    responded_at: datetime | None = None,
    arrived_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Update state + timestamps for one participant row."""
    now = datetime.now(timezone.utc)
    fields: dict[str, object] = {"state": state, "updated_at": now}
    if reason is not None:
        fields["reason"] = reason
    if responded_at is not None:
        fields["responded_at"] = responded_at
    if arrived_at is not None:
        fields["arrived_at"] = arrived_at

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [session_id, agent_id]
    execute(
        f"""
        UPDATE meeting_session_participants
        SET {assignments}
        WHERE session_id = ${len(params) - 1} AND agent_id = ${len(params)}
        """,
        params,
    )
    return get_meeting_session_participant(session_id, agent_id)


def mark_meeting_participant_pinged(*, session_id: str, agent_id: str) -> None:
    """Record that the host pinged a participant about this meeting."""
    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE meeting_session_participants
        SET last_pinged_at = $1, updated_at = $1
        WHERE session_id = $2 AND agent_id = $3
        """,
        [now, session_id, agent_id],
    )


def meeting_all_required_accounted_for(session_id: str) -> bool:
    """Return whether all required participants are arrived/declined/timed_out."""
    rows = query(
        """
        SELECT state
        FROM meeting_session_participants
        WHERE session_id = $1 AND required = 1
        """,
        [session_id],
    )
    if not rows:
        return False
    accounted = {"arrived", "declined", "timed_out"}
    return all(str(row.get("state") or "") in accounted for row in rows)


def list_meeting_participant_details(session_id: str) -> list[dict[str, Any]]:
    """Return participant rows enriched with agent names."""
    return query(
        """
        SELECT
            p.session_id,
            p.agent_id,
            a.name,
            a.role,
            p.required,
            p.state,
            p.reason,
            p.invited_at,
            p.responded_at,
            p.arrived_at,
            p.last_pinged_at,
            p.updated_at
        FROM meeting_session_participants p
        JOIN agents a ON a.id = p.agent_id
        WHERE p.session_id = $1
        ORDER BY a.name ASC
        """,
        [session_id],
    )
