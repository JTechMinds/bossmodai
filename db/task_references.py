"""BossMod AI — Reference documents bound to tasks (the ``task_references`` side table).

One row per task that has references, holding the JSON list of
``TaskReference``. No row means the task has none. Which paths a reference
may name is decided in ``core/tasking/references.py``; this layer stores
what it is given.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.models.task import TaskReference
from db.crud import execute, query_one


def get_task_references(task_id: str) -> list[TaskReference]:
    """Return a task's references in stored order; ``[]`` when it has none."""
    row = query_one("SELECT refs FROM task_references WHERE task_id = $1", [task_id])
    if row is None:
        return []
    return [TaskReference.model_validate(item) for item in json.loads(row["refs"])]


def set_task_references(task_id: str, references: list[TaskReference]) -> None:
    """Create or replace a task's references.

    Raises:
        ValueError: ``references`` is empty; clearing is
            :func:`delete_task_references`, so an empty row is never stored.
    """
    if not references:
        raise ValueError("set_task_references needs at least one reference; use delete_task_references to clear")
    refs = json.dumps([reference.model_dump() for reference in references])
    existing = query_one("SELECT task_id FROM task_references WHERE task_id = $1", [task_id])
    if existing is None:
        execute("INSERT INTO task_references (task_id, refs) VALUES ($1, $2)", [task_id, refs])
        return
    execute(
        "UPDATE task_references SET refs = $1, updated_at = $2 WHERE task_id = $3",
        [refs, datetime.now(timezone.utc), task_id],
    )


def delete_task_references(task_id: str) -> None:
    """Remove every reference from a task."""
    execute("DELETE FROM task_references WHERE task_id = $1", [task_id])
