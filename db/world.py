"""BossMod AI — World state and spatial queries."""

from __future__ import annotations

from typing import Any

from db.crud import query


def get_world_state() -> list[dict[str, Any]]:
    """Return all agents joined with their state for WebSocket broadcast."""
    return query(
        """
        SELECT
            a.id, a.name, a.role, a.color,
            a.desk_x, a.desk_y,
            s.x, s.y, s.status,
            s.last_active_at, s.idle_since,
            act.kind AS current_activity_kind,
            act.task_id AS bound_task_id
        FROM agents a
        LEFT JOIN agent_state s ON s.agent_id = a.id
        LEFT JOIN activities act
            ON act.agent_id = a.id AND act.status = 'active'
        ORDER BY a.created_at
        """,
    )


def get_nearby_agents(
    agent_id: str,
    x: int,
    y: int,
    radius: int,
) -> list[dict[str, Any]]:
    """Return agents within Manhattan distance of (x, y), excluding the given agent."""
    return query(
        """
        SELECT a.id, a.name, a.role
        FROM agents a
        JOIN agent_state s ON s.agent_id = a.id
        WHERE a.id != $1
          AND (ABS(s.x - $2) + ABS(s.y - $3)) <= $4
        ORDER BY a.name
        """,
        [agent_id, x, y, radius],
    )
