"""BossMod AI — The conversation a task came from (the ``task_origin_conversations`` side table).

One row per task that has one, holding a ``TaskOriginConversation``:
``source_names`` and ``messages`` as JSON, oldest message first. No row
means none was recorded. Never read through the task join in
``db/tasks.py``: only a work turn's history and the task detail's endpoint
read it, so task lists and the board never pay for it. The rules (what is
saved, when, the window rule, forwarding to a child) live in
``core/agent_loop/origin_conversation.py``; this layer stores what it is
given.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.models.task import TaskOriginConversation
from db.crud import execute, query_one


def set_task_origin_conversation(origin: TaskOriginConversation) -> None:
    """Create a task's origin conversation, or replace the one it has.

    Args:
        origin: The conversation to store; its ``task_id`` names the row.

    Raises:
        sqlite3.IntegrityError: ``origin.task_id`` names no task.
    """
    names = json.dumps(origin.source_names)
    messages = json.dumps([message.model_dump(mode="json") for message in origin.messages])
    execute(
        """
        INSERT INTO task_origin_conversations (task_id, source_kind, source_names, messages, captured_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT(task_id) DO UPDATE SET
            source_kind = excluded.source_kind,
            source_names = excluded.source_names,
            messages = excluded.messages,
            captured_at = excluded.captured_at,
            updated_at = excluded.updated_at
        """,
        [origin.task_id, origin.source_kind, names, messages, origin.captured_at, datetime.now(timezone.utc)],
    )


def get_task_origin_conversation(task_id: str) -> TaskOriginConversation | None:
    """Return a task's origin conversation, or ``None`` when none is recorded.

    Args:
        task_id: The task whose conversation is read.

    Returns:
        The stored conversation, or ``None`` when the task has no row.

    Raises:
        pydantic.ValidationError: The stored row is malformed. It is never
            skipped: a bad row is a fault to fix, not an absent one.
        json.JSONDecodeError: A JSON column is not JSON.
    """
    row = query_one(
        "SELECT task_id, source_kind, source_names, messages, captured_at "
        "FROM task_origin_conversations WHERE task_id = $1",
        [task_id],
    )
    if row is None:
        return None
    return TaskOriginConversation.model_validate({
        **row,
        "source_names": json.loads(row["source_names"]),
        "messages": json.loads(row["messages"]),
    })
