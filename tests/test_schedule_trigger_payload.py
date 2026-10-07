"""A scheduled task tells its agent it is one run: the trigger payload, the prompt, `schedules`.

Covers Revision 4 of the schedules plan: ``build_task_assigned_trigger``
carries the schedule's fields, the shipped ``runtime_block_trigger_event``
renders the recurring-task message from them (through the real template
engine), and the agent's read-only ``schedules`` command lists its own
schedules.
"""

from __future__ import annotations

import os
import time as time_module
from datetime import datetime, timezone
from pathlib import Path

import pytest

import db
from core.models.message import HUMAN_SENDER_ID
from core import config
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.bm_cli.command_registry import VIRTUAL_COMMAND_REGISTRY
from core.bm_cli.runtime import execute_bm_cli
from core.default_prompts import load_default_prompt
from core.llm.context_builder import _format_trigger
from core.models.schedule import SCHEDULE_TRIGGER_FIELDS, RecurrenceRule
from core.scheduling.recurrence import format_local_run, next_occurrence
from core.scheduling.runner import run_now

PROMPT_KEY = "runtime_block_trigger_event"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db_in_utc():
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
    db.close_connection()
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time_module.tzset()


def _rule() -> RecurrenceRule:
    return RecurrenceRule.model_validate({
        "frequency": "daily", "interval": 1, "every_minutes": 5, "window_start": "00:00",
        "window_end": "23:59", "start_date": "2026-09-01",
    })


def _scheduled_task(*, enabled: bool = True, title: str = "Ping me"):
    ada = db.create_agent("Ada", role="Operator")
    schedule = db.create_schedule(
        agent_id=ada.id, title=title, instructions="Send the operator a ping.",
        recurrence=_rule(), notification_policy="completion_blocked", enabled=enabled, created_by=HUMAN_SENDER_ID, agent_can_change=False,
    )
    run = run_now(schedule.id, now=NOW)
    return ada, schedule, db.get_task(run.task.id)


def _render(payload: dict, contract_kind: str) -> str:
    trigger = {"type": "task_assigned", **payload}
    return _format_trigger(trigger, contract_kind, {PROMPT_KEY: load_default_prompt(PROMPT_KEY)})


# ─── The payload ───


def test_a_scheduled_task_carries_the_schedule_fields() -> None:
    _ada, schedule, task = _scheduled_task()
    before = datetime.now(timezone.utc)
    payload = build_task_assigned_trigger(task)["payload"]
    after = datetime.now(timezone.utc)
    # Exactly the fields the prompt can read (trigger.<key>).
    assert {key for key, _ in SCHEDULE_TRIGGER_FIELDS} <= set(payload)
    assert payload["schedule_title"] == "Ping me"
    assert payload["schedule_summary"] == "Every day, every 5 minutes"
    assert payload["schedule_enabled"] == "true"
    expected = {format_local_run(next_occurrence(schedule.recurrence, after=at)) for at in (before, after)}
    assert payload["schedule_next_run"] in expected


def test_a_disabled_schedule_has_no_next_run() -> None:
    _ada, _schedule, task = _scheduled_task(enabled=False)
    payload = build_task_assigned_trigger(task)["payload"]
    assert (payload["schedule_next_run"], payload["schedule_enabled"]) == ("", "false")


def test_an_ordinary_task_or_a_deleted_schedules_run_carries_none_of_them() -> None:
    ada, schedule, task = _scheduled_task()
    db.delete_schedule(schedule.id)
    detached = db.get_task(task.id)
    assert detached.schedule_id is None
    keys = {"schedule_title", "schedule_summary", "schedule_next_run", "schedule_enabled"}
    assert not keys & set(build_task_assigned_trigger(detached)["payload"])
    # Read just before the delete: still presented as an ordinary task.
    assert not keys & set(build_task_assigned_trigger(task)["payload"])
    plain = db.create_task(title="Write it", description="Plain work", assigned_to=ada.id)
    assert not keys & set(build_task_assigned_trigger(plain)["payload"])


