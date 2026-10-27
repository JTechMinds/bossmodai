"""BossMod AI — Shared task status transition table.

Callers must change task status through ``transition_task`` so illegal jumps
cannot strand work (e.g. ``pending → complete``) and every change leaves a
``task_events`` row. A same-status call leaves one only when it sets a new,
non-empty status note. ``db.update_task(..., status=...)`` uses the same allow-map
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


class TaskStatusChanged(Exception):
    """The task was no longer in the status the caller expected (``expected_from``).

    Raised by a compare-and-set ``transition_task`` before anything is
    written or logged: someone else moved the task first.

    Attributes:
        task_id: The task.
        expected: The status the caller required.
        actual: The status the task had instead (``None`` if it vanished).
    """

    def __init__(self, task_id: str, *, expected: str, actual: str | None) -> None:
        self.task_id = task_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Task {task_id} is {actual}, not {expected}")


# Identity (from == to) is always allowed and is not listed here.
TERMINAL_TASK_STATUSES = frozenset({"complete", "abandoned", "declined", "cancelled"})

ALLOWED_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    # New / deferred assignment. Must accept or otherwise resolve — not jump to complete.
    # ``held`` is the operator parking it before the assignee starts.
    "pending": frozenset(
        {
            "accepted",
            "declined",
            "abandoned",
            "cancelled",
            "blocked",
            "stalled",
            "active",
            "waiting",
            "delegated",
            "held",
        }
    ),
    # Created but not ready: waits on prerequisites or the operator. No edge to
    # accepted, active or complete, so it cannot be started without release.
    "held": frozenset({"pending", "blocked", "cancelled", "abandoned"}),
    "accepted": frozenset(
        {
            "active",
            "waiting",
            "blocked",
            "complete",
            "abandoned",
            "cancelled",
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
            "cancelled",
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
            "cancelled",
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
            "cancelled",
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
            "cancelled",
            "complete",
            "delegated",
        }
    ),
    # Terminal. Reopen is a later product decision — not a silent jump.
    "complete": frozenset(),
    "abandoned": frozenset(),
    "declined": frozenset(),
    "cancelled": frozenset(),
    # Parent handed off; may still coordinate or close.
    "delegated": frozenset(
        {
            "complete",
            "abandoned",
            "cancelled",
            "waiting",
            "active",
            "accepted",
            "blocked",
            "stalled",
            "pending",
        }
    ),
}


def is_terminal_task_status(status: str) -> bool:
    """Return whether ``status`` is a closed task state."""
    return str(status or "") in TERMINAL_TASK_STATUSES


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
    expected_from: str | None = None,
    **fields: Any,
) -> Task:
    """Move a task to ``to`` if the allow-map permits it, and log a task event.

    A status change always logs one ``status_update`` event built from
    ``reason``. A same-status call logs one ``status_update`` event only when
    ``fields`` sets a non-empty ``status_note`` that differs from the stored
    one; its content is the new note. Clearing or rewriting the same note logs
    nothing.

    Extra ``fields`` are passed through to ``update_task`` (status_note,
    completion_summary, watchdog_pinged_at, …). The status column is always
    taken from ``to``, not from ``fields``.

    ``expected_from`` makes the status write a compare-and-set: it applies
    only while the task is still in that status, so of two callers racing to
    move the same task exactly one wins. Omitted, the write is unconditional.

    Entering a terminal status settles the tasks held on this one
    (``core.tasking.dependencies.settle_dependents``), after this task's own
    write and event: the only place that happens.

    Raises ``ValueError`` for an empty target or unknown task,
    ``TaskStatusChanged`` when ``expected_from`` is given and the task is no
    longer in it (nothing written or logged), ``IllegalTaskTransition`` for a
    jump off the allow-map, and ``RuntimeError`` when the task cannot be
    reloaded after the update.
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
    previous_note = task.status_note
    # Before the allow-map: a task someone else already moved on is a lost
    # race, not an illegal request.
    if expected_from is not None and from_status != expected_from:
        raise TaskStatusChanged(task_id, expected=expected_from, actual=from_status)
    assert_valid_task_transition(from_status, target)

    extra = {key: value for key, value in fields.items() if key != "status"}
    updated = db.update_task(task_id, status=target, expected_status=expected_from, **extra)
    if updated is None:
        if expected_from is not None:
            current = db.get_task(task_id)
            raise TaskStatusChanged(
                task_id, expected=expected_from, actual=current.status if current is not None else None,
            )
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
    elif "status_note" in fields:
        new_note = str(fields["status_note"] or "").strip()
        if new_note and new_note != (previous_note or "").strip():
            append_task_event(
                task_id=task_id,
                author_type=actor_type,
                author_name=actor,
                author_agent_id=actor_agent_id,
                event_type="status_update",
                content=new_note,
                source_trigger_id=source_trigger_id,
            )
    stored = db.get_task(task_id) or updated
    if from_status != target and is_terminal_task_status(target):
        # Local: dependencies imports this module.
        from core.tasking.dependencies import settle_dependents

        settle_dependents(stored)
    return stored
