"""BossMod AI — Message CRUD."""

from __future__ import annotations

from typing import Any

from core.models import Message
from db.crud import execute, fetch_all, insert_returning, query_one

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


def get_human_chat_thread(agent_id: str, limit: int = 50) -> list[Message]:
    """Return the direct human <-> agent chat thread (oldest first)."""
    from core.models.message import HUMAN_SENDER_ID

    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE (from_agent = $1 AND to_agent = $2)
           OR (from_agent = $2 AND to_agent = $1)
        ORDER BY created_at DESC LIMIT $3
        """,
        [agent_id, HUMAN_SENDER_ID, limit],
        Message,
    )
    messages.reverse()
    return messages


def get_agent_direct_thread(
    agent_id: str,
    other_agent_id: str,
    limit: int = 50,
) -> list[Message]:
    """Return the direct message thread between two agents (oldest first)."""
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE (from_agent = $1 AND to_agent = $2)
           OR (from_agent = $2 AND to_agent = $1)
        ORDER BY created_at DESC LIMIT $3
        """,
        [agent_id, other_agent_id, limit],
        Message,
    )
    messages.reverse()
    return messages


def get_recent_work_artifacts(agent_id: str, limit: int = 10) -> list[Message]:
    """Return recent durable work outputs authored by the agent."""
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE from_agent = $1
          AND message_type = 'work'
          AND to_agent IS NULL
        ORDER BY created_at DESC LIMIT $2
        """,
        [agent_id, limit],
        Message,
    )
    messages.reverse()
    return messages


def get_recent_authored_messages(agent_id: str, limit: int = 20) -> list[Message]:
    """Return recent messages authored by the agent across all channels."""
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE from_agent = $1
        ORDER BY created_at DESC LIMIT $2
        """,
        [agent_id, limit],
        Message,
    )
    messages.reverse()
    return messages


def delete_human_chat_thread(agent_id: str) -> int:
    """Delete the direct human <-> agent chat thread and return rows removed."""
    from core.models.message import HUMAN_SENDER_ID

    count_row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM messages
        WHERE (from_agent = $1 AND to_agent = $2)
           OR (from_agent = $2 AND to_agent = $1)
        """,
        [agent_id, HUMAN_SENDER_ID],
    )
    count = int(count_row["cnt"]) if count_row else 0
    execute(
        """
        DELETE FROM messages
        WHERE (from_agent = $1 AND to_agent = $2)
           OR (from_agent = $2 AND to_agent = $1)
        """,
        [agent_id, HUMAN_SENDER_ID],
    )
    return count


def get_recent_completed_tasks(agent_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return recent archived task summaries for recall context."""
    from db.crud import query

    return query(
        """
        SELECT
            id, title, project, status, completion_summary, status_note,
            last_activity, created_at
        FROM tasks
        WHERE assigned_to = $1
          AND status IN ('complete', 'blocked', 'abandoned', 'stalled', 'delegated', 'declined')
        ORDER BY last_activity DESC, created_at DESC
        LIMIT $2
        """,
        [agent_id, limit],
    )


def get_formatted_messages(
    messages: list[Message],
    human_label: str = "Human Operator",
) -> list[dict[str, Any]]:
    """Fetch messages with resolved sender names.

    Shared helper for chat and direct-message thread rendering.
    """
    from core.models.message import HUMAN_SENDER_ID
    from db.agents import get_agents_by_ids

    sender_ids = list({m.from_agent for m in messages if m.from_agent != HUMAN_SENDER_ID})
    agents_map = get_agents_by_ids(sender_ids)

    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.from_agent == HUMAN_SENDER_ID:
            from_name = human_label
        else:
            sender = agents_map.get(msg.from_agent)
            from_name = sender.name if sender else "Unknown"
        result.append({
            "id": msg.id,
            "from_agent": msg.from_agent,
            "from_name": from_name,
            "to_agent": msg.to_agent,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        })
    return result