# ─── The prompt (the shipped template, through the real engine) ───


@pytest.mark.parametrize("contract_kind", ["decision", "execution"])
def test_a_scheduled_task_is_presented_as_one_run(contract_kind: str) -> None:
    _ada, _schedule, task = _scheduled_task()
    payload = build_task_assigned_trigger(task)["payload"]
    text = _render(payload, contract_kind)
    assert "This is a scheduled recurring task (Every day, every 5 minutes)." in text
    assert "run `schedules`" in text
    assert "The title of this task is: Ping me" in text
    assert "The task description is:\nSend the operator a ping." in text
    assert f"The next run is: {payload['schedule_next_run']}" in text
    assert "BossMod creates a new task for the next run and will wake you then." in text
    assert "Perform this run's work now, then close this task out. Do not wait for the next run." in text
    assert "Task description:" not in text


def test_a_switched_off_schedule_says_no_further_runs() -> None:
    _ada, _schedule, task = _scheduled_task(enabled=False)
    text = _render(build_task_assigned_trigger(task)["payload"], "decision")
    assert "This schedule is switched off, so no further runs are scheduled." in text
    assert "The next run is:" not in text
    assert "Do not wait for the next run." in text


def test_an_ordinary_task_renders_as_before() -> None:
    ada = db.create_agent("Ada", role="Operator")
    plain = db.create_task(title="Write it", description="Plain work", assigned_to=ada.id)
    text = _render(build_task_assigned_trigger(plain)["payload"], "decision")
    assert "Task description: Plain work" in text
    assert "scheduled recurring task" not in text and "schedules" not in text


# ─── The `schedules` command ───


def test_schedules_lists_only_the_callers_schedules() -> None:
    ada, schedule, task = _scheduled_task()
    bob = db.create_agent("Bob", role="Operator")
    db.create_schedule(
        agent_id=bob.id, title="Bob's routine", instructions="x", recurrence=_rule(),
        notification_policy="none", enabled=True, created_by=HUMAN_SENDER_ID, agent_can_change=False,
    )
    result = execute_bm_cli(ada, db.get_agent_state(ada.id), "schedules")
    assert result.ok, result.prompt_content
    assert [row["title"] for row in result.data["schedules"]] == ["Ping me"]
    row = result.data["schedules"][0]
    assert (row["repeats"], row["enabled"], row["last_outcome"], row["last_task_id"], row["last_task_status"]) == (
        "Every day, every 5 minutes", True, "fired", task.id, "pending",
    )
    assert row["next_run"] and row["last_run"] == format_local_run(NOW)
    assert "Bob's routine" not in result.prompt_content
    line = next(text for text in result.prompt_content.splitlines() if text.startswith(f"{row['id'][:8]} | Ping me |"))
    assert f"| on | no (ask the boss) | {row['next_run']} | fired {format_local_run(NOW)} | {task.id} (pending)" in line


def test_schedules_with_none_says_so() -> None:
    ada = db.create_agent("Ada", role="Operator")
    result = execute_bm_cli(ada, db.get_agent_state(ada.id), "schedules")
    assert result.ok
    assert result.data["schedules"] == []
    assert "You have no scheduled recurring tasks." in result.prompt_content


def test_schedules_is_a_listed_agent_command_with_help() -> None:
    meta = VIRTUAL_COMMAND_REGISTRY["schedules"]
    assert (meta.category, meta.usage_syntax, meta.description) == (
        "agent", "schedules [list|add|edit <id>|on <id>|off <id>|remove <id>]",
        "List and manage your scheduled recurring tasks.",
    )
    ada = db.create_agent("Ada", role="Operator")
    state = db.get_agent_state(ada.id)
    assert "schedules" in execute_bm_cli(ada, state, "help schedules").prompt_content
    assert "schedules" in execute_bm_cli(ada, state, "categories").prompt_content
