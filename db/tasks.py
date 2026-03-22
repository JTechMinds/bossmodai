"""BossMod AI — Task CRUD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import Task
from db.crud import build_update, fetch_all, fetch_one, insert_returning

_TASK_COLUMNS = (
    "id, title, description, project, assigned_to, created_by, "
    "status, parent_task_id, cost_ceiling, last_activity, created_at"
)

_TASK_VALID_COLUMNS = {
    "title", "description", "project", "assigned_to",
    "status", "parent_task_id", "cost_ceiling", "last_activity",
}


def create_task(
    title: str,
    description: str | None = None,
    project: str | None = None,
    assigned_to: str | None = None,
    created_by: str | None = None,
    parent_task_id: str | None = None,
) -> Task:
    """Insert a new task."""
    return insert_returning(
        f"""
        INSERT INTO tasks (title, description, project, assigned_to, created_by, parent_task_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING {_TASK_COLUMNS}
        """,
        [title, description, project, assigned_to, created_by, parent_task_id],
        Task,
    )


def get_task(task_id: str) -> Task | None:
    """Fetch a single task by ID."""
    return fetch_one(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1",
        [task_id],
        Task,
    )


def list_tasks(
    assigned_to: str | None = None,
    status: str | None = None,
) -> list[Task]:
    """Return tasks, optionally filtered by assignee and/or status."""
    conditions: list[str] = []
    params: list[Any] = []

    if assigned_to is not None:
        params.append(assigned_to)
        conditions.append(f"assigned_to = ${len(params)}")
    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_all(
        f"SELECT {_TASK_COLUMNS} FROM tasks {where} ORDER BY created_at",
        params,
        Task,
    )


def update_task(task_id: str, **fields: Any) -> Task | None:
    """Update task fields. Auto-updates last_activity on status change."""
    if "status" in fields:
        fields.setdefault("last_activity", datetime.now(timezone.utc))

    build_update("tasks", "id", task_id, fields, _TASK_VALID_COLUMNS)
    return get_task(task_id)
