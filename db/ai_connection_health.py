"""BossMod AI — AI connection health rows (``ai_connection_health``).

Storage only: reading, writing and deleting one row per connection. When a
connection is paused, what counts as a failure and how long it waits are the
policy in ``core/llm/connection_health.py``, which runs every read-modify-write
here inside one ``db.connection.transaction()``.
"""

from __future__ import annotations

from core.models import ConnectionHealth
from db.crud import execute, fetch_all, fetch_one, query

_COLUMNS = (
    "connection_id, failure_count, failure_kind, last_error, "
    "suspended_at, retry_at, backoff_seconds, updated_at"
)


def get_connection_health(connection_id: str) -> ConnectionHealth | None:
    """Return one connection's health row.

    Args:
        connection_id: The ``ai_connections`` id.

    Returns:
        The row, or ``None`` when the connection has no recorded failures
        (healthy) or does not exist.
    """
    return fetch_one(
        f"SELECT {_COLUMNS} FROM ai_connection_health WHERE connection_id = $1",
        [connection_id],
        ConnectionHealth,
    )


def list_suspended_connection_health() -> list[ConnectionHealth]:
    """Return every paused connection's row (``suspended_at`` set), oldest pause first."""
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM ai_connection_health
        WHERE suspended_at IS NOT NULL
        ORDER BY suspended_at
        """,
        model_cls=ConnectionHealth,
    )


def save_connection_health(health: ConnectionHealth) -> bool:
    """Insert or replace one connection's health row; ``updated_at`` is set to now.

    The insert only happens while the connection exists, so a model call that
    raced the connection's deletion records nothing instead of raising a
    foreign-key error inside that call. That is intended: a deleted
    connection has nothing left to protect.

    Args:
        health: The complete row to store. Its ``updated_at`` is ignored.

    Returns:
        ``True`` when the row was written; ``False`` when the connection no
        longer exists.
    """
    written = query(
        f"""
        INSERT INTO ai_connection_health ({_COLUMNS})
        SELECT $1, $2, $3, $4, $5, $6, $7, current_timestamp
        WHERE EXISTS (SELECT 1 FROM ai_connections WHERE id = $1)
        ON CONFLICT(connection_id) DO UPDATE SET
            failure_count = excluded.failure_count,
            failure_kind = excluded.failure_kind,
            last_error = excluded.last_error,
            suspended_at = excluded.suspended_at,
            retry_at = excluded.retry_at,
            backoff_seconds = excluded.backoff_seconds,
            updated_at = current_timestamp
        RETURNING connection_id
        """,
        [
            health.connection_id,
            health.failure_count,
            health.failure_kind,
            health.last_error,
            health.suspended_at,
            health.retry_at,
            health.backoff_seconds,
        ],
    )
    return bool(written)


def delete_connection_health(connection_id: str) -> None:
    """Delete one connection's health row; a connection with none is left as it is."""
    execute("DELETE FROM ai_connection_health WHERE connection_id = $1", [connection_id])
