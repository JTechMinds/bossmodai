"""BossMod AI — First-class notification storage."""

from __future__ import annotations

from datetime import datetime

from core.models.notification import Notification
from db.crud import execute, fetch_all, insert_returning, query_one

_NOTIFICATION_COLUMNS = (
    "id, agent_id, task_id, activity_id, kind, content, "
    "source_channel, policy, chat_visible, prompt_visibility, created_at"
)


def create_notification(
    *,
    agent_id: str,
    kind: str,
    content: str,
    source_channel: str,
    policy: str,
    chat_visible: bool,
    prompt_visibility: bool,
    task_id: str | None = None,
    activity_id: str | None = None,
) -> Notification:
    """Insert one runtime notification."""
    return insert_returning(
        f"""
        INSERT INTO notifications (
            agent_id, task_id, activity_id, kind, content,
            source_channel, policy, chat_visible, prompt_visibility
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING {_NOTIFICATION_COLUMNS}
        """,
        [
            agent_id,
            task_id,
            activity_id,
            kind,
            content,
            source_channel,
            policy,
            chat_visible,
            prompt_visibility,
        ],
        Notification,
    )


def list_notifications(
    *,
    agent_id: str,
    limit: int = 50,
    chat_visible: bool | None = None,
    prompt_visible: bool | None = None,
    earliest_ts: datetime | None = None,
) -> list[Notification]:
    """Return notifications for an agent, newest first unless reversed by caller."""
    conditions = ["agent_id = $1"]
    params: list[object] = [agent_id]
    if chat_visible is not None:
        params.append(chat_visible)
        conditions.append(f"chat_visible = ${len(params)}")
    if prompt_visible is not None:
        params.append(prompt_visible)
        conditions.append(f"prompt_visibility = ${len(params)}")
    if earliest_ts is not None:
        params.append(earliest_ts)
        conditions.append(f"created_at >= ${len(params)}")
    params.append(limit)
    return fetch_all(
        f"""
        SELECT {_NOTIFICATION_COLUMNS}
        FROM notifications
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(params)}
        """,
        params,
        Notification,
    )


def delete_agent_notifications(agent_id: str, *, chat_visible_only: bool = False) -> int:
    """Delete stored notifications for an agent and return the deleted count."""
    if chat_visible_only:
        execute(
            """
            DELETE FROM notification_links
            WHERE notification_id IN (
                SELECT id FROM notifications WHERE agent_id = $1 AND chat_visible = TRUE
            )
            """,
            [agent_id],
        )
        row = query_one(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE agent_id = $1 AND chat_visible = TRUE",
            [agent_id],
        )
        count = int(row["cnt"]) if row else 0
        execute("DELETE FROM notifications WHERE agent_id = $1 AND chat_visible = TRUE", [agent_id])
        return count

    execute(
        "DELETE FROM notification_links WHERE notification_id IN (SELECT id FROM notifications WHERE agent_id = $1)",
        [agent_id],
    )
    row = query_one("SELECT COUNT(*) AS cnt FROM notifications WHERE agent_id = $1", [agent_id])
    count = int(row["cnt"]) if row else 0
    execute("DELETE FROM notifications WHERE agent_id = $1", [agent_id])
    return count
