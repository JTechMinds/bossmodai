"""BossMod AI — Shared channel CRUD and transcript helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import Channel, ChannelMember, ChannelMessage
from db.connection import transaction
from db.crud import execute, fetch_all, fetch_one, insert_returning, query, query_one

_CHANNEL_COLUMNS = "id, name, kind, status, created_by, created_at, updated_at, archived_at"
_MEMBER_COLUMNS = "channel_id, agent_id, created_at"
_MESSAGE_COLUMNS = "id, channel_id, author_type, author_agent_id, author_name, content, source_channel, created_at"


def create_channel(
    *,
    name: str,
    member_agent_ids: list[str],
    created_by: str | None = None,
    kind: str = "manual",
) -> Channel:
    """Create one active shared channel and attach its initial members."""
    unique_members = list(dict.fromkeys(agent_id for agent_id in member_agent_ids if agent_id))
    if not unique_members:
        raise ValueError("At least one channel member is required")

    with transaction():
        channel = insert_returning(
            f"""
            INSERT INTO channels (name, kind, status, created_by)
            VALUES ($1, $2, 'active', $3)
            RETURNING {_CHANNEL_COLUMNS}
            """,
            [name, kind, created_by],
            Channel,
        )
        for agent_id in unique_members:
            execute(
                """
                INSERT INTO channel_members (channel_id, agent_id)
                VALUES ($1, $2)
                """,
                [channel.id, agent_id],
            )
    return channel


def get_channel(channel_id: str) -> Channel | None:
    """Return one channel by id."""
    return fetch_one(
        f"SELECT {_CHANNEL_COLUMNS} FROM channels WHERE id = $1",
        [channel_id],
        Channel,
    )


def update_channel(
    channel_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    archived_at: datetime | None = None,
    touch: bool = True,
) -> Channel | None:
    """Update one channel's metadata."""
    fields: dict[str, object] = {}
    if name is not None:
        fields["name"] = name
    if status is not None:
        fields["status"] = status
    if archived_at is not None:
        fields["archived_at"] = archived_at
    if touch:
        fields["updated_at"] = datetime.now(timezone.utc)
    if not fields:
        return get_channel(channel_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [channel_id]
    return fetch_one(
        f"""
        UPDATE channels
        SET {assignments}
        WHERE id = ${len(params)}
        RETURNING {_CHANNEL_COLUMNS}
        """,
        params,
        Channel,
    )


def list_channels(*, status: str = "active") -> list[Channel]:
    """Return shared channels ordered by recent activity."""
    return fetch_all(
        f"""
        SELECT {_CHANNEL_COLUMNS}
        FROM channels
        WHERE status = $1
        ORDER BY COALESCE(
            (
                SELECT MAX(cm.created_at)
                FROM channel_messages cm
                WHERE cm.channel_id = channels.id
            ),
            updated_at,
            created_at
        ) DESC,
        created_at DESC
        """,
        [status],
        Channel,
    )


def list_channel_members(channel_id: str) -> list[ChannelMember]:
    """Return channel memberships."""
    return fetch_all(
        f"""
        SELECT {_MEMBER_COLUMNS}
        FROM channel_members
        WHERE channel_id = $1
        ORDER BY created_at ASC
        """,
        [channel_id],
        ChannelMember,
    )


def list_channel_member_details(channel_id: str) -> list[dict[str, Any]]:
    """Return channel members joined to agent identity and runtime state."""
    return query(
        """
        SELECT
            a.id,
            a.name,
            a.role,
            a.color,
            s.status,
            s.x,
            s.y,
            act.kind AS currentActivityKind
        FROM channel_members cm
        JOIN agents a ON a.id = cm.agent_id
        LEFT JOIN agent_state s ON s.agent_id = a.id
        LEFT JOIN activities act ON act.agent_id = a.id AND act.status = 'active'
        WHERE cm.channel_id = $1
        ORDER BY a.name
        """,
        [channel_id],
    )


def add_channel_members(channel_id: str, agent_ids: list[str]) -> int:
    """Add new agents to a shared channel."""
    unique_members = list(dict.fromkeys(agent_id for agent_id in agent_ids if agent_id))
    added = 0
    if not unique_members:
        return added
    with transaction():
        for agent_id in unique_members:
            exists = query_one(
                """
                SELECT 1
                FROM channel_members
                WHERE channel_id = $1 AND agent_id = $2
                """,
                [channel_id, agent_id],
            )
            if exists:
                continue
            execute(
                """
                INSERT INTO channel_members (channel_id, agent_id)
                VALUES ($1, $2)
                """,
                [channel_id, agent_id],
            )
            added += 1
    return added


def create_channel_message(
    *,
    channel_id: str,
    author_type: str,
    author_name: str,
    content: str,
    source_channel: str,
    author_agent_id: str | None = None,
) -> ChannelMessage:
    """Append one message to the shared channel transcript."""
    message = insert_returning(
        f"""
        INSERT INTO channel_messages (
            channel_id, author_type, author_agent_id, author_name, content, source_channel, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [
            channel_id,
            author_type,
            author_agent_id,
            author_name,
            content,
            source_channel,
            datetime.now(timezone.utc),
        ],
        ChannelMessage,
    )
    return message


def list_channel_messages(channel_id: str, *, limit: int = 80) -> list[ChannelMessage]:
    """Return recent channel transcript entries, oldest first."""
    rows = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages
        WHERE channel_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        [channel_id, limit],
        ChannelMessage,
    )
    rows.reverse()
    return rows


def get_formatted_channel_messages(channel_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """Return channel transcript rows in prompt-history shape."""
    return [
        {
            "id": item.id,
            "from_agent": item.author_agent_id or ("__human__" if item.author_type == "human" else "__system__"),
            "from_name": item.author_name,
            "to_agent": None,
            "content": item.content,
            "message_type": "channel",
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "channel_id": channel_id,
        }
        for item in list_channel_messages(channel_id, limit=limit)
    ]


def get_latest_channel_message(channel_id: str) -> ChannelMessage | None:
    """Return the newest channel message, if any."""
    return fetch_one(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages
        WHERE channel_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        [channel_id],
        ChannelMessage,
    )
