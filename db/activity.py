"""BossMod AI — Activity log CRUD."""

from __future__ import annotations

from typing import Any

from db.crud import insert_returning_dict, query


def create_activity(
    event: str,
    detail: str,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Insert an activity event and return it as a dict."""
    return insert_returning_dict(
        """
        INSERT INTO activity_log (event, detail, agent_name)
        VALUES ($1, $2, $3)
        RETURNING id, event, detail, agent_name, created_at
        """,
        [event, detail, agent_name],
    )


def get_recent_activity(limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent activity events, oldest first."""
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
