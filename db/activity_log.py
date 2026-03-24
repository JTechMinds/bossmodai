"""BossMod AI — Activity feed log CRUD.

This module owns the durable UI-facing activity log. It is intentionally
separate from the runtime ``activities`` store, which tracks live agent state.
"""

from __future__ import annotations

from typing import Any

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
