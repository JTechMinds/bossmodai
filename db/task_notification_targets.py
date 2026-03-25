"""BossMod AI — Durable notification delivery targets bound to tasks."""

from __future__ import annotations

from datetime import datetime, timezone

from db.crud import execute, query_one


def get_task_notification_target_channel_id(task_id: str) -> str | None:
    """Return the shared channel target for a task notification, if any."""
    row = query_one(
        """
        SELECT channel_id
        FROM task_notification_targets
        WHERE task_id = $1
        """,
        [task_id],
    )
    if not row:
        return None
    channel_id = row.get("channel_id")
    return str(channel_id) if isinstance(channel_id, str) and channel_id.strip() else None


def set_task_notification_target_channel_id(task_id: str, channel_id: str | None) -> None:
    """Create or replace one task's channel notification target."""
    existing = query_one(
        """
        SELECT 1
        FROM task_notification_targets
        WHERE task_id = $1
        """,
        [task_id],
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        execute(
            """
            INSERT INTO task_notification_targets (task_id, channel_id, created_at, updated_at)
            VALUES ($1, $2, $3, $4)
            """,
            [task_id, channel_id, now, now],
        )
        return
    execute(
        """
        UPDATE task_notification_targets
        SET channel_id = $1, updated_at = $2
        WHERE task_id = $3
        """,
        [channel_id, now, task_id],
    )


def delete_task_notification_target(task_id: str) -> None:
    """Delete one task's notification target."""
    execute("DELETE FROM task_notification_targets WHERE task_id = $1", [task_id])
