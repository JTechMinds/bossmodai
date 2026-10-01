"""One schedule occurrence (core/scheduling/runner.py) against a real database."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from core.floors import send_home
from core.models.message import HUMAN_SENDER_ID
from core.models.schedule import RecurrenceRule
from core.scheduling import runner
from core.scheduling.runner import (
    ScheduleRunFailed,
    ScheduleRunRefused,
    ScheduleSettingError,
    ScheduleTiming,
    read_timing,
    run_now,
    run_occurrence,
)
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task

TIMING = ScheduleTiming(max_sleep_seconds=60, grace_seconds=120)
DUE = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


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


def _rule() -> RecurrenceRule:
    return RecurrenceRule.model_validate(
        {"frequency": "daily", "interval": 1, "times": ["06:00"], "start_date": "2026-09-01"},
    )


def _schedule(agent_id: str, *, title: str = "Check the status page", enabled: bool = True):
    return db.create_schedule(
        agent_id=agent_id, title=title, instructions="Log in and read the status page.",
        recurrence=_rule(), notification_policy="completion_blocked", enabled=enabled,
    )


def _agent(name: str = "Ada"):
    return db.create_agent(name, role="Operator")


def test_a_due_run_creates_a_task_for_the_agent_and_returns_its_wake() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)

    run = run_occurrence(schedule.id, due_at=DUE, now=DUE + timedelta(seconds=1), timing=TIMING)

    assert run is not None and run.outcome == "fired"
    task = run.task
    assert task is not None
    stored = db.get_task(task.id)
    assert stored.schedule_id == schedule.id
    assert stored.title == "Check the status page"
    assert stored.assigned_to == ada.id
    assert stored.requester_id == HUMAN_SENDER_ID
    assert stored.source_channel == "api"
    assert stored.notification_policy == "completion_blocked"
    # The instructions only: the agent learns it is one run of a schedule from the trigger block.
    assert stored.description == "Log in and read the status page."
    assert run.trigger is not None
    assert run.trigger["trigger_type"] == "task_assigned"
    assert run.trigger["agent_id"] == ada.id and run.trigger["task_id"] == task.id
    assert run.rule == schedule.recurrence
    row = db.get_schedule(schedule.id)
    assert (row.last_outcome, row.last_task_id, row.last_occurrence_at) == ("fired", task.id, DUE)


def test_a_run_handled_past_the_grace_window_is_missed_and_creates_nothing() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)

    run = run_occurrence(schedule.id, due_at=DUE, now=DUE + timedelta(seconds=121), timing=TIMING)

    assert run is not None and run.outcome == "missed"
    assert run.task is None and run.trigger is None
    assert db.list_tasks(assigned_to=ada.id) == []
    assert db.get_schedule(schedule.id).last_outcome == "missed"


def test_an_open_previous_run_skips_until_it_is_finished() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    first = run_occurrence(schedule.id, due_at=DUE, now=DUE, timing=TIMING)
    assert first.outcome == "fired"

    later = DUE + timedelta(days=1)
    skipped = run_occurrence(schedule.id, due_at=later, now=later, timing=TIMING)
    assert skipped.outcome == "skipped_open" and skipped.task is None
    row = db.get_schedule(schedule.id)
    assert row.last_outcome == "skipped_open" and row.last_task_id == first.task.id
    assert len(db.list_tasks(assigned_to=ada.id)) == 1

    transition_task(first.task.id, "cancelled", reason="done with it", actor="Human Operator", actor_type="human")
    again = DUE + timedelta(days=2)
    fired = run_occurrence(schedule.id, due_at=again, now=again, timing=TIMING)
    assert fired.outcome == "fired" and fired.task.id != first.task.id
    assert db.get_schedule(schedule.id).last_task_id == fired.task.id


def test_a_vacationing_agent_is_skipped() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    send_home(ada.id)

    run = run_occurrence(schedule.id, due_at=DUE, now=DUE, timing=TIMING)

    assert run.outcome == "skipped_vacation" and run.task is None
    assert db.list_tasks(assigned_to=ada.id) == []


def test_a_creation_error_is_recorded_as_failed_and_the_run_still_returned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    ada = _agent()
    schedule = _schedule(ada.id)

    def boom(**_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runner, "create_or_bind_task", boom)
    caplog.set_level("WARNING", logger="core.scheduling.runner")

    run = run_occurrence(schedule.id, due_at=DUE, now=DUE, timing=TIMING)
    again = run_occurrence(schedule.id, due_at=DUE + timedelta(days=1), now=DUE + timedelta(days=1), timing=TIMING)

    assert run.outcome == "failed" and again.outcome == "failed"
    row = db.get_schedule(schedule.id)
    assert row.last_outcome == "failed"
    assert row.last_outcome_detail == "RuntimeError: database is locked"
    # Logged when the detail changes, not on every repeat.
    assert len([r for r in caplog.records if r.name == "core.scheduling.runner"]) == 1


def test_a_deleted_or_disabled_schedule_returns_none() -> None:
    ada = _agent()
    off = _schedule(ada.id, enabled=False)
    gone = _schedule(ada.id, title="Gone")
    db.delete_schedule(gone.id)

    assert run_occurrence(off.id, due_at=DUE, now=DUE, timing=TIMING) is None
    assert run_occurrence(gone.id, due_at=DUE, now=DUE, timing=TIMING) is None
    assert db.list_tasks(assigned_to=ada.id) == []


def test_an_open_operator_task_with_the_same_title_does_not_absorb_the_run() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    operator = create_or_bind_task(
        title="Check the status page", description="By hand", project=None, assigned_to=ada.id,
        requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None,
        work_contract=None, source_channel="api", notification_policy="completion_blocked",
        notification_channel_id=None, audit_author_name="Human Operator", audit_author_type="human",
    ).task

    run = run_occurrence(schedule.id, due_at=DUE, now=DUE, timing=TIMING)

    assert run.outcome == "fired"
    assert run.task.id != operator.id
    assert db.get_task(operator.id).schedule_id is None
    assert len(db.list_tasks(assigned_to=ada.id)) == 2


def test_bind_and_schedule_cannot_be_combined() -> None:
    ada = _agent()
    with pytest.raises(ValueError):
        create_or_bind_task(
            title="x", description=None, project=None, assigned_to=ada.id, requester_id=HUMAN_SENDER_ID,
            owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None, work_contract=None,
            source_channel="api", notification_policy="completion_blocked", notification_channel_id=None,
            audit_author_name="Schedule", audit_author_type="system", bind_task_id="t", schedule_id="s",
        )


def test_read_timing_reads_the_seeds_and_refuses_a_grace_no_longer_than_the_sleep() -> None:
    assert read_timing() == ScheduleTiming(max_sleep_seconds=60, grace_seconds=120)
    db.set_setting("schedule_fire_grace_seconds", "60", "simulation")
    config.reload()
    with pytest.raises(ScheduleSettingError):
        read_timing()
    db.set_setting("schedule_fire_grace_seconds", "abc", "simulation")
    config.reload()
    with pytest.raises(ScheduleSettingError):
        read_timing()


def test_changed_marks_news_and_every_fire() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    at = [DUE + timedelta(days=offset) for offset in range(4)]

    first = run_occurrence(schedule.id, due_at=at[0], now=at[0], timing=TIMING)
    skip = run_occurrence(schedule.id, due_at=at[1], now=at[1], timing=TIMING)
    again = run_occurrence(schedule.id, due_at=at[2], now=at[2], timing=TIMING)
    transition_task(first.task.id, "cancelled", reason="done", actor="Human Operator", actor_type="human")
    refire = run_occurrence(schedule.id, due_at=at[3], now=at[3], timing=TIMING)

    assert [(run.outcome, run.changed) for run in (first, skip, again, refire)] == [
        ("fired", True), ("skipped_open", True), ("skipped_open", False), ("fired", True),
    ]


def test_a_fired_run_carries_its_persisted_origin_line_and_others_none() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)

    fired = run_occurrence(schedule.id, due_at=DUE, now=DUE, timing=TIMING)
    skipped = run_occurrence(schedule.id, due_at=DUE + timedelta(days=1), now=DUE + timedelta(days=1), timing=TIMING)

    line = fired.origin_line["chat_message"]
    assert line["agent_id"] == ada.id and "Check the status page" in line["content"]
    assert skipped.origin_line == {}


# ─── Run now ───


def test_run_now_creates_a_manual_run_and_records_it_fired_now() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    now = datetime(2026, 9, 30, 14, 7, tzinfo=timezone.utc)

    run = run_now(schedule.id, now=now)

    assert run.outcome == "fired" and run.changed is True
    task = db.get_task(run.task.id)
    assert task.schedule_id == schedule.id and task.assigned_to == ada.id
    assert task.description == "Log in and read the status page."
    assert run.trigger["trigger_type"] == "task_assigned"
    assert run.origin_line["chat_message"]["agent_id"] == ada.id
    row = db.get_schedule(schedule.id)
    assert (row.last_outcome, row.last_occurrence_at, row.last_task_id) == ("fired", now, task.id)


def test_run_now_works_on_a_disabled_schedule_and_leaves_it_disabled() -> None:
    ada = _agent()
    schedule = _schedule(ada.id, enabled=False)
    run = run_now(schedule.id, now=DUE)
    assert run.outcome == "fired"
    assert db.get_schedule(schedule.id).enabled is False


def test_run_now_refuses_a_vacation_and_records_nothing() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    send_home(ada.id)
    with pytest.raises(ScheduleRunRefused) as refused:
        run_now(schedule.id, now=DUE)
    assert (refused.value.reason, refused.value.task_id) == ("vacation", None)
    assert refused.value.message == "Ada is on vacation"
    row = db.get_schedule(schedule.id)
    assert (row.last_outcome, row.last_occurrence_at) == (None, None)
    assert db.list_tasks(assigned_to=ada.id) == []


def test_run_now_refuses_while_the_last_run_is_open_and_records_nothing() -> None:
    ada = _agent()
    schedule = _schedule(ada.id)
    first = run_now(schedule.id, now=DUE)
    with pytest.raises(ScheduleRunRefused) as refused:
        run_now(schedule.id, now=DUE + timedelta(minutes=1))
    assert (refused.value.reason, refused.value.task_id) == ("open", first.task.id)
    assert refused.value.message == "The last run is still open"
    assert db.get_schedule(schedule.id).last_occurrence_at == DUE
    assert len(db.list_tasks(assigned_to=ada.id)) == 1


def test_run_now_records_a_creation_error_as_failed_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = _agent()
    schedule = _schedule(ada.id)

    def boom(**_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runner, "create_or_bind_task", boom)
    with pytest.raises(ScheduleRunFailed) as failed:
        run_now(schedule.id, now=DUE)
    assert failed.value.detail == "RuntimeError: database is locked"
    row = db.get_schedule(schedule.id)
    assert (row.last_outcome, row.last_outcome_detail, row.last_occurrence_at) == (
        "failed", "RuntimeError: database is locked", DUE,
    )


def test_run_now_on_a_missing_schedule_is_a_lookup_error() -> None:
    with pytest.raises(LookupError):
        run_now("nope", now=DUE)
