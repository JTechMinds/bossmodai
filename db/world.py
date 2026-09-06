"""BossMod AI — World state and spatial queries."""

from __future__ import annotations

from typing import Any

from core.world.tilemap import get_room_at
from db.crud import query


def get_world_state() -> list[dict[str, Any]]:
    """Return all agents joined with their state for WebSocket broadcast."""
    rows = query(
        """
        SELECT
            a.id, a.name, a.role, a.description, a.done_fail_bar, a.color,
            a.desk_x, a.desk_y,
            s.x, s.y, s.status,
            s.last_active_at, s.idle_since,
            act.kind AS currentActivityKind,
            act.task_id AS boundTaskId
        FROM agents a
        LEFT JOIN agent_state s ON s.agent_id = a.id
        LEFT JOIN activities act
            ON act.agent_id = a.id AND act.status = 'active'
        ORDER BY a.created_at
        """,
    )
    for row in rows:
        room = get_room_at(int(row.get("x") or 0), int(row.get("y") or 0))
        row["location"] = room["name"] if room else "Unknown"
    return rows


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
