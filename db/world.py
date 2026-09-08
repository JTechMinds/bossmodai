"""BossMod AI — World state and spatial queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.world.tilemap import get_room_at
from db.crud import query


def _as_utc(raw: Any) -> datetime | None:
    """Read an aggregate timestamp back as an aware UTC datetime.

    ``detect_types=PARSE_DECLTYPES`` converts a column whose declared type is
    TIMESTAMP, and an aggregate has no declared type — so ``MAX(created_at)``
    comes back as the stored string while every direct column on the same row
    is already a datetime. Converting here keeps one type on the payload;
    without it the rail would receive two shapes for the same kind of value.

    An unparseable timestamp raises rather than resolving to None: a stored
    value nobody can read is a bug in what wrote it, not a missing message.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        parsed = datetime.fromisoformat(str(raw))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_human_chat_times() -> dict[str, datetime]:
    """When each agent last exchanged a direct message with the operator.

    ONE query for the whole roster. The rail draws a timestamp on every person
    row, so the per-agent read `db.messages.get_human_chat_thread` offers would
    be an N+1 that grows with the company.

    Direct human chat only — the same pair of directions
    ``get_human_chat_thread`` reads. Channel posts live in their own table and
    agent-to-agent work never appears in the operator's conversation with
    anyone, so neither belongs in a "when did we last speak" column.

    @returns Agent id -> the latest message time. Agents who have never
        exchanged a message are simply absent, which the caller renders as
        nothing rather than as a fabricated date.
    """
    from core.models.message import HUMAN_SENDER_ID

    rows = query(
        """
        SELECT
            CASE WHEN from_agent = $1 THEN to_agent ELSE from_agent END AS agent_id,
            MAX(created_at) AS last_message_at
        FROM messages
        WHERE (from_agent = $1 AND to_agent IS NOT NULL)
           OR to_agent = $1
        GROUP BY agent_id
        """,
        [HUMAN_SENDER_ID],
    )
    return {
        str(row["agent_id"]): _as_utc(row["last_message_at"])
        for row in rows
        if row.get("agent_id") and row.get("last_message_at") is not None
    }


def get_world_state() -> list[dict[str, Any]]:
    """Return all agents joined with their state for WebSocket broadcast."""
    from core.world.seating import heal_desk_seats

    heal_desk_seats()
    rows = query(
        """
        SELECT
            a.id, a.name, a.role, a.description, a.done_fail_bar, a.color,
            a.desk_x, a.desk_y,
            s.x, s.y, s.status,
            s.last_active_at, s.idle_since,
            act.kind AS currentActivityKind,
            act.created_at AS currentActivitySince,
            act.task_id AS boundTaskId
        FROM agents a
        LEFT JOIN agent_state s ON s.agent_id = a.id
        LEFT JOIN activities act
            ON act.agent_id = a.id AND act.status = 'active'
        ORDER BY a.created_at
        """,
    )
    latest_chat = _latest_human_chat_times()
    for row in rows:
        room = get_room_at(int(row.get("x") or 0), int(row.get("y") or 0))
        row["location"] = room["name"] if room else "Unknown"
        # None for an agent nobody has spoken to. The rail shows nothing at
        # all for that, which is the honest answer; a placeholder date would
        # claim a conversation that never happened.
        row["lastMessageAt"] = latest_chat.get(row["id"])
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
