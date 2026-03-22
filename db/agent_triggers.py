"""BossMod AI — Durable agent trigger queue CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.models import AgentTrigger
from db.crud import execute, fetch_one, insert_returning, query, query_one
from db.connection import get_connection

_TRIGGER_COLUMNS = (
    "id, agent_id, trigger_type, source_channel, payload, task_id, status, "
    "failure_reason, claimed_at, completed_at, failed_at, created_at"
)


def create_agent_trigger(
    agent_id: str,
    trigger_type: str,
    source_channel: str,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> AgentTrigger:
    """Insert a queued trigger for an agent."""
    return insert_returning(
        f"""
        INSERT INTO agent_triggers (agent_id, trigger_type, source_channel, payload, task_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [agent_id, trigger_type, source_channel, json.dumps(payload), task_id],
        AgentTrigger,
    )


def claim_next_trigger(excluded_agent_ids: list[str] | None = None) -> AgentTrigger | None:
    """Claim the oldest queued trigger whose agent is not currently active."""
    con = get_connection()
    excluded_agent_ids = excluded_agent_ids or []

    where = ["status = 'queued'"]
    params: list[Any] = []

    if excluded_agent_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(excluded_agent_ids)))
        where.append(f"agent_id NOT IN ({placeholders})")
        params.extend(excluded_agent_ids)

    row = con.execute(
        f"""
        SELECT id
        FROM agent_triggers
        WHERE {' AND '.join(where)}
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return None

    trigger_id = row[0]
    now = datetime.now(timezone.utc)
    result = con.execute(
        f"""
        UPDATE agent_triggers
        SET status = 'claimed', claimed_at = $1
        WHERE id = $2 AND status = 'queued'
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [now, trigger_id],
    )
    claimed = result.fetchone()
    if claimed is None:
        return None
    return AgentTrigger.model_validate({col[0]: val for col, val in zip(result.description, claimed)})


def complete_agent_trigger(trigger_id: str) -> AgentTrigger | None:
    """Mark a claimed trigger completed."""
    return fetch_one(
        f"""
        UPDATE agent_triggers
        SET status = 'completed', completed_at = $1
        WHERE id = $2
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [datetime.now(timezone.utc), trigger_id],
        AgentTrigger,
    )


def fail_agent_trigger(trigger_id: str, reason: str) -> AgentTrigger | None:
    """Mark a trigger failed with a reason."""
    return fetch_one(
        f"""
        UPDATE agent_triggers
        SET status = 'failed', failure_reason = $1, failed_at = $2
        WHERE id = $3
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [reason, datetime.now(timezone.utc), trigger_id],
        AgentTrigger,
    )


def get_agent_trigger(trigger_id: str) -> AgentTrigger | None:
    """Return a single trigger by id."""
    return fetch_one(
        f"SELECT {_TRIGGER_COLUMNS} FROM agent_triggers WHERE id = $1",
        [trigger_id],
        AgentTrigger,
    )


def count_queued_triggers(agent_id: str) -> int:
    """Count queued or claimed triggers for an agent."""
    row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE agent_id = $1 AND status IN ('queued', 'claimed')
        """,
        [agent_id],
    )
    return int(row["cnt"]) if row else 0


def has_open_trigger(agent_id: str) -> bool:
    """Return whether the agent has any queued or claimed trigger."""
    return count_queued_triggers(agent_id) > 0


def has_open_trigger_matching(
    agent_id: str,
    *,
    trigger_types: list[str] | None = None,
    task_id: str | None = None,
) -> bool:
    """Return whether a matching queued/claimed trigger already exists."""
    conditions = ["agent_id = $1", "status IN ('queued', 'claimed')"]
    params: list[Any] = [agent_id]

    if trigger_types:
        placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(trigger_types)))
        conditions.append(f"trigger_type IN ({placeholders})")
        params.extend(trigger_types)

    if task_id is not None:
        params.append(task_id)
        conditions.append(f"task_id = ${len(params)}")

    row = query_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    return bool(row and int(row["cnt"]) > 0)


def has_queued_trigger_matching(
    agent_id: str,
    *,
    trigger_types: list[str] | None = None,
    task_id: str | None = None,
) -> bool:
    """Return whether a matching queued trigger already exists."""
    conditions = ["agent_id = $1", "status = 'queued'"]
    params: list[Any] = [agent_id]

    if trigger_types:
        placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(trigger_types)))
        conditions.append(f"trigger_type IN ({placeholders})")
        params.extend(trigger_types)

    if task_id is not None:
        params.append(task_id)
        conditions.append(f"task_id = ${len(params)}")

    row = query_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    return bool(row and int(row["cnt"]) > 0)


def list_agent_triggers(
    agent_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent triggers for an agent."""
    conditions = ["agent_id = $1"]
    params: list[Any] = [agent_id]
    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    params.append(limit)
    return query(
        f"""
        SELECT {_TRIGGER_COLUMNS}
        FROM agent_triggers
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(params)}
        """,
        params,
    )


def get_latest_trigger(
    agent_id: str,
    trigger_type: str,
    since: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the latest trigger of a given type for an agent."""
    params: list[Any] = [agent_id, trigger_type]
    where = ["agent_id = $1", "trigger_type = $2"]
    if since is not None:
        params.append(since)
        where.append(f"created_at >= ${len(params)}")
    return query_one(
        f"""
        SELECT {_TRIGGER_COLUMNS}
        FROM agent_triggers
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        params,
    )


def delete_open_triggers(agent_id: str) -> int:
    """Delete queued or claimed triggers for an agent and return rows removed."""
    row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE agent_id = $1 AND status IN ('queued', 'claimed')
        """,
        [agent_id],
    )
    count = int(row["cnt"]) if row else 0
    execute(
        """
        DELETE FROM agent_triggers
        WHERE agent_id = $1 AND status IN ('queued', 'claimed')
        """,
        [agent_id],
    )
    return count
