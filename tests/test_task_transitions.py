"""HA-CORR-P1-02 — task status transition table."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.models.message import HUMAN_SENDER_ID
from core.tasking.transitions import (
    ALLOWED_TASK_TRANSITIONS,
    IllegalTaskTransition,
    is_allowed_task_transition,
    transition_task,
)


ALL_STATUSES = (
    "pending",
    "accepted",
    "active",
    "waiting",
    "blocked",
    "stalled",
    "complete",
    "abandoned",
    "delegated",
    "declined",
)

# Shortest legal walk from the default ``pending`` insert.
_SETUP_PATHS: dict[str, tuple[str, ...]] = {
    "pending": (),
    "accepted": ("accepted",),
    "active": ("accepted", "active"),
    "waiting": ("accepted", "active", "waiting"),
    "blocked": ("accepted", "blocked"),
    "stalled": ("accepted", "stalled"),
    "complete": ("accepted", "active", "complete"),
    "abandoned": ("accepted", "abandoned"),
    "delegated": ("accepted", "delegated"),
    "declined": ("declined",),
}

_ALLOWED_PAIRS = [
    (source, dest)
    for source, dests in ALLOWED_TASK_TRANSITIONS.items()
    for dest in sorted(dests)
]

_ILLEGAL_PAIRS = [
    (source, dest)
    for source in ALL_STATUSES
    for dest in ALL_STATUSES
    if source != dest and dest not in ALLOWED_TASK_TRANSITIONS.get(source, frozenset())
]


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _new_task():
    return db.create_task(title="Write report", assigned_to=None, created_by=HUMAN_SENDER_ID)


def _reach_status(task_id: str, target: str) -> None:
    for status in _SETUP_PATHS[target]:
        transition_task(task_id, status, reason=f"setup {status}", actor="pytest")


@pytest.mark.parametrize("from_status,to_status", _ALLOWED_PAIRS)
def test_allowed_transition_updates_status_and_logs_event(from_status: str, to_status: str) -> None:
    task = _new_task()
    _reach_status(task.id, from_status)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == from_status

    updated = transition_task(
        task.id,
        to_status,
        reason=f"table {from_status}->{to_status}",
        actor="pytest",
    )
    assert updated.status == to_status
    events = db.list_task_events(task.id)
    assert any(
        event.event_type == "status_update" and f"{from_status} → {to_status}" in event.content
        for event in events
    )


@pytest.mark.parametrize("from_status,to_status", _ILLEGAL_PAIRS)
def test_illegal_transition_raises_and_leaves_db_unchanged(from_status: str, to_status: str) -> None:
    task = _new_task()
    _reach_status(task.id, from_status)
    before = db.get_task(task.id)
    assert before is not None
    assert before.status == from_status
    event_count = len(db.list_task_events(task.id))

    with pytest.raises(IllegalTaskTransition) as exc:
        transition_task(task.id, to_status, reason="illegal jump", actor="pytest")

    assert exc.value.from_status == from_status
    assert exc.value.to_status == to_status
    after = db.get_task(task.id)
    assert after is not None
    assert after.status == from_status
    assert after.status_note == before.status_note
    assert len(db.list_task_events(task.id)) == event_count


def test_happy_path_pending_accepted_active_complete() -> None:
    task = _new_task()
    assert task.status == "pending"

    accepted = transition_task(task.id, "accepted", reason="accepted the assignment", actor="Ada")
    assert accepted.status == "accepted"
    active = transition_task(task.id, "active", reason="started work", actor="Ada")
    assert active.status == "active"
    complete = transition_task(task.id, "complete", reason="finished", actor="Ada")
    assert complete.status == "complete"

    contents = [event.content for event in db.list_task_events(task.id) if event.event_type == "status_update"]
    assert any("pending → accepted" in item for item in contents)
    assert any("accepted → active" in item for item in contents)
    assert any("active → complete" in item for item in contents)


def test_update_task_cannot_bypass_transition_table() -> None:
    task = _new_task()
    with pytest.raises(IllegalTaskTransition):
        db.update_task(task.id, status="complete")
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "pending"


def test_identity_transition_is_allowed() -> None:
    task = _new_task()
    same = transition_task(task.id, "pending", reason="no-op", actor="pytest")
    assert same.status == "pending"
    assert is_allowed_task_transition("complete", "complete")
