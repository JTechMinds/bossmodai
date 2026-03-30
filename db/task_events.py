"""BossMod AI — Durable task-thread event CRUD."""

from __future__ import annotations

from core.models import TaskEvent
from db.crud import fetch_all, insert_returning

_TASK_EVENT_COLUMNS = (
    "id, task_id, author_type, author_agent_id, author_name, "
    "event_type, content, source_message_id, source_trigger_id, created_at"
)


def create_task_event(
    *,
    task_id: str,
    author_type: str,
    author_name: str,
    event_type: str,
    content: str,
    author_agent_id: str | None = None,
    source_message_id: str | None = None,
    source_trigger_id: str | None = None,
) -> TaskEvent:
    """Insert a new durable task-thread event."""
    return insert_returning(
        f"""
        INSERT INTO task_events (
            task_id,
            author_type,
            author_agent_id,
            author_name,
            event_type,
            content,
            source_message_id,
            source_trigger_id
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING {_TASK_EVENT_COLUMNS}
        """,
        [
            task_id,
            author_type,
            author_agent_id,
            author_name,
            event_type,
            content,
            source_message_id,
            source_trigger_id,
        ],
        TaskEvent,
    )


def list_task_events(task_id: str, *, limit: int = 100) -> list[TaskEvent]:
    """Return task-thread events oldest first."""
    return fetch_all(
        f"""
        SELECT {_TASK_EVENT_COLUMNS}
        FROM task_events
        WHERE task_id = $1
        ORDER BY created_at ASC
        LIMIT $2
        """,
        [task_id, limit],
        TaskEvent,
    )


def list_recent_task_events(task_ids: list[str], *, limit_per_task: int = 3) -> dict[str, list[TaskEvent]]:
    """Return recent task-thread events for a set of tasks."""
    if not task_ids:
        return {}

    placeholders = ", ".join(f"${index + 1}" for index in range(len(task_ids)))
    rows = fetch_all(
        f"""
        SELECT id, task_id, author_type, author_agent_id, author_name,
               event_type, content, source_message_id, source_trigger_id, created_at
        FROM (
            SELECT
                {_TASK_EVENT_COLUMNS},
                ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY created_at DESC) AS row_num
            FROM task_events
            WHERE task_id IN ({placeholders})
        )
        WHERE row_num <= ${len(task_ids) + 1}
        ORDER BY task_id, created_at ASC
        """,
        [*task_ids, limit_per_task],
        TaskEvent,
    )
    grouped: dict[str, list[TaskEvent]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(row)
    return grouped
