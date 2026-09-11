"""BossMod AI — Shared channel CRUD and transcript helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import (
    Channel,
    ChannelArchivedError,
    ChannelMember,
    ChannelMessage,
    THREAD_STALE_SKIP_KIND,
    THREAD_STALE_SKIP_LINE,
)
from db.connection import transaction
from db.crud import execute, fetch_all, fetch_one, insert_returning, query, query_one

_CHANNEL_COLUMNS = "id, name, kind, status, created_by, created_at, updated_at, archived_at"
_MEMBER_COLUMNS = "channel_id, agent_id, created_at"
_MESSAGE_COLUMNS = (
    "id, channel_id, author_type, author_agent_id, author_name, content, "
    "source_channel, notification_kind, consent_id, created_at"
)


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


def find_active_channel_for_members(member_agent_ids: list[str]) -> Channel | None:
    """Return the newest active thread whose roster matches these agents exactly."""
    unique_members = list(dict.fromkeys(agent_id for agent_id in member_agent_ids if agent_id))
    if not unique_members:
        return None
    placeholders = ", ".join(f"${index + 2}" for index in range(len(unique_members)))
    return fetch_one(
        f"""
        SELECT {_CHANNEL_COLUMNS}
        FROM channels
        WHERE status = 'active'
          AND id IN (
            SELECT cm.channel_id
            FROM channel_members cm
            GROUP BY cm.channel_id
            HAVING COUNT(*) = $1
               AND SUM(CASE WHEN cm.agent_id IN ({placeholders}) THEN 1 ELSE 0 END) = $1
          )
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        [len(unique_members), *unique_members],
        Channel,
    )


def is_channel_archived(channel_id: str | None) -> bool:
    """Return True when this id points at a sealed archived thread."""
    token = (channel_id or "").strip()
    if not token:
        return False
    existing = get_channel(token)
    return existing is not None and existing.status == "archived"


def payload_targets_archived_channel(payload: Any) -> bool:
    """Return True when a trigger payload is bound to a sealed archived thread."""
    if not isinstance(payload, dict):
        return False
    raw = payload.get("channel_id")
    if not isinstance(raw, str) or not raw.strip():
        return False
    return is_channel_archived(raw.strip())


def archive_channel(channel_id: str) -> Channel | None:
    """Soft-delete one thread so it leaves the active Threads list."""
    existing = get_channel(channel_id)
    if existing is None:
        return None
    if existing.status == "archived":
        return existing
    return update_channel(
        channel_id,
        status="archived",
        archived_at=datetime.now(timezone.utc),
    )


def reopen_channel(channel_id: str) -> Channel | None:
    """Restore an archived thread to active and unseal writes."""
    existing = get_channel(channel_id)
    if existing is None:
        return None
    if existing.status != "archived":
        return existing
    return fetch_one(
        f"""
        UPDATE channels
        SET status = 'active', archived_at = NULL, updated_at = $1
        WHERE id = $2
        RETURNING {_CHANNEL_COLUMNS}
        """,
        [datetime.now(timezone.utc), channel_id],
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
    notification_kind: str | None = None,
    consent_id: str | None = None,
) -> ChannelMessage:
    """Append one message to the shared channel transcript."""
    if is_channel_archived(channel_id):
        raise ChannelArchivedError(channel_id)
    message = insert_returning(
        f"""
        INSERT INTO channel_messages (
            channel_id, author_type, author_agent_id, author_name, content,
            source_channel, notification_kind, consent_id, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [
            channel_id,
            author_type,
            author_agent_id,
            author_name,
            content,
            source_channel,
            notification_kind,
            consent_id,
            datetime.now(timezone.utc),
        ],
        ChannelMessage,
    )
    return message


def find_queue_visibility_channel_message(
    *,
    agent_id: str,
    channel_id: str | None = None,
) -> ChannelMessage | None:
    """Return this agent's live queue-visibility line on a channel, if any."""
    conditions = ["notification_kind = 'queue_visibility'", "author_agent_id = $1"]
    params: list[Any] = [agent_id]
    if channel_id:
        params.append(channel_id)
        conditions.append(f"channel_id = ${len(params)}")
    return fetch_one(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        params,
        ChannelMessage,
    )


def update_channel_message_content(message_id: str, content: str) -> ChannelMessage | None:
    """Replace the text of one channel message and return the updated row."""
    return fetch_one(
        f"""
        UPDATE channel_messages
        SET content = $1
        WHERE id = $2
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [content, message_id],
        ChannelMessage,
    )


def delete_channel_message(message_id: str) -> ChannelMessage | None:
    """Delete one channel message and return the removed row."""
    return fetch_one(
        f"""
        DELETE FROM channel_messages
        WHERE id = $1
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [message_id],
        ChannelMessage,
    )


def list_queue_visibility_channel_messages(agent_id: str) -> list[ChannelMessage]:
    """Return every live queue-visibility line this agent posted on a channel."""
    return fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages
        WHERE notification_kind = 'queue_visibility' AND author_agent_id = $1
        ORDER BY created_at DESC, id DESC
        """,
        [agent_id],
        ChannelMessage,
    )


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


def get_channel_message(message_id: str) -> ChannelMessage | None:
    """Return one channel message by id."""
    token = (message_id or "").strip()
    if not token:
        return None
    return fetch_one(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages
        WHERE id = $1
        """,
        [token],
        ChannelMessage,
    )


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


def get_later_human_channel_message(
    channel_id: str,
    after_message_id: str,
) -> ChannelMessage | None:
    """Return the newest human message posted after one transcript row.

    Order is ``created_at`` then SQLite ``rowid`` so two humans in the same
    second still count as a tip move. Agent and system lines do not move the
    human tip — peers can still answer the same wake.
    """
    channel_token = (channel_id or "").strip()
    after_token = (after_message_id or "").strip()
    if not channel_token or not after_token:
        return None
    return fetch_one(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages AS newer
        WHERE newer.channel_id = $1
          AND newer.author_type = 'human'
          AND newer.id != $2
          AND EXISTS (
              SELECT 1
              FROM channel_messages AS wake
              WHERE wake.id = $2
                AND (
                    newer.created_at > wake.created_at
                    OR (newer.created_at = wake.created_at AND newer.rowid > wake.rowid)
                )
          )
        ORDER BY newer.created_at DESC, newer.rowid DESC
        LIMIT 1
        """,
        [channel_token, after_token],
        ChannelMessage,
    )


def find_thread_skip_line_after(
    *,
    channel_id: str,
    after_message_id: str,
) -> ChannelMessage | None:
    """Return the collapsed stale-skip system line posted after one tip, if any."""
    channel_token = (channel_id or "").strip()
    after_token = (after_message_id or "").strip()
    if not channel_token or not after_token:
        return None
    return fetch_one(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM channel_messages AS skip
        WHERE skip.channel_id = $1
          AND skip.author_type = 'system'
          AND skip.notification_kind = $3
          AND skip.content = $4
          AND EXISTS (
              SELECT 1
              FROM channel_messages AS tip
              WHERE tip.id = $2
                AND (
                    skip.created_at > tip.created_at
                    OR (skip.created_at = tip.created_at AND skip.rowid > tip.rowid)
                )
          )
        ORDER BY skip.created_at ASC, skip.rowid ASC
        LIMIT 1
        """,
        [channel_token, after_token, THREAD_STALE_SKIP_KIND, THREAD_STALE_SKIP_LINE],
        ChannelMessage,
    )
