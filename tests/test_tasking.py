"""HA-TEST-P1-03 — bind vs create + watchdog ping enqueue. No LLM."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.watchdog import TaskWatchdog
from core.models.message import HUMAN_SENDER_ID
from core.tasking import create_or_bind_task
from core.tasking.transitions import transition_task


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


def _bind(
    *,
    title: str,
    assigned_to: str | None,
    bind_task_id: str | None = None,
):
    return create_or_bind_task(
        title=title,
        description="Board work",
        project=None,
        assigned_to=assigned_to,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
        bind_task_id=bind_task_id,
    )


def test_create_or_bind_creates_then_reuses_same_title() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    first = _bind(title="Weekly report", assigned_to=agent.id)
    assert first.outcome == "create_new_task"
    assert first.task is not None

    second = _bind(title="Weekly report", assigned_to=agent.id)
    assert second.outcome == "bind_existing_task"
    assert second.task is not None
    assert second.task.id == first.task.id
    assert len(db.list_tasks(assigned_to=agent.id)) == 1


def test_create_or_bind_ambiguous_match_does_not_create_third() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    first = db.create_task(title="Shared title", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    second = db.create_task(title="Shared title", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    assert first.id != second.id

    result = _bind(title="Shared title", assigned_to=agent.id)
    assert result.outcome == "clarify_ambiguous_match"
    assert result.task is None
    assert {candidate.id for candidate in result.resolution.candidates} == {first.id, second.id}
    assert len(db.list_tasks(assigned_to=agent.id)) == 2


def test_create_or_bind_explicit_bind_task_id_reuses_row() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    existing = db.create_task(title="Chosen row", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)

    result = _bind(title="Different title", assigned_to=agent.id, bind_task_id=existing.id)
    assert result.outcome == "bind_existing_task"
    assert result.task is not None
    assert result.task.id == existing.id
    assert len(db.list_tasks(assigned_to=agent.id)) == 1


@pytest.mark.asyncio
async def test_watchdog_enqueues_status_ping_for_quiet_active_task() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    creation = _bind(title="Quiet active work", assigned_to=agent.id)
    assert creation.task is not None
    transition_task(creation.task.id, "accepted", reason="setup", actor="pytest")
    transition_task(creation.task.id, "active", reason="setup", actor="pytest")
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(
        creation.task.id,
        last_progress_at=past,
        last_heartbeat_at=past,
        last_activity=past,
        watchdog_pinged_at=None,
    )

    await TaskWatchdog()._check_tasks()

    pings = [
        row
        for row in db.list_agent_triggers(agent.id)
        if row["trigger_type"] == "watchdog_status_ping"
        and row["task_id"] == creation.task.id
        and row["status"] == "queued"
    ]
    assert pings
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.watchdog_pinged_at is not None
