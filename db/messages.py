"""BossMod AI — Message CRUD."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import Message
from db.crud import execute, fetch_all, fetch_one, insert_returning, query, query_one

_MESSAGE_COLUMNS = (
    "id, from_agent, to_agent, content, message_type, floor_id, "
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
    floor_id: str | None = None,
) -> Message:
    """Insert a new message.

    ``floor_id`` is set only for agent↔agent messages: the floor the two
    agents shared at send time. Human↔agent DMs and work outputs leave it
    ``None``.
    """
    return insert_returning(
        f"""
        INSERT INTO messages (from_agent, to_agent, content, message_type,
                              location_x, location_y, token_count, floor_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [from_agent, to_agent, content, message_type, location_x, location_y, token_count, floor_id],
        Message,
    )


def get_message(message_id: str) -> Message | None:
    """Return one message by id, or ``None`` when no such row exists."""
    return fetch_one(
        f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = $1",
        [message_id],
        Message,
    )


def list_floor_peer_messages(
    floor_id: str,
    limit: int,
    before: tuple[datetime, str] | None = None,
) -> list[Message]:
    """Return one page of a floor's agent↔agent messages, newest first.

    Only rows stamped with ``floor_id`` at send time (or by the one-time
    backfill) are listed, so a conversation stays on the floor it happened
    on. Human↔agent rows are excluded even if one carried a floor.

    Args:
        floor_id: The floor whose conversations are listed.
        limit: Maximum rows returned. The caller asks for one more than a
            page to learn whether older rows exist.
        before: Keyset cursor ``(created_at, id)`` of the oldest row already
            shown; only strictly older rows (by ``created_at``, then ``id``)
            are returned. ``None`` returns the newest page.

    Returns:
        Messages ordered by ``created_at`` then ``id``, both descending.
    """
    from core.models.message import HUMAN_SENDER_ID

    conditions = [
        "floor_id = $1",
        "to_agent IS NOT NULL",
        "from_agent <> $2",
        "to_agent <> $2",
    ]
    params: list[Any] = [floor_id, HUMAN_SENDER_ID]
    if before is not None:
        before_at, before_id = before
        params.extend([before_at, before_id])
        # julianday() on both sides: stored defaults read "YYYY-MM-DD HH:MM:SS"
        # but a bound datetime is adapted with a "+00:00" suffix, so a raw
        # text comparison would rank the cursor row itself as older.
        at, mid = len(params) - 1, len(params)
        conditions.append(
            f"(julianday(created_at) < julianday(${at}) "
            f"OR (julianday(created_at) = julianday(${at}) AND id < ${mid}))"
        )
    params.append(limit)
    return fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC LIMIT ${len(params)}
        """,
        params,
        Message,
    )


def get_human_chat_thread(agent_id: str, limit: int = 50, earliest_ts: datetime | None = None) -> list[Message]:
    """Return the authored direct human <-> agent chat thread (oldest first).

    Ordered by ``(created_at, rowid)``: ``created_at`` has second precision,
    and ``rowid`` is insertion order, so rows written in the same second keep
    the order they were sent in. ``delete_human_chat_from`` cuts on the same
    order, so the transcript, the prompt and a rewind agree.
    """
    from core.models.message import HUMAN_SENDER_ID

    conditions = [
        "((from_agent = $1 AND to_agent = $2) OR (from_agent = $2 AND to_agent = $1))",
    ]
    params: list[Any] = [agent_id, HUMAN_SENDER_ID]
    if earliest_ts is not None:
        params.append(earliest_ts)
        conditions.append(f"created_at >= ${len(params)}")
    params.append(limit)
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, rowid DESC LIMIT ${len(params)}
        """,
        params,
        Message,
    )
    messages.reverse()
    return messages


def get_agent_direct_thread(
    agent_id: str,
    other_agent_id: str,
    limit: int = 50,
    earliest_ts: datetime | None = None,
) -> list[Message]:
    """Return the direct message thread between two agents (oldest first)."""
    conditions = [
        "((from_agent = $1 AND to_agent = $2) OR (from_agent = $2 AND to_agent = $1))",
    ]
    params: list[Any] = [agent_id, other_agent_id]
    if earliest_ts is not None:
        params.append(earliest_ts)
        conditions.append(f"created_at >= ${len(params)}")
    params.append(limit)
    messages = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS} FROM messages
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC LIMIT ${len(params)}
        """,
        params,
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


def delete_human_chat_from(agent_id: str, from_message_id: str) -> list[str]:
    """Delete one DM message and every later message of the same DM.

    This is the one definition of a rewind cut. "Later" is the thread's own
    order, ``(created_at, rowid)`` (see ``get_human_chat_thread``):
    ``julianday()`` compares the timestamps as instants rather than as text,
    as ``list_floor_peer_messages`` does, and ``rowid`` breaks a same-second
    tie in insertion order. Only rows of this agent's human DM pair are
    touched. Notifications live in their own table and are never cut.

    Must run inside the caller's ``transaction()``: the lookup and the delete
    are two statements, and the caller also removes the triggers and
    attachments that belong to the removed rows.

    Args:
        agent_id: The agent whose DM with the operator is rewound.
        from_message_id: The first message to remove.

    Returns:
        The ids of the removed messages, in no particular order.

    Raises:
        LookupError: ``from_message_id`` does not exist, or is not a message
            of this agent's DM with the operator. Nothing is deleted.
    """
    from core.models.message import HUMAN_SENDER_ID

    pair = "((from_agent = $1 AND to_agent = $2) OR (from_agent = $2 AND to_agent = $1))"
    cut = query_one(
        f"SELECT rowid AS cut_rowid, julianday(created_at) AS cut_day FROM messages WHERE id = $3 AND {pair}",
        [agent_id, HUMAN_SENDER_ID, from_message_id],
    )
    if cut is None:
        raise LookupError(
            f"Message {from_message_id!r} is not in the DM between {agent_id!r} and the operator"
        )
    rows = query(
        f"""
        DELETE FROM messages
        WHERE {pair}
          AND (julianday(created_at) > $3
               OR (julianday(created_at) = $3 AND rowid >= $4))
        RETURNING id
        """,
        [agent_id, HUMAN_SENDER_ID, cut["cut_day"], cut["cut_rowid"]],
    )
    return [str(row["id"]) for row in rows]


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
    *,
    human_label: str,
) -> list[dict[str, Any]]:
    """Fetch messages with resolved sender names.

    Shared helper for chat and direct-message thread rendering.

    Args:
        messages: Rows to render, oldest first.
        human_label: Name shown on the human's rows. The caller owns the
            presentation (``core.boss.boss_label()`` for model prompts).
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
