"""BossMod AI — Activity feed log CRUD.

This module owns the durable UI-facing activity log. It is intentionally
separate from the runtime ``activities`` store, which tracks live agent state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db.connection import get_connection
from db.crud import insert_returning_dict, query


def create_activity_log_entry(
    event: str,
    detail: str,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Insert one activity-feed event and return it as a dict."""
    return insert_returning_dict(
        """
        INSERT INTO activity_log (event, detail, agent_name)
        VALUES ($1, $2, $3)
        RETURNING id, event, detail, agent_name, created_at
        """,
        [event, detail, agent_name],
    )


def get_recent_activity_log_entries(limit: int = 200) -> list[dict[str, Any]]:
    """Return recent activity-feed events, oldest first."""
    rows = query(
        """
        SELECT id, event, detail, agent_name, created_at
        FROM activity_log
        ORDER BY created_at DESC
        LIMIT $1
        """,
        [limit],
    )
    rows.reverse()
    return rows


def prune_activity_log(older_than: datetime) -> int:
    """Delete activity-feed events created before a cutoff.

    No table stores an ``activity_log`` id, so the delete leaves nothing
    pointing at a missing row.

    ``created_at`` is SQLite's ``current_timestamp`` text (UTC, whole
    seconds), so the cutoff is written the same way for the comparison.

    Args:
        older_than: The cutoff, an aware datetime.

    Returns:
        How many events were deleted.

    Raises:
        ValueError: ``older_than`` is naive, so its zone would be a guess.
    """
    if older_than.tzinfo is None:
        raise ValueError("prune_activity_log needs an aware datetime")
    cutoff = older_than.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cursor = get_connection().execute(
        "DELETE FROM activity_log WHERE created_at < $1",
        [cutoff],
    )
    return cursor.rowcount
