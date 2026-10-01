"""Agents manage their own schedules; the operator's lock (Revision 5 of the schedules plan).

The service rules for both actors, the migration of the two new columns,
the agent's ``schedules`` subcommands end to end through ``execute_bm_cli``,
the operator's DM note, declared activity and reload for every change, and
the worker's reload, which only syncs (Revision 7).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.bm_cli.runtime import execute_bm_cli
from core.models.message import HUMAN_SENDER_ID
from core.models.schedule import AgentScheduleUpdate, ScheduleCreate, ScheduleUpdate
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.scheduling import service
from core.scheduling.service import OPERATOR, ScheduleActor, ScheduleLocked, ScheduleNotFound

RULE = {"frequency": "daily", "interval": 1, "times": ["09:00"], "start_date": "2026-09-01"}
LOCKED = "This schedule does not allow you to modify it. Contact Human Operator if you need help managing this schedule."


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


def _create(title: str = "Check GitHub", **fields: Any) -> ScheduleCreate:
    return ScheduleCreate.model_validate({"title": title, "instructions": "Look.", "recurrence": RULE, **fields})


def _agent_actor(agent_id: str) -> ScheduleActor:
    return ScheduleActor("agent", agent_id)


def _reloads() -> list[dict[str, Any]]:
    return [
        json.loads(command.payload) for command in db.list_queued_runtime_commands()
        if command.command_type == "reload_schedules"
    ]


def _cli(agent, command: str, content: str | None = None):
    return execute_bm_cli(agent, db.get_agent_state(agent.id), command, content)


# ─── Service rules ───


def test_an_agent_creates_its_own_schedule_unlocked_and_marked_as_its_own() -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule = service.create_schedule(ada.id, _create(), actor=_agent_actor(ada.id))
    assert (schedule.created_by, schedule.agent_can_change) == (ada.id, True)
    view = service.to_view(schedule, now=schedule.created_at)
    assert view.created_by_name == "Ada"


def test_the_operators_schedule_starts_locked_unless_opened() -> None:
    ada = db.create_agent("Ada", role="Operator")
    locked = service.create_schedule(ada.id, _create(), actor=OPERATOR)
    opened = service.create_schedule(ada.id, _create("Open", agent_can_change=True), actor=OPERATOR)
    assert (locked.created_by, locked.agent_can_change) == (HUMAN_SENDER_ID, False)
    assert opened.agent_can_change is True
    assert service.to_view(locked, now=locked.created_at).created_by_name is None


def test_an_agent_cannot_create_for_another_or_set_the_lock() -> None:
    ada = db.create_agent("Ada", role="Operator")
    bob = db.create_agent("Bob", role="Operator")
    with pytest.raises(ValueError, match="only schedule work for itself"):
        service.create_schedule(bob.id, _create(), actor=_agent_actor(ada.id))
    with pytest.raises(ValueError, match="Only the operator"):
        service.create_schedule(ada.id, _create(agent_can_change=True), actor=_agent_actor(ada.id))


@pytest.mark.parametrize("change", ["edit", "on", "off", "remove"])
def test_every_agent_change_to_a_locked_schedule_says_who_to_contact(change: str) -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule = service.create_schedule(ada.id, _create(), actor=OPERATOR)
    actor = _agent_actor(ada.id)
    with pytest.raises(ScheduleLocked) as locked:
        if change == "edit":
            service.update_schedule(schedule.id, AgentScheduleUpdate(title="New"), actor=actor)
        elif change == "remove":
            service.delete_schedule(schedule.id, actor=actor)
        else:
            service.set_enabled(schedule.id, change == "on", actor=actor)
    assert locked.value.message == LOCKED
    assert "Contact Human Operator" in locked.value.message
    assert db.get_schedule(schedule.id).title == "Check GitHub"


def test_another_agents_schedule_is_not_found() -> None:
    ada = db.create_agent("Ada", role="Operator")
    bob = db.create_agent("Bob", role="Operator")
    bobs = service.create_schedule(bob.id, _create(), actor=_agent_actor(bob.id))
    with pytest.raises(ScheduleNotFound):
        service.update_schedule(bobs.id, AgentScheduleUpdate(title="Mine"), actor=_agent_actor(ada.id))
    with pytest.raises(ScheduleNotFound):
        service.delete_schedule(bobs.id, actor=_agent_actor(ada.id))


def test_an_agent_edit_body_cannot_carry_the_lock_or_enabled() -> None:
    for field in ("agent_can_change", "enabled"):
        with pytest.raises(Exception) as refused:
            AgentScheduleUpdate.model_validate({field: True})
        assert "Extra inputs are not permitted" in str(refused.value)
    ada = db.create_agent("Ada", role="Operator")
    schedule = service.create_schedule(ada.id, _create(), actor=_agent_actor(ada.id))
    with pytest.raises(ValueError, match="Only the operator"):
        service.update_schedule(schedule.id, ScheduleUpdate(agent_can_change=False), actor=_agent_actor(ada.id))


def test_the_operator_can_change_the_lock() -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule = service.create_schedule(ada.id, _create(), actor=OPERATOR)
    opened = service.update_schedule(schedule.id, ScheduleUpdate(agent_can_change=True), actor=OPERATOR)
    assert opened.agent_can_change is True
    edited = service.update_schedule(schedule.id, AgentScheduleUpdate(title="Mine now"), actor=_agent_actor(ada.id))
    assert edited.title == "Mine now"


def test_request_reload_always_deduplicates_and_carries_no_announcement() -> None:
    service.request_reload()
    service.request_reload()
    service.request_reload()
    assert _reloads() == [{}]
    with pytest.raises(TypeError):
        service.request_reload(changed_agent_id="a1", detail="Ada changed it")


# ─── Migration ───


def test_the_migration_adds_both_columns_and_old_rows_read_as_the_operators_and_locked() -> None:
    ada = db.create_agent("Ada", role="Operator")
    db.close_connection()
    raw = sqlite3.connect(os.environ["BOSSMOD_DB_PATH"])
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DROP TABLE agent_schedules")
    raw.execute(
        """
        CREATE TABLE agent_schedules (
            id VARCHAR PRIMARY KEY, agent_id VARCHAR NOT NULL, title VARCHAR NOT NULL,
            instructions TEXT NOT NULL, recurrence TEXT NOT NULL, notification_policy VARCHAR NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1, last_occurrence_at TIMESTAMP, last_outcome VARCHAR,
            last_outcome_detail TEXT, last_task_id VARCHAR,
            created_at TIMESTAMP DEFAULT current_timestamp, updated_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    raw.execute(
        "INSERT INTO agent_schedules (id, agent_id, title, instructions, recurrence, notification_policy) "
        "VALUES ('old-1', ?, 'Old', 'x', ?, 'none')",
        [ada.id, json.dumps(RULE)],
    )
    raw.commit()
    raw.close()

    db.init_db()

    columns = {row["name"] for row in db.query("PRAGMA table_info(agent_schedules)")}
    assert {"created_by", "agent_can_change"} <= columns
    old = db.get_schedule("old-1")
    assert (old.created_by, old.agent_can_change) == (HUMAN_SENDER_ID, False)


# ─── The agent's `schedules` command ───


def _add_body(title: str = "Ping the operator", **extra: Any) -> str:
    return json.dumps({
        "title": title, "instructions": "Send a short ping.",
        "recurrence": {"frequency": "daily", "interval": 1, "start_date": "2026-10-01",
                       "every_minutes": 5, "window_start": "00:00", "window_end": "23:59"},
        **extra,
    })


def test_add_edit_off_on_remove_end_to_end_with_notes_and_reloads() -> None:
    ada = db.create_agent("Ada", role="Operator")

    added = _cli(ada, "schedules add", _add_body())
    assert added.ok, added.prompt_content
    schedule = db.list_schedules_for_agent(ada.id)[0]
    assert (schedule.created_by, schedule.agent_can_change) == (ada.id, True)
    assert added.data["origin_chrome"]["content"] == 'Ada scheduled "Ping the operator": Every day, every 5 minutes'
    assert added.data["origin_chrome"]["agent_id"] == ada.id
    short = schedule.id[:8]
    assert f"{short} | Ping the operator |" in added.prompt_content

    edited = _cli(ada, f"schedules edit {short}", json.dumps({"title": "Ping"}))
    assert edited.ok, edited.prompt_content
    assert db.get_schedule(schedule.id).title == "Ping"
    off = _cli(ada, f"schedules off {schedule.id}")
    assert off.ok and db.get_schedule(schedule.id).enabled is False
    on = _cli(ada, f"schedules on {short}")
    assert on.ok and db.get_schedule(schedule.id).enabled is True
    removed = _cli(ada, f"schedules remove {short}")
    assert removed.ok and db.get_schedule(schedule.id) is None

    notes = [result.data["origin_chrome"]["content"] for result in (edited, off, on, removed)]
    assert notes == [
        'Ada changed "Ping": Every day, every 5 minutes',
        'Ada switched off "Ping": Every day, every 5 minutes',
        'Ada switched on "Ping": Every day, every 5 minutes',
        'Ada removed "Ping": Every day, every 5 minutes',
    ]
    # Every change asked the worker to sync (one open row: no worker claimed it)...
    assert _reloads() == [{}]
    # ...and declares the desk's repaint for the turn to broadcast.
    activities = [result.data["activity"] for result in (added, edited, off, on, removed)]
    assert activities == [
        {"event": "schedule_changed", "detail": detail, "extra": {"agent_id": ada.id, "schedule_id": schedule.id}}
        for detail in ['Ada scheduled "Ping the operator": Every day, every 5 minutes', *notes]
    ]


def test_list_is_the_default_and_shows_short_ids() -> None:
    ada = db.create_agent("Ada", role="Operator")
    bare, listed_empty = _cli(ada, "schedules"), _cli(ada, "schedules list")
    assert bare.ok and bare.data == listed_empty.data == {"schedules": []}
    assert "You have no scheduled recurring tasks." in bare.prompt_content
    _cli(ada, "schedules add", _add_body())
    listed = _cli(ada, "schedules list")
    schedule = db.list_schedules_for_agent(ada.id)[0]
    assert f"{schedule.id[:8]} | Ping the operator | Every day, every 5 minutes | on | yes |" in listed.prompt_content


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not json", "The body must be a JSON object: Expecting value"),
        ("[1, 2]", "The body must be a JSON object: it is not an object"),
        (json.dumps({"title": "x", "instructions": "y", "recurrence": {**RULE, "frequency": "weekly"}}),
         "recurrence: A weekly schedule needs at least one weekday"),
        (_add_body(agent_can_change=True), "Only the operator can set agent_can_change"),
    ],
    ids=["not-json", "not-object", "validation", "lock"],
)
def test_add_says_exactly_what_is_wrong(body: str, expected: str) -> None:
    ada = db.create_agent("Ada", role="Operator")
    result = _cli(ada, "schedules add", body)
    assert not result.ok
    assert expected in result.prompt_content
    assert db.list_schedules_for_agent(ada.id) == []


