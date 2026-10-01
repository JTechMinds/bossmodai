"""Schedules API (api/routes/schedules.py): CRUD, errors, next run, and the worker reload."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.runtime import runtime_services
from core.scheduling.runner import ScheduleTiming, run_occurrence

RULE = {"frequency": "weekly", "interval": 1, "times": ["12:00", "06:00"], "weekdays": [0, 1, 2, 3, 4],
        "start_date": "2026-09-01"}


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


@pytest.fixture()
def worker_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app believes its worker process is up, so reloads are queued."""
    monkeypatch.setattr(runtime_services, "_process_is_running", lambda: True)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _body(**fields) -> dict:
    body = {"title": "Check the status page", "instructions": "Log in and read it.", "recurrence": RULE}
    body.update(fields)
    return body


def _open_reloads() -> int:
    return len([
        command for command in db.list_queued_runtime_commands()
        if command.command_type == "reload_schedules"
    ])


def test_create_list_update_delete(client: TestClient, worker_running: None) -> None:
    ada = db.create_agent("Ada", role="Operator")

    created = client.post(f"/api/agents/{ada.id}/schedules", json=_body())
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["agent_id"] == ada.id
    assert view["recurrence"]["times"] == ["06:00", "12:00"]
    assert view["notification_policy"] == "completion_blocked"
    assert view["enabled"] is True
    assert view["summary"] == "Every weekday at 06:00, 12:00"
    assert view["next_run_at"] is not None
    assert datetime.fromisoformat(view["next_run_at"]) > datetime.now(timezone.utc)
    assert view["last_outcome"] is None and view["last_task_status"] is None

    listed = client.get(f"/api/agents/{ada.id}/schedules")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [view["id"]]

    patched = client.patch(f"/api/schedules/{view['id']}", json={"enabled": False, "title": "  Status  "})
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False
    assert patched.json()["title"] == "Status"
    assert patched.json()["next_run_at"] is None
    assert patched.json()["instructions"] == "Log in and read it."

    deleted = client.delete(f"/api/schedules/{view['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/agents/{ada.id}/schedules").json() == []


def test_unknown_agent_and_schedule_are_404(client: TestClient) -> None:
    assert client.get("/api/agents/nope/schedules").status_code == 404
    assert client.post("/api/agents/nope/schedules", json=_body()).status_code == 404
    assert client.patch("/api/schedules/nope", json={"enabled": False}).status_code == 404
    assert client.delete("/api/schedules/nope").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        _body(recurrence={**RULE, "weekdays": []}),
        _body(recurrence={**RULE, "frequency": "monthly", "weekdays": []}),
        _body(recurrence={**RULE, "times": ["06:00", "06:00"]}),
        _body(recurrence={**RULE, "interval": 0}),
        _body(title="   "),
        _body(extra="field"),
    ],
)
def test_bad_bodies_are_422(client: TestClient, body: dict) -> None:
    ada = db.create_agent("Ada", role="Operator")
    assert client.post(f"/api/agents/{ada.id}/schedules", json=body).status_code == 422


def test_bad_edits_are_422(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule_id = client.post(f"/api/agents/{ada.id}/schedules", json=_body()).json()["id"]
    for body in ({}, {"title": ""}, {"enabled": None}, {"recurrence": {**RULE, "weekdays": []}}):
        assert client.patch(f"/api/schedules/{schedule_id}", json=body).status_code == 422, body


def test_the_view_carries_the_last_run_and_its_task_status(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule_id = client.post(f"/api/agents/{ada.id}/schedules", json=_body()).json()["id"]
    due = datetime.now(timezone.utc) - timedelta(seconds=1)
    run = run_occurrence(schedule_id, due_at=due, now=due,
                         timing=ScheduleTiming(max_sleep_seconds=60, grace_seconds=120))
    assert run.outcome == "fired"

    view = client.get(f"/api/agents/{ada.id}/schedules").json()[0]
    assert view["last_outcome"] == "fired"
    assert view["last_task_id"] == run.task.id
    assert view["last_task_status"] == "pending"

    # The run is an ordinary task on the board, carrying its schedule.
    listed = client.get("/api/tasks").json()
    assert [task["schedule_id"] for task in listed if task["id"] == run.task.id] == [schedule_id]


def test_delete_detaches_the_tasks_its_runs_created(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    schedule_id = client.post(f"/api/agents/{ada.id}/schedules", json=_body()).json()["id"]
    due = datetime.now(timezone.utc)
    run = run_occurrence(schedule_id, due_at=due, now=due,
                         timing=ScheduleTiming(max_sleep_seconds=60, grace_seconds=120))

    assert client.delete(f"/api/schedules/{schedule_id}").status_code == 204

    task = db.get_task(run.task.id)
    assert task is not None and task.schedule_id is None


def test_every_mutation_queues_one_reload_while_the_worker_runs(
    client: TestClient, worker_running: None,
) -> None:
    ada = db.create_agent("Ada", role="Operator")
    assert _open_reloads() == 0
    first = client.post(f"/api/agents/{ada.id}/schedules", json=_body()).json()
    assert _open_reloads() == 1
    client.post(f"/api/agents/{ada.id}/schedules", json=_body(title="Second"))
    client.patch(f"/api/schedules/{first['id']}", json={"enabled": False})
    client.delete(f"/api/schedules/{first['id']}")
    # De-duplicated: one open command however many edits piled up.
    assert _open_reloads() == 1

    # Once the worker has handled it, the next edit queues a fresh one.
    for command in db.list_queued_runtime_commands():
        db.complete_runtime_command(command.id)
    assert _open_reloads() == 0
    client.post(f"/api/agents/{ada.id}/schedules", json=_body(title="Third"))
    assert _open_reloads() == 1


def test_no_reload_is_queued_while_no_worker_runs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Pinned rather than read from the process-wide singleton, which an
    # earlier test in the run may have left holding a process handle.
    monkeypatch.setattr(runtime_services, "_process_is_running", lambda: False)
    ada = db.create_agent("Ada", role="Operator")
    assert client.post(f"/api/agents/{ada.id}/schedules", json=_body()).status_code == 201
    assert _open_reloads() == 0


def test_every_mutation_announces_schedule_changed_for_the_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.websocket import manager

    seen: list[tuple[str, dict]] = []

    async def record(event, detail, agent_name=None, extra=None):
        seen.append((event, extra or {}))

    monkeypatch.setattr(manager, "broadcast_activity", record)
    ada = db.create_agent("Ada", role="Operator")
    schedule_id = client.post(f"/api/agents/{ada.id}/schedules", json=_body()).json()["id"]
    client.patch(f"/api/schedules/{schedule_id}", json={"title": "Renamed"})
    client.delete(f"/api/schedules/{schedule_id}")
    assert [event for event, _ in seen] == ["schedule_changed"] * 3
    assert all(extra == {"agent_id": ada.id, "schedule_id": schedule_id} for _, extra in seen)
