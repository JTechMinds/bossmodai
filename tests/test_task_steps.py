"""Task steps: one durable row per execution step an agent takes on a task.

The DB CRUD (create, finish once, newest-first keyset pages), the loop helpers
that write a step from an action and finish it from its result (or from a
resumed command's CLI result), the one wire shape, both GET routes and their
settings, and an agent deletion that keeps its steps.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.task_steps import cli_step_result, finish_task_step, start_task_step, task_step_event
from core.agent_repository import agent_repository
from core.bm_cli.types import BossModCliResult

_T0 = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)


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


def _agent(name: str = "Ettie"):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _task(title: str = "Fix the build"):
    return db.create_task(title=title, description="Make it green.")


def _step(task_id: str, agent, *, at: datetime, note: str | None = "Run the tests", command: str | None = None):
    return db.create_task_step(
        task_id=task_id, agent_id=agent.id, agent_name=agent.name, action="bm_cli",
        note=note, command=command, started_at=at,
    )


# ─── DB ───


def test_a_step_is_created_open_and_finished_once() -> None:
    agent, task = _agent(), _task()
    step = _step(task.id, agent, at=_T0, command="cargo test --workspace")

    assert (step.task_id, step.agent_id, step.agent_name, step.action) == (task.id, agent.id, "Ettie", "bm_cli")
    assert (step.note, step.command) == ("Run the tests", "cargo test --workspace")
    assert (step.outcome, step.result, step.ended_at) == (None, None, None)
    assert step.started_at == _T0
    assert db.get_task_step(step.id) == step

    ended = _T0 + timedelta(seconds=225)
    finished = db.finish_task_step(step.id, outcome="failed", result="command failed (exit 101)", ended_at=ended)
    assert (finished.outcome, finished.result, finished.ended_at) == ("failed", "command failed (exit 101)", ended)
    assert db.get_task_step(step.id) == finished

    with pytest.raises(ValueError, match="already ended"):
        db.finish_task_step(step.id, outcome="ok", result=None, ended_at=ended)
    assert db.get_task_step(step.id) == finished
    with pytest.raises(ValueError, match="Unknown task step"):
        db.finish_task_step("no-such-step", outcome="ok", result=None, ended_at=ended)
    with pytest.raises(ValueError, match="Unknown task step outcome"):
        db.finish_task_step(_step(task.id, agent, at=ended).id, outcome="maybe", result=None, ended_at=ended)


def test_steps_page_newest_first_with_a_strict_cursor_through_a_tie() -> None:
    agent, task = _agent(), _task()
    other = _task("Another task")
    _step(other.id, agent, at=_T0 + timedelta(hours=1))
    oldest = _step(task.id, agent, at=_T0)
    # Two steps in the same microsecond: only the id orders them.
    tied = sorted(
        (_step(task.id, agent, at=_T0 + timedelta(seconds=5)) for _ in range(2)),
        key=lambda step: step.id,
        reverse=True,
    )
    newest = _step(task.id, agent, at=_T0 + timedelta(seconds=9, microseconds=500))

    first = db.list_task_steps(task.id, limit=2)
    assert [step.id for step in first] == [newest.id, tied[0].id]
    second = db.list_task_steps(task.id, limit=2, before=(first[-1].started_at, first[-1].id))
    assert [step.id for step in second] == [tied[1].id, oldest.id]
    assert db.list_task_steps(task.id, limit=2, before=(oldest.started_at, oldest.id)) == []
    with pytest.raises(ValueError, match="at least 1"):
        db.list_task_steps(task.id, limit=0)


# ─── Loop helpers ───


def test_start_writes_nothing_without_a_bound_task() -> None:
    agent = _agent()
    action = {"action": "bm_cli", "command": "ls", "thought": "look"}
    assert start_task_step(task_id=None, agent=agent, action=action, at=_T0) is None
    assert start_task_step(task_id="", agent=agent, action=action, at=_T0) is None
    assert db.query("SELECT id FROM task_steps") == []


def test_start_keeps_the_note_and_a_cli_command_stripped_and_verbatim() -> None:
    agent, task = _agent(), _task()
    cli = start_task_step(
        task_id=task.id, agent=agent, at=_T0,
        action={"action": "bm_cli", "command": "  cargo +1.67.1 test --workspace \n", "thought": " Run the suite "},
    )
    assert (cli.action, cli.note, cli.command, cli.started_at) == (
        "bm_cli", "Run the suite", "cargo +1.67.1 test --workspace", _T0,
    )
    message = start_task_step(
        task_id=task.id, agent=agent, at=_T0,
        action={"action": "message", "command": "not a command", "thought": "Tell Debra"},
    )
    assert (message.action, message.note, message.command) == ("message", "Tell Debra", None)
    blank = start_task_step(task_id=task.id, agent=agent, at=_T0, action={"action": "complete", "thought": "   "})
    assert (blank.action, blank.note, blank.command) == ("complete", None, None)


def test_a_note_that_is_not_text_is_left_out_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    agent, task = _agent(), _task()
    with caplog.at_level(logging.ERROR, logger="core.agent_loop.task_steps"):
        step = start_task_step(task_id=task.id, agent=agent, at=_T0, action={"action": "bm_cli", "command": "ls", "thought": 7})
    assert (step.note, step.command) == (None, "ls")
    assert any("note" in record.getMessage() and "int" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        ({"event": "bm_cli_result", "detail": " command completed (exit 0, 10ms) "}, "ok"),
        ({"event": "bm_cli_error", "detail": "BossMod CLI error: Blocked — host path is outside the locked clone"}, "failed"),
        ({"event": "agent_error", "detail": "Ettie returned an invalid task action"}, "failed"),
        (
            {"event": "cli_approval_required", "approval_required": True, "detail": "Ettie requests approval: docker run x"},
            "approval",
        ),
        ({"event": "host_path_consent_required", "consent_required": True, "detail": "Ettie asks to use /srv"}, "consent"),
        ({"event": "task_completed"}, "ok"),
    ],
)
def test_finish_sets_the_outcome_from_the_result_and_keeps_its_detail(result: dict[str, Any], outcome: str) -> None:
    agent, task = _agent(), _task()
    step = start_task_step(task_id=task.id, agent=agent, at=_T0, action={"action": "bm_cli", "command": "x", "thought": "t"})
    ended = _T0 + timedelta(seconds=1)
    finished = finish_task_step(step, result=result, at=ended)
    assert finished.outcome == outcome
    assert finished.result == (result["detail"].strip() if "detail" in result else None)
    assert finished.ended_at == ended


@pytest.mark.parametrize(
    ("fields", "event", "outcome"),
    [
        ({"ok": True, "detail": "command completed (exit 0, 225s)"}, "bm_cli_result", "ok"),
        ({"ok": False, "detail": "command failed (exit 101, 225s)"}, "bm_cli_error", "failed"),
        ({"ok": False, "detail": "Ettie requests approval: docker run x", "approval_required": True},
         "bm_cli_error", "approval"),
        ({"ok": False, "detail": "Ettie asks to use /srv", "consent_required": True}, "bm_cli_error", "consent"),
    ],
)
def test_a_resumed_cli_result_maps_to_a_step_result(fields: dict[str, Any], event: str, outcome: str) -> None:
    cli_result = BossModCliResult(command="cargo test", prompt_content="BOSSMOD CLI RESULT", **fields)
    mapped = cli_step_result(cli_result)
    assert mapped == {
        "event": event,
        "detail": fields["detail"],
        "approval_required": fields.get("approval_required", False),
        "consent_required": fields.get("consent_required", False),
    }
    # It is finished on the one path every step takes.
    agent, task = _agent(), _task()
    step = start_task_step(task_id=task.id, agent=agent, at=_T0, action={"action": "bm_cli", "command": "cargo test"})
    finished = finish_task_step(step, result=mapped, at=_T0 + timedelta(seconds=225))
    assert (finished.outcome, finished.result) == (outcome, fields["detail"])


def test_the_event_is_the_stored_row_with_iso_times() -> None:
    agent, task = _agent(), _task()
    step = _step(task.id, agent, at=_T0, command="git status")
    assert task_step_event(step) == {
        "id": step.id, "task_id": task.id, "agent_id": agent.id, "agent_name": "Ettie", "action": "bm_cli",
        "note": "Run the tests", "command": "git status", "outcome": None, "result": None,
        "started_at": "2026-10-22T21:00:00+00:00", "ended_at": None,
    }
    finished = db.finish_task_step(step.id, outcome="ok", result="clean", ended_at=_T0 + timedelta(seconds=2))
    event = task_step_event(finished)
    assert (event["outcome"], event["result"], event["ended_at"]) == ("ok", "clean", "2026-10-22T21:00:02+00:00")


# ─── Routes ───


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _set(key: str, value: str) -> None:
    db.set_setting(key, value, "simulation")
    config.reload()


def test_recent_steps_read_their_count_from_the_setting() -> None:
    agent, task = _agent(), _task()
    steps = [_step(task.id, agent, at=_T0 + timedelta(seconds=index)) for index in range(4)]
    client, headers = _client()

    body = client.get(f"/api/tasks/{task.id}/steps/recent", headers=headers).json()
    assert body["limit"] == 5 and body["has_more"] is False
    assert [row["id"] for row in body["steps"]] == [step.id for step in reversed(steps)]
    assert body["steps"][0] == task_step_event(steps[-1])

    _set("task_steps_recent_count", "3")
    body = client.get(f"/api/tasks/{task.id}/steps/recent", headers=headers).json()
    assert body["limit"] == 3 and body["has_more"] is True
    assert [row["id"] for row in body["steps"]] == [step.id for step in reversed(steps[1:])]


def test_the_history_pages_at_the_setting_through_the_cursor() -> None:
    agent, task = _agent(), _task()
    steps = [_step(task.id, agent, at=_T0 + timedelta(seconds=index)) for index in range(5)]
    _set("task_steps_page_size", "2")
    client, headers = _client()
    url = f"/api/tasks/{task.id}/steps"

    page1 = client.get(url, headers=headers).json()
    assert [row["id"] for row in page1["steps"]] == [steps[4].id, steps[3].id] and page1["has_more"] is True
    page2 = client.get(url, params={"before": steps[3].id}, headers=headers).json()
    assert [row["id"] for row in page2["steps"]] == [steps[2].id, steps[1].id] and page2["has_more"] is True
    page3 = client.get(url, params={"before": steps[1].id}, headers=headers).json()
    assert page3 == {"steps": [task_step_event(steps[0])], "has_more": False}


def test_an_unknown_task_is_404_on_both_routes() -> None:
    client, headers = _client()
    for url in ("/api/tasks/nope/steps/recent", "/api/tasks/nope/steps"):
        response = client.get(url, headers=headers)
        assert response.status_code == 404, url
        assert response.json()["detail"] == "Task not found"


def test_a_cursor_that_is_not_a_step_of_this_task_is_400() -> None:
    agent, task, other = _agent(), _task(), _task("Another task")
    foreign = _step(other.id, agent, at=_T0)
    client, headers = _client()
    for cursor in ("missing-id", foreign.id):
        response = client.get(f"/api/tasks/{task.id}/steps", params={"before": cursor}, headers=headers)
        assert response.status_code == 400, cursor
        assert response.json()["detail"] == "Unknown cursor"


@pytest.mark.parametrize(
    ("key", "label", "url"),
    [
        ("task_steps_recent_count", "Recent Task Steps", "/api/tasks/{id}/steps/recent"),
        ("task_steps_page_size", "Task Steps Page Size", "/api/tasks/{id}/steps"),
    ],
)
def test_a_bad_count_setting_is_a_500_naming_its_label(
    caplog: pytest.LogCaptureFixture, key: str, label: str, url: str,
) -> None:
    task = _task()
    client, headers = _client()
    for bad in ("0", "-3", "many", ""):
        _set(key, bad)
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="api.routes.tasks"):
            response = client.get(url.format(id=task.id), headers=headers)
        assert response.status_code == 500, bad
        detail = response.json()["detail"]
        assert label in detail and key not in detail, bad
        assert any(
            record.levelname == "ERROR" and key in record.getMessage() for record in caplog.records
        ), bad


def test_both_settings_are_seeded() -> None:
    assert config.require_int("task_steps_recent_count") == 5
    assert config.require_int("task_steps_page_size") == 50


def test_both_settings_sit_together_in_the_simulation_tab() -> None:
    # The runtime settings shown in that tab hold 82, 84, 86 and 88; 89 and 90
    # follow them instead of interleaving.
    meta = (Path(__file__).resolve().parents[1] / "ui/static/js/settings/settings-system-meta.js").read_text(
        encoding="utf-8"
    )
    orders = {
        key: re.search(rf"\n\s+{key}: \{{\n\s+order: (\d+),", meta).group(1)
        for key in ("task_steps_recent_count", "task_steps_page_size")
    }
    assert orders == {"task_steps_recent_count": "89", "task_steps_page_size": "90"}


# ─── Agent deletion ───


def test_deleting_an_agent_keeps_its_steps_detached() -> None:
    ettie = agent_repository.create(name="Ettie")
    task = _task()
    step = _step(task.id, ettie, at=_T0, command="cargo test")

    agent_repository.delete(ettie.id)

    kept = db.get_task_step(step.id)
    assert kept is not None
    assert (kept.agent_id, kept.agent_name, kept.command) == (None, "Ettie", "cargo test")