def test_ids_resolve_by_unique_prefix_only() -> None:
    ada = db.create_agent("Ada", role="Operator")
    bob = db.create_agent("Bob", role="Operator")
    for schedule_id, owner in (("abcdef01-1", ada), ("abcdef01-2", ada), ("feedface-1", bob)):
        db.execute(
            "INSERT INTO agent_schedules (id, agent_id, title, instructions, recurrence, notification_policy, "
            "created_by, agent_can_change) VALUES ($1, $2, $3, 'x', $4, 'none', $2, 1)",
            [schedule_id, owner.id, f"S {schedule_id}", json.dumps({**RULE, "weekdays": [], "month_day": None})],
        )

    short = _cli(ada, "schedules off abcdef0")
    assert "Give at least 8 characters" in short.prompt_content
    ambiguous = _cli(ada, "schedules off abcdef01")
    assert "matches more than one of your schedules" in ambiguous.prompt_content
    assert "abcdef01-1" in ambiguous.prompt_content and "abcdef01-2" in ambiguous.prompt_content
    unknown = _cli(ada, "schedules off 99999999")
    assert "No schedule of yours matches 99999999." in unknown.prompt_content
    # Bob's schedule is not Ada's: not found, not "locked".
    others = _cli(ada, "schedules off feedface")
    assert "No schedule of yours matches feedface." in others.prompt_content
    unique = _cli(ada, "schedules off abcdef01-2")
    assert unique.ok and db.get_schedule("abcdef01-2").enabled is False


