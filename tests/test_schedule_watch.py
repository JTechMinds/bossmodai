"""The runtime worker's schedule clock (core/scheduling/watch.py), with an injected wall clock."""

from __future__ import annotations

import asyncio
import os
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.models.schedule import RecurrenceRule
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.scheduling import runner as runner_module
from core.scheduling import watch as watch_module
from core.scheduling.watch import ScheduleWatch

DUE = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db_and_utc_zone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time_module.tzset()
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    runtime_events.set_sink(NullRuntimeEventSink())
    db.close_connection()
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time_module.tzset()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue_trigger(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


class RecordingSink(NullRuntimeEventSink):
    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []
        self.world_states = 0

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append({"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})

    async def broadcast_world_state(self) -> None:
        self.world_states += 1


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    dispatcher = FakeDispatcher()
    sink = RecordingSink()
    monkeypatch.setattr(watch_module, "dispatcher", dispatcher)
    runtime_events.set_sink(sink)
    return dispatcher, sink


def _timing(max_sleep: int, grace: int) -> None:
    db.set_setting("schedule_max_sleep_seconds", str(max_sleep), "simulation")
    db.set_setting("schedule_fire_grace_seconds", str(grace), "simulation")
    config.reload()


def _schedule(agent_id: str, *, at: str = "06:00", title: str = "Status check"):
    return db.create_schedule(
        agent_id=agent_id, title=title, instructions="Read the status page.",
        recurrence=RecurrenceRule.model_validate(
            {"frequency": "daily", "interval": 1, "times": [at], "start_date": "2026-09-01"},
        ),
        notification_policy="completion_blocked", enabled=True,
    )


async def _until(predicate, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def _wake(watch: ScheduleWatch) -> None:
    """Wake the sleeping loop without a reload (a reload would recompute from now)."""
    watch._wake.set()


async def test_a_due_entry_fires_enqueues_broadcasts_and_moves_to_its_next_run(env) -> None:
    dispatcher, sink = env
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _schedule(ada.id)
    clock = Clock(DUE - timedelta(seconds=5))
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await _until(lambda: watch._timetable.next_due() == DUE)
        clock.now = DUE + timedelta(seconds=1)
        _wake(watch)
        await _until(lambda: db.get_schedule(schedule.id).last_outcome == "fired")
        await _until(lambda: sink.world_states == 1)
    finally:
        await watch.stop()

    row = db.get_schedule(schedule.id)
    assert [call["trigger_type"] for call in dispatcher.calls] == ["task_assigned"]
    assert dispatcher.calls[0]["task_id"] == row.last_task_id
    events = [item["event"] for item in sink.activities]
    assert events == ["task_created", "schedule_ran"]
    ran = sink.activities[1]
    assert ran["extra"] == {"agent_id": ada.id, "schedule_id": schedule.id, "outcome": "fired"}
    assert ran["agent_name"] == "Ada"
    assert watch._timetable.next_due() == DUE + timedelta(days=1)


async def test_reload_picks_up_a_new_schedule_without_waiting_out_the_sleep(env) -> None:
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    clock = Clock(DUE - timedelta(hours=1))
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await asyncio.sleep(0.05)
        assert len(watch._timetable) == 0
        _schedule(ada.id)
        watch.reload()
        # Far under the 30 s sleep the loop is in.
        await _until(lambda: len(watch._timetable) == 1, timeout=1.0)
    finally:
        await watch.stop()
    assert watch._timetable.next_due() == DUE


async def test_a_clock_jump_past_the_grace_window_records_missed_and_creates_nothing(env) -> None:
    dispatcher, sink = env
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _schedule(ada.id)
    clock = Clock(DUE - timedelta(seconds=5))
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await _until(lambda: watch._timetable.next_due() == DUE)
        # The machine slept through 06:00 and woke at 06:10.
        clock.now = DUE + timedelta(minutes=10)
        _wake(watch)
        await _until(lambda: db.get_schedule(schedule.id).last_outcome == "missed")
        await _until(lambda: any(item["event"] == "schedule_ran" for item in sink.activities))
    finally:
        await watch.stop()
    assert dispatcher.calls == []
    assert db.list_tasks(assigned_to=ada.id) == []
    assert [item["extra"]["outcome"] for item in sink.activities] == ["missed"]
    assert sink.world_states == 0
    assert watch._timetable.next_due() == DUE + timedelta(days=1)


async def test_a_reload_after_a_due_time_but_before_the_next_pass_still_fires_it(env) -> None:
    dispatcher, _sink = env
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _schedule(ada.id)
    clock = Clock(DUE - timedelta(seconds=5))
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await _until(lambda: watch._checked_through is not None and watch._timetable.next_due() == DUE)
        # 06:00 passes while the loop sleeps; an operator edit's reload lands
        # before the next pass. The rebuild must not skip past the due run.
        clock.now = DUE + timedelta(seconds=1)
        watch.reload()
        await _until(lambda: db.get_schedule(schedule.id).last_outcome == "fired")
    finally:
        await watch.stop()
    assert [call["trigger_type"] for call in dispatcher.calls] == ["task_assigned"]
    assert watch._timetable.next_due() == DUE + timedelta(days=1)


async def test_a_run_due_during_a_failed_occurrence_survives_the_rebuild(
    env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    broken = _schedule(ada.id, at="06:00", title="Broken")
    later = _schedule(ada.id, at="06:01", title="Later")
    clock = Clock(DUE - timedelta(seconds=5))
    real_run = runner_module.run_occurrence

    def slow_then_fail(schedule_id, **kwargs):
        if schedule_id == broken.id:
            # The call takes long enough for "Later" (06:01) to come due.
            clock.now = DUE + timedelta(seconds=90)
            raise RuntimeError("database is locked")
        return real_run(schedule_id, **kwargs)

    monkeypatch.setattr(runner_module, "run_occurrence", slow_then_fail)
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await _until(lambda: watch._checked_through is not None and watch._timetable.next_due() == DUE)
        clock.now = DUE + timedelta(seconds=1)
        _wake(watch)
        await _until(lambda: db.get_schedule(later.id).last_outcome == "fired")
    finally:
        await watch.stop()
    # The failed occurrence is not replayed; "Broken" moves to tomorrow.
    assert db.get_schedule(broken.id).last_outcome is None
    assert watch._timetable.next_due() == DUE + timedelta(days=1)


async def test_the_first_load_after_start_skips_runs_missed_while_stopped(env) -> None:
    dispatcher, sink = env
    _timing(30, 60)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _schedule(ada.id)
    # Started after 06:00: today's run came due while the watch was stopped.
    clock = Clock(DUE + timedelta(seconds=10))
    watch = ScheduleWatch(clock=clock)
    watch.start()
    try:
        await _until(lambda: watch._checked_through is not None)
    finally:
        await watch.stop()
    assert watch._timetable.next_due() == DUE + timedelta(days=1)
    assert db.get_schedule(schedule.id).last_outcome is None
    assert dispatcher.calls == [] and sink.activities == []


async def test_the_watch_refuses_to_start_on_unusable_timing(caplog: pytest.LogCaptureFixture) -> None:
    _timing(60, 60)
    watch = ScheduleWatch(clock=Clock(DUE))
    caplog.set_level("ERROR", logger="core.scheduling.watch")
    watch.start()
    assert watch._task is None
    assert any("not started" in record.getMessage() for record in caplog.records)
    watch.reload()  # a no-op while not running
    await watch.stop()


async def test_the_worker_starts_stops_and_reloads_the_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.runtime import worker

    calls: list[str] = []

    class Service:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            calls.append(f"start:{self.name}")

        async def stop(self) -> None:
            calls.append(f"stop:{self.name}")

        def reload(self) -> None:
            calls.append(f"reload:{self.name}")

    for name in ("dispatcher", "simulation", "watchdog", "meeting_watchdog", "channel_idle_watch",
                 "extension_wake_watch", "schedule_watch"):
        monkeypatch.setattr(worker, name, Service(name))

    controller = worker.RuntimeController()
    controller._start_services()
    assert "start:schedule_watch" in calls
    calls.clear()
    await controller._stop_services()
    assert calls[0] == "stop:schedule_watch"
    calls.clear()

    runtime_worker = worker.RuntimeWorker()
    await runtime_worker._execute("reload_schedules", {})
    assert calls == ["reload:schedule_watch"]
