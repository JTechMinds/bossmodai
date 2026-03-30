"""BossMod AI — Durable agent trigger queue CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from core.models import AgentTrigger
from db.crud import execute, fetch_one, insert_returning, query, query_one
from db.connection import get_connection

_TRIGGER_COLUMNS = (
    "id, agent_id, trigger_type, source_channel, payload, task_id, status, "
    "retry_count, failure_reason, claimed_at, completed_at, failed_at, created_at"
)

_TRIGGER_PRIORITY_CASE = """
CASE trigger_type
    WHEN 'human_chat' THEN 0
    WHEN 'cli_approval_resolved' THEN 1
    WHEN 'peer_message' THEN 2
    WHEN 'task_follow_up' THEN 3
    WHEN 'session_response' THEN 4
    WHEN 'channel_response' THEN 5
    WHEN 'session_message' THEN 6
    WHEN 'channel_message' THEN 7
    WHEN 'watchdog_status_ping' THEN 8
    WHEN 'task_assigned' THEN 9
    WHEN 'activity_resumed' THEN 9
    WHEN 'social' THEN 10
    ELSE 10
END
"""


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


def list_queued_triggers(limit: int = 100) -> list[AgentTrigger]:
    """Return queued triggers in dispatch order."""
    rows = query(
        f"""
        SELECT {_TRIGGER_COLUMNS}
        FROM agent_triggers
        WHERE status = 'queued'
        ORDER BY {_TRIGGER_PRIORITY_CASE}, created_at ASC, id ASC
        LIMIT $1
        """,
        [limit],
    )
    return [AgentTrigger.model_validate(row) for row in rows]


def delete_queued_triggers(
    agent_id: str,
    *,
    trigger_types: list[str] | None = None,
) -> int:
    """Delete queued triggers for an agent and return the number removed."""
    conditions = ["agent_id = $1", "status = 'queued'"]
    params: list[Any] = [agent_id]

    if trigger_types:
        placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(trigger_types)))
        conditions.append(f"trigger_type IN ({placeholders})")
        params.extend(trigger_types)

    row = query_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    deleted = int(row["cnt"]) if row else 0
    if deleted:
        execute(
            f"""
            DELETE FROM agent_triggers
            WHERE {' AND '.join(conditions)}
            """,
            params,
        )
    return deleted


def claim_trigger(trigger_id: str) -> AgentTrigger | None:
    """Claim a specific queued trigger."""
    con = get_connection()
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


def release_trigger(trigger_id: str) -> AgentTrigger | None:
    """Return a claimed trigger back to queued state."""
    return fetch_one(
        f"""
        UPDATE agent_triggers
        SET status = 'queued', claimed_at = NULL
        WHERE id = $1 AND status = 'claimed'
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [trigger_id],
        AgentTrigger,
    )


def retry_agent_trigger(trigger_id: str, reason: str) -> AgentTrigger | None:
    """Return a claimed trigger to the queue and increment its retry count."""
    return fetch_one(
        f"""
        UPDATE agent_triggers
        SET status = 'queued',
            retry_count = retry_count + 1,
            failure_reason = $1,
            claimed_at = NULL,
            failed_at = NULL
        WHERE id = $2 AND status IN ('claimed', 'queued')
        RETURNING {_TRIGGER_COLUMNS}
        """,
        [reason, trigger_id],
        AgentTrigger,
    )


def requeue_stale_triggers(claim_timeout_seconds: int) -> int:
    """Return stale claimed triggers to the queue."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=claim_timeout_seconds)
    row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM agent_triggers
        WHERE status = 'claimed' AND claimed_at < $1
        """,
        [cutoff],
    )
    count = int(row["cnt"]) if row else 0
    if count:
        execute(
            """
            UPDATE agent_triggers
            SET status = 'queued', claimed_at = NULL
            WHERE status = 'claimed' AND claimed_at < $1
            """,
            [cutoff],
        )
    return count


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
