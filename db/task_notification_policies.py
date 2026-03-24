"""BossMod AI — Durable notification policy bound to tasks."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.notification import TaskNotificationSettings
from db.crud import execute, insert_returning, fetch_one

_POLICY_COLUMNS = "task_id, source_channel, policy, created_at, updated_at"


def get_task_notification_settings(task_id: str) -> TaskNotificationSettings | None:
    """Return the durable notification settings for a task."""
    return fetch_one(
        f"SELECT {_POLICY_COLUMNS} FROM task_notification_policies WHERE task_id = $1",
        [task_id],
        TaskNotificationSettings,
    )


def set_task_notification_settings(
    task_id: str,
    *,
    source_channel: str,
    policy: str,
) -> TaskNotificationSettings:
    """Create or replace the durable notification settings for a task."""
    existing = get_task_notification_settings(task_id)
    if existing is None:
        return insert_returning(
            f"""
            INSERT INTO task_notification_policies (task_id, source_channel, policy)
            VALUES ($1, $2, $3)
            RETURNING {_POLICY_COLUMNS}
            """,
            [task_id, source_channel, policy],
            TaskNotificationSettings,
        )

    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE task_notification_policies
        SET source_channel = $1, policy = $2, updated_at = $3
        WHERE task_id = $4
        """,
        [source_channel, policy, now, task_id],
    )
    refreshed = get_task_notification_settings(task_id)
    if refreshed is None:
        raise RuntimeError(f"Failed to reload notification settings for task {task_id}")
    return refreshed


def delete_task_notification_settings(task_id: str) -> None:
    """Delete durable notification settings for a task."""
    execute("DELETE FROM task_notification_policies WHERE task_id = $1", [task_id])
