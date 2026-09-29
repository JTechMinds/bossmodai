"""BossMod AI — the last wake check of each (extension, agent) pair (manifest ``wake``).

The runtime worker's wake service writes one row per pair after every real
check; the app reads it for the agent's desk. The two processes share only
the database, so this table is how mailbox health crosses between them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db.crud import execute, query_one


def record_wake_check(
    extension_id: str,
    agent_id: str,
    *,
    ok: bool,
    error: str | None,
    new_count: int,
) -> None:
    """Upsert one pair's latest check.

    ``last_new_at`` / ``last_new_count`` change only when ``new_count > 0``,
    so the desk keeps saying when mail last arrived.

    Args:
        extension_id: The extension id.
        agent_id: An existing agent's id (FK).
        ok: Whether the check succeeded.
        error: The operator-facing sentence on failure; ``None`` on success.
        new_count: How many new items the check delivered (0 on failure).

    Raises:
        ValueError: ``ok`` and ``error`` disagree, or ``new_count`` is negative.
        sqlite3.IntegrityError: ``agent_id`` names no agent.
    """
    if ok == (error is not None):
        raise ValueError("a successful check has no error and a failed one has one")
    if new_count < 0:
        raise ValueError("new_count cannot be negative")
    now = datetime.now(timezone.utc)
    new_at = now if new_count > 0 else None
    execute(
        """
        INSERT INTO extension_wake_status
            (extension_id, agent_id, checked_at, ok, error, last_new_at, last_new_count)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT(extension_id, agent_id) DO UPDATE SET
            checked_at = excluded.checked_at,
            ok = excluded.ok,
            error = excluded.error,
            last_new_at = COALESCE(excluded.last_new_at, extension_wake_status.last_new_at),
            last_new_count = COALESCE(excluded.last_new_count, extension_wake_status.last_new_count)
        """,
        [extension_id, agent_id, now, ok, error, new_at, new_count if new_count > 0 else None],
    )


def get_wake_status(extension_id: str, agent_id: str) -> dict[str, Any] | None:
    """Return one pair's latest check.

    Returns:
        ``{checked_at, ok, error, last_new_at, last_new_count}`` (datetimes
        in UTC), or ``None`` before the first check.
    """
    return query_one(
        """
        SELECT checked_at, ok, error, last_new_at, last_new_count
        FROM extension_wake_status
        WHERE extension_id = $1 AND agent_id = $2
        """,
        [extension_id, agent_id],
    )
