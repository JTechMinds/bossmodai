"""BossMod AI — Task liveness bookkeeping."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db

_PROGRESS_ACTIONS = {"work", "complete", "blocked", "delegated", "abandoned", "attendMeeting", "remoteMeeting"}


def record_task_heartbeat(task_id: str | None, *, at: datetime | None = None) -> None:
    """Record that a task-bound turn is still alive."""
    if not task_id:
        return
    timestamp = at or datetime.now(timezone.utc)
    db.update_task(
        task_id,
        last_heartbeat_at=timestamp,
        last_activity=timestamp,
        watchdog_pinged_at=None,
    )


def record_task_progress(task_id: str | None, *, at: datetime | None = None) -> None:
    """Record that tangible progress happened on a task."""
    if not task_id:
        return
    timestamp = at or datetime.now(timezone.utc)
    db.update_task(
        task_id,
        last_progress_at=timestamp,
        last_heartbeat_at=timestamp,
        last_activity=timestamp,
        watchdog_pinged_at=None,
    )


def record_action_liveness(
    task_id: str | None,
    action: dict[str, Any],
    result: dict[str, Any],
    *,
    at: datetime | None = None,
) -> None:
    """Update task liveness based on an executed action."""
    if not task_id:
        return

    action_name = action.get("action")
    event = result.get("event")

    if action_name == "work" and event == "agent_updated":
        record_task_progress(task_id, at=at)
        return

    if action_name in {"attendMeeting", "remoteMeeting"} and event == "meeting_started":
        record_task_progress(task_id, at=at)
        return

    if action_name in _PROGRESS_ACTIONS - {"work", "attendMeeting", "remoteMeeting"}:
        record_task_progress(task_id, at=at)
        return

    record_task_heartbeat(task_id, at=at)
