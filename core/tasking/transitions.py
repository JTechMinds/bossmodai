"""BossMod AI — Shared task status transition table.

Callers must change task status through ``transition_task`` so illegal jumps
cannot strand work (e.g. ``pending → complete``) and every change leaves a
``task_events`` row. ``db.update_task(..., status=...)`` uses the same allow-map
so the graph cannot be bypassed.
"""

from __future__ import annotations

from typing import Any

from core.models.task import Task, TaskStatus


class IllegalTaskTransition(ValueError):
    """Raised when a caller asks for a status jump that is not on the allow-map."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Illegal task status transition: {from_status} → {to_status}")


# Identity (from == to) is always allowed and is not listed here.
ALLOWED_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    # New / deferred assignment. Must accept or otherwise resolve — not jump to complete.
    "pending": frozenset(
        {
            "accepted",
            "declined",
            "abandoned",
            "blocked",
            "stalled",
            "active",
            "waiting",
            "delegated",
        }
    ),
    "accepted": frozenset(
        {
            "active",
            "waiting",
            "blocked",
            "complete",
            "abandoned",
            "delegated",
            "stalled",
            "pending",
            "declined",
        }
    ),
    "active": frozenset(
        {
            "waiting",
            "blocked",
            "complete",
            "abandoned",
            "delegated",
            "stalled",
            "pending",
            "accepted",
        }
    ),
    "waiting": frozenset(
        {
            "active",
            "accepted",
            "blocked",
            "complete",
            "abandoned",
            "delegated",
            "stalled",
            "pending",
        }
    ),
    "blocked": frozenset(
        {
            "active",
            "accepted",
            "waiting",
            "pending",
            "abandoned",
            "stalled",
            "delegated",
            "complete",
        }
    ),
    "stalled": frozenset(
        {
            "pending",
            "accepted",
            "active",
            "waiting",
            "blocked",
            "abandoned",
            "complete",
            "delegated",
        }
    ),
    # Terminal. Reopen is a later product decision — not a silent jump.
    "complete": frozenset(),
    "abandoned": frozenset(),
    "declined": frozenset(),
    # Parent handed off; may still coordinate or close.
    "delegated": frozenset(
        {
            "complete",
            "abandoned",
            "waiting",
            "active",
            "accepted",
            "blocked",
            "stalled",
            "pending",
        }
    ),
}


def is_allowed_task_transition(from_status: str, to_status: str) -> bool:
    """Return whether ``from_status → to_status`` is on the allow-map (or identity)."""
    if from_status == to_status:
        return True
    allowed = ALLOWED_TASK_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def assert_valid_task_transition(from_status: str, to_status: str) -> None:
    """Raise ``IllegalTaskTransition`` when the jump is not allowed."""
    if not is_allowed_task_transition(from_status, to_status):
        raise IllegalTaskTransition(from_status, to_status)


def transition_task(
    task_id: str,
    to: TaskStatus | str,
    *,
    reason: str,
    actor: str,
    actor_type: str = "system",
    actor_agent_id: str | None = None,
    source_trigger_id: str | None = None,
    **fields: Any,
) -> Task:
    """Move a task to ``to`` if the allow-map permits it, and log a task event.

    Extra ``fields`` are passed through to ``update_task`` (status_note,
    completion_summary, watchdog_pinged_at, …). The status column is always
    taken from ``to``, not from ``fields``.
    """
    import db
    from core.tasking.service import append_task_event

    target = str(to).strip()
    if not target:
        raise ValueError("transition_task requires a target status")

    task = db.get_task(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    from_status = task.status
    assert_valid_task_transition(from_status, target)

    extra = {key: value for key, value in fields.items() if key != "status"}
    updated = db.update_task(task_id, status=target, **extra)
    if updated is None:
        raise RuntimeError(f"Failed to reload task {task_id} after transition")

    if from_status != target:
        note = (reason or "").strip() or f"Status {from_status} → {target}."
        if not note.lower().startswith("status "):
            note = f"Status {from_status} → {target}: {note}"
        append_task_event(
            task_id=task_id,
            author_type=actor_type,
            author_name=actor,
            author_agent_id=actor_agent_id,
            event_type="status_update",
            content=note,
            source_trigger_id=source_trigger_id,
        )
    return db.get_task(task_id) or updated
