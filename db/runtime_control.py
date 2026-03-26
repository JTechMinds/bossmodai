"""BossMod AI — Durable runtime control-plane CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.models import RuntimeCommand, RuntimeWorkerState
from db.connection import get_connection
from db.crud import execute, fetch_one, insert_returning, query, query_one

_DEFAULT_WORKER_NAME = "primary"

_COMMAND_COLUMNS = (
    "id, command_type, payload, status, failure_reason, "
    "claimed_at, completed_at, failed_at, created_at"
)

_WORKER_COLUMNS = (
    "worker_name, pid, lifecycle_state, last_heartbeat_at, "
    "started_at, stopped_at, last_error, updated_at"
)


def create_runtime_command(
    command_type: str,
    payload: dict[str, Any] | None = None,
) -> RuntimeCommand:
    """Insert a queued runtime control command."""
    return insert_returning(
        f"""
        INSERT INTO runtime_commands (command_type, payload)
        VALUES ($1, $2)
        RETURNING {_COMMAND_COLUMNS}
        """,
        [command_type, json.dumps(payload or {})],
        RuntimeCommand,
    )


def list_queued_runtime_commands(limit: int = 100) -> list[RuntimeCommand]:
    """Return queued runtime commands in FIFO order."""
    rows = query(
        f"""
        SELECT {_COMMAND_COLUMNS}
        FROM runtime_commands
        WHERE status = 'queued'
        ORDER BY created_at ASC, id ASC
        LIMIT $1
        """,
        [limit],
    )
    return [RuntimeCommand.model_validate(row) for row in rows]


def claim_runtime_command(command_id: str) -> RuntimeCommand | None:
    """Claim a queued runtime command."""
    con = get_connection()
    now = datetime.now(timezone.utc)
    result = con.execute(
        f"""
        UPDATE runtime_commands
        SET status = 'claimed', claimed_at = $1
        WHERE id = $2 AND status = 'queued'
        RETURNING {_COMMAND_COLUMNS}
        """,
        [now, command_id],
    )
    row = result.fetchone()
    if row is None:
        return None
    return RuntimeCommand.model_validate({col[0]: val for col, val in zip(result.description, row)})


def complete_runtime_command(command_id: str) -> RuntimeCommand | None:
    """Mark a claimed runtime command completed."""
    return fetch_one(
        f"""
        UPDATE runtime_commands
        SET status = 'completed', completed_at = $1
        WHERE id = $2
        RETURNING {_COMMAND_COLUMNS}
        """,
        [datetime.now(timezone.utc), command_id],
        RuntimeCommand,
    )


def fail_runtime_command(command_id: str, reason: str) -> RuntimeCommand | None:
    """Mark a runtime command failed."""
    return fetch_one(
        f"""
        UPDATE runtime_commands
        SET status = 'failed', failure_reason = $1, failed_at = $2
        WHERE id = $3
        RETURNING {_COMMAND_COLUMNS}
        """,
        [reason, datetime.now(timezone.utc), command_id],
        RuntimeCommand,
    )


def get_runtime_command(command_id: str) -> RuntimeCommand | None:
    """Fetch a runtime command by id."""
    return fetch_one(
        f"SELECT {_COMMAND_COLUMNS} FROM runtime_commands WHERE id = $1",
        [command_id],
        RuntimeCommand,
    )


def has_open_runtime_command(command_types: list[str] | None = None) -> bool:
    """Return whether queued/claimed runtime commands exist."""
    conditions = ["status IN ('queued', 'claimed')"]
    params: list[Any] = []
    if command_types:
        placeholders = ", ".join(f"${index + 1}" for index in range(len(command_types)))
        conditions.append(f"command_type IN ({placeholders})")
        params.extend(command_types)
    row = query_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM runtime_commands
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    return bool(row and int(row["cnt"]) > 0)


def delete_open_runtime_commands(command_types: list[str] | None = None) -> int:
    """Delete queued/claimed runtime commands and return rows removed."""
    conditions = ["status IN ('queued', 'claimed')"]
    params: list[Any] = []
    if command_types:
        placeholders = ", ".join(f"${index + 1}" for index in range(len(command_types)))
        conditions.append(f"command_type IN ({placeholders})")
        params.extend(command_types)
    row = query_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM runtime_commands
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    count = int(row["cnt"]) if row else 0
    if count:
        execute(
            f"""
            DELETE FROM runtime_commands
            WHERE {' AND '.join(conditions)}
            """,
            params,
        )
    return count


def get_runtime_worker_state(worker_name: str = _DEFAULT_WORKER_NAME) -> RuntimeWorkerState | None:
    """Fetch the persisted runtime worker state row."""
    return fetch_one(
        f"SELECT {_WORKER_COLUMNS} FROM runtime_worker_state WHERE worker_name = $1",
        [worker_name],
        RuntimeWorkerState,
    )


def mark_runtime_worker_starting(
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
) -> RuntimeWorkerState:
    """Mark the runtime worker as starting."""
    now = datetime.now(timezone.utc)
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state="starting",
        pid=pid,
        heartbeat_at=now,
        started_at=now,
        stopped_at=None,
        last_error=None,
        preserve_started_at=False,
    )


def mark_runtime_worker_running(
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
) -> RuntimeWorkerState:
    """Mark the runtime worker as running."""
    now = datetime.now(timezone.utc)
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state="running",
        pid=pid,
        heartbeat_at=now,
        started_at=now,
        stopped_at=None,
        last_error=None,
        preserve_started_at=True,
    )


def mark_runtime_worker_stopping(
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
) -> RuntimeWorkerState:
    """Mark the runtime worker as stopping."""
    now = datetime.now(timezone.utc)
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state="stopping",
        pid=pid,
        heartbeat_at=now,
        started_at=None,
        stopped_at=None,
        last_error=None,
        preserve_started_at=True,
    )


def mark_runtime_worker_stopped(
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
    last_error: str | None = None,
) -> RuntimeWorkerState:
    """Mark the runtime worker as stopped."""
    now = datetime.now(timezone.utc)
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state="stopped",
        pid=pid,
        heartbeat_at=now,
        started_at=None,
        stopped_at=now,
        last_error=last_error,
        preserve_started_at=True,
        clear_pid=True,
    )


def mark_runtime_worker_error(
    error: str,
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
) -> RuntimeWorkerState:
    """Mark the runtime worker as failed."""
    now = datetime.now(timezone.utc)
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state="error",
        pid=pid,
        heartbeat_at=now,
        started_at=None,
        stopped_at=now,
        last_error=error,
        preserve_started_at=True,
        clear_pid=True,
    )


def record_runtime_worker_heartbeat(
    pid: int | None = None,
    *,
    worker_name: str = _DEFAULT_WORKER_NAME,
) -> RuntimeWorkerState:
    """Persist a heartbeat for the active worker."""
    now = datetime.now(timezone.utc)
    existing = get_runtime_worker_state(worker_name)
    lifecycle_state = existing.lifecycle_state if existing is not None else "running"
    started_at = existing.started_at if existing is not None else now
    return _upsert_runtime_worker_state(
        worker_name=worker_name,
        lifecycle_state=lifecycle_state,
        pid=pid,
        heartbeat_at=now,
        started_at=started_at,
        stopped_at=None if lifecycle_state != "stopped" else existing.stopped_at if existing else None,
        last_error=existing.last_error if existing is not None else None,
        preserve_started_at=False,
        clear_pid=pid is None and existing is not None and existing.pid is None,
    )


def _upsert_runtime_worker_state(
    *,
    worker_name: str,
    lifecycle_state: str,
    pid: int | None,
    heartbeat_at: datetime | None,
    started_at: datetime | None,
    stopped_at: datetime | None,
    last_error: str | None,
    preserve_started_at: bool,
    clear_pid: bool = False,
) -> RuntimeWorkerState:
    now = datetime.now(timezone.utc)
    pid_value = None if clear_pid else pid
    pid_sql = "NULL" if clear_pid else "COALESCE(excluded.pid, runtime_worker_state.pid)"
    started_sql = (
        "COALESCE(runtime_worker_state.started_at, excluded.started_at)"
        if preserve_started_at
        else "excluded.started_at"
    )
    return fetch_one(
        f"""
        INSERT INTO runtime_worker_state (
            worker_name,
            pid,
            lifecycle_state,
            last_heartbeat_at,
            started_at,
            stopped_at,
            last_error,
            updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(worker_name) DO UPDATE SET
            pid = {pid_sql},
            lifecycle_state = excluded.lifecycle_state,
            last_heartbeat_at = COALESCE(excluded.last_heartbeat_at, runtime_worker_state.last_heartbeat_at),
            started_at = {started_sql},
            stopped_at = excluded.stopped_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        RETURNING {_WORKER_COLUMNS}
        """,
        [worker_name, pid_value, lifecycle_state, heartbeat_at, started_at, stopped_at, last_error, now],
        RuntimeWorkerState,
    )
