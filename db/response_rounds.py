"""BossMod AI — Shared meeting/channel response-round SQL.

Meeting and channel queues are the same state machine. They differ by table
name and parent foreign key (`session_id` vs `channel_id`). Domain façades in
`meeting_response_rounds` and `channel_response_rounds` bind those names.
"""

from __future__ import annotations

import json
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


def list_rounds_for_parent(
    schema: ResponseRoundSchema,
    parent_id: str,
    *,
    status: str | None = None,
) -> list[Any]:
    """Return response rounds for one parent, newest first."""
    token = (parent_id or "").strip()
    if not token:
        return []
    conditions = [f"{schema.parent_fk} = $1"]
    params: list[Any] = [token]
    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    return fetch_all(
        f"""
        SELECT {schema.round_columns}
        FROM {schema.rounds_table}
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        """,
        params,
        schema.round_model,
    )


def get_round_for_source(
    schema: ResponseRoundSchema,
    *,
    parent_id: str,
    source_message_id: str,
) -> Any | None:
    """Return the newest round stamped with one source message, if any."""
    parent_token = (parent_id or "").strip()
    source_token = (source_message_id or "").strip()
    if not parent_token or not source_token:
        return None
    return fetch_one(
        f"""
        SELECT {schema.round_columns}
        FROM {schema.rounds_table}
        WHERE {schema.parent_fk} = $1 AND source_message_id = $2
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        [parent_token, source_token],
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


def _normalize_binds(pairs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep one pair per agent. A later pair with a task id replaces an empty one."""
    found: list[dict[str, str]] = []
    index: dict[str, int] = {}
    for pair in pairs:
        agent_id = str(pair.get("agent_id") or "").strip()
        task_id = str(pair.get("task_id") or "").strip()
        if not agent_id:
            continue
        item = {"agent_id": agent_id, "task_id": task_id}
        if agent_id in index:
            if task_id:
                found[index[agent_id]] = item
            continue
        index[agent_id] = len(found)
        found.append(item)
    return found


def _json_binds(raw: Any) -> list[dict[str, str]]:
    """Parse stored work binds. A bare id list has no task id yet."""
    if isinstance(raw, list):
        parsed = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    if not isinstance(parsed, list):
        return []
    pairs: list[dict[str, str]] = []
    for item in parsed:
        if isinstance(item, str):
            pairs.append({"agent_id": item, "task_id": ""})
        elif isinstance(item, dict):
            pairs.append(
                {
                    "agent_id": str(item.get("agent_id") or ""),
                    "task_id": str(item.get("task_id") or ""),
                }
            )
    return _normalize_binds(pairs)


def _json_id_list(raw: Any) -> list[str]:
    """Parse a JSON list of agent ids. Bad payloads become an empty list."""
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def channel_round_meta(round_id: str) -> dict[str, Any] | None:
    """Return channel-only round orchestration fields.

    Meeting rounds do not use these columns. Missing rows return None.
    ``dispatch_mode`` defaults to the legacy fan-out queue when unset.
    """
    token = (round_id or "").strip()
    if not token:
        return None
    row = query_one(
        """
        SELECT round_index, dispatch_mode, stepped_out, next_mentions,
               router_mode, pinned_ids, work_bind_ids
        FROM channel_response_rounds
        WHERE id = $1
        """,
        [token],
    )
    if row is None:
        return None
    mode = str(row.get("dispatch_mode") or "").strip() or "fanout"
    try:
        index = int(row.get("round_index") or 1)
    except (TypeError, ValueError):
        index = 1
    router_mode = str(row.get("router_mode") or "").strip() or "fallback"
    if router_mode not in {"system", "fallback"}:
        router_mode = "fallback"
    binds = _json_binds(row.get("work_bind_ids"))
    return {
        "round_index": index if index > 0 else 1,
        "dispatch_mode": mode,
        "stepped_out": _json_id_list(row.get("stepped_out")),
        "next_mentions": _json_id_list(row.get("next_mentions")),
        "router_mode": router_mode,
        "pinned_ids": _json_id_list(row.get("pinned_ids")),
        "work_binds": binds,
        "work_bind_ids": [pair["agent_id"] for pair in binds],
    }


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
) -> dict[str, Any] | None:
    """Update channel round orchestration fields. Omitted fields stay put."""
    token = (round_id or "").strip()
    if not token:
        return None
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if round_index is not None:
        fields["round_index"] = round_index
    if dispatch_mode is not None:
        fields["dispatch_mode"] = dispatch_mode
    if stepped_out is not None:
        fields["stepped_out"] = json.dumps(list(stepped_out))
    if next_mentions is not None:
        fields["next_mentions"] = json.dumps(list(next_mentions))
    if router_mode is not None:
        fields["router_mode"] = router_mode
    if pinned_ids is not None:
        fields["pinned_ids"] = json.dumps(list(pinned_ids))
    if work_binds is not None:
        fields["work_bind_ids"] = json.dumps(_normalize_binds(work_binds))
    if len(fields) == 1:
        return channel_round_meta(token)
    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [token]
    execute(
        f"""
        UPDATE channel_response_rounds
        SET {assignments}
        WHERE id = ${len(params)}
        """,
        params,
    )
    return channel_round_meta(token)


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
