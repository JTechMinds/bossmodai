"""BossMod AI — The assignee's checklist for a task (the ``task_checklists`` side table).

One row per task that has a checklist, holding the JSON list of
``TaskChecklistItem`` in item order. No row means the task has no checklist.
Rows are read through the task join in ``db/tasks.py`` and written through
``db.update_task``. The rules for how a checklist may change (append items,
resolve one by number, never remove) live in ``core/tasking/checklist.py``;
this layer stores what it is given.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.models.task import TaskChecklistItem
from db.crud import execute, query_one


def set_task_checklist(task_id: str, items: list[TaskChecklistItem]) -> None:
    """Create or replace a task's checklist with ``items``, in order.

    Args:
        task_id: The task the checklist belongs to.
        items: Every item of the checklist, in item order.

    Raises:
        ValueError: ``items`` is empty. A checklist's items are never
            removed, so an empty checklist is never stored.
    """
    if not items:
        raise ValueError("A checklist's items are never removed")
    payload = json.dumps([item.model_dump() for item in items])
    existing = query_one("SELECT task_id FROM task_checklists WHERE task_id = $1", [task_id])
    if existing is None:
        execute("INSERT INTO task_checklists (task_id, items) VALUES ($1, $2)", [task_id, payload])
        return
    execute(
        "UPDATE task_checklists SET items = $1, updated_at = $2 WHERE task_id = $3",
        [payload, datetime.now(timezone.utc), task_id],
    )
