"""BossMod AI — Task-thread history formatting for prompt use."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import db
from core.models import TaskEvent


def load_task_thread_history(
    *,
    task_id: str,
    limit: int,
    earliest_ts: datetime | None = None,
    exclude_source_message_id: Any = None,
    exclude_source_task_event_id: Any = None,
) -> list[dict[str, Any]]:
    """Return task-thread events formatted as prompt-history entries."""
    events = db.list_task_events(task_id, limit=limit, earliest_ts=earliest_ts)
    source_id = str(exclude_source_message_id or "").strip()
    if source_id:
        events = [event for event in events if str(event.source_message_id or "") != source_id]
    source_event_id = str(exclude_source_task_event_id or "").strip()
    if source_event_id:
        events = [event for event in events if str(event.id or "") != source_event_id]
    return [_format_task_event(event) for event in events]


def _format_task_event(event: TaskEvent) -> dict[str, Any]:
    """Format one TaskEvent into a conversation-history row."""
    content = str(event.content or "").strip()
    event_type = str(event.event_type or "").strip()
    if event_type and event_type not in {"comment"}:
        content = f"({event_type}) {content}"
    return {
        "id": event.id,
        "from_agent": event.author_agent_id,
        "from_name": event.author_name or "Unknown",
        "content": content,
        "created_at": event.created_at.isoformat() if getattr(event, "created_at", None) else None,
        "event_type": event.event_type,
        "source_message_id": event.source_message_id,
        "source_trigger_id": event.source_trigger_id,
    }
