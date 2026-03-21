"""BossMod AI — Message CRUD."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import Message
from db.crud import fetch_all, insert_returning

_MESSAGE_COLUMNS = (
    "id, from_agent, to_agent, content, message_type, "
    "location_x, location_y, token_count, created_at"
)


def create_message(
    from_agent: str,
    to_agent: str | None,
    content: str,
    message_type: str = "work",
    location_x: int = 0,
    location_y: int = 0,
    token_count: int = 0,
) -> Message:
    """Insert a new message."""
    return insert_returning(
        f"""
        INSERT INTO messages (from_agent, to_agent, content, message_type,
                              location_x, location_y, token_count)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [from_agent, to_agent, content, message_type, location_x, location_y, token_count],
        Message,
    )


def get_messages_for_agent(agent_id: str, limit: int = 50) -> list[Message]:
    """Return recent messages sent to or from an agent (oldest first)."""
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE from_agent = $1 OR to_agent = $1 OR to_agent IS NULL
        ORDER BY created_at DESC LIMIT $2
        """,
        [agent_id, limit],
        Message,
    )
    messages.reverse()
    return messages


def get_unread_messages(
    agent_id: str,
    since: datetime | None = None,
) -> list[Message]:
    """Return messages sent to an agent since a given timestamp."""
    if since:
        return fetch_all(
            f"""
            SELECT {_MESSAGE_COLUMNS} FROM messages
            WHERE to_agent = $1 AND created_at > $2
            ORDER BY created_at
            """,
            [agent_id, since],
            Message,
        )
    return fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE to_agent = $1
        ORDER BY created_at
        """,
        [agent_id],
        Message,
    )