def test_a_locked_schedule_answers_with_the_operators_message_and_changes_nothing() -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule = service.create_schedule(ada.id, _create(), actor=OPERATOR)
    for command, body in (
        (f"schedules edit {schedule.id[:8]}", json.dumps({"title": "Mine"})),
        (f"schedules off {schedule.id[:8]}", None),
        (f"schedules remove {schedule.id[:8]}", None),
    ):
        result = _cli(ada, command, body)
        assert not result.ok
        assert LOCKED in result.prompt_content
    stored = db.get_schedule(schedule.id)
    assert (stored.title, stored.enabled) == ("Check GitHub", True)
    assert _reloads() == []


def test_usage_errors_list_the_forms() -> None:
    ada = db.create_agent("Ada", role="Operator")
    for command in ("schedules frobnicate", "schedules off", "schedules add extra"):
        result = _cli(ada, command)
        assert not result.ok
        assert "schedules remove <id>" in result.prompt_content, command


# ─── The worker's reload only syncs ───


class _Sink(NullRuntimeEventSink):
    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append({"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})


async def test_the_worker_reload_syncs_and_announces_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.runtime import worker

    reloads: list[str] = []
    monkeypatch.setattr(worker.schedule_watch, "reload", lambda: reloads.append("reload"))
    sink = _Sink()
    runtime_events.set_sink(sink)
    try:
        controller = worker.RuntimeController()
        await controller.reload_schedules()
        await controller.reload_schedules()
    finally:
        runtime_events.set_sink(NullRuntimeEventSink())
    assert reloads == ["reload", "reload"]
    assert sink.activities == []
