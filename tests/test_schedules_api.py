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


def test_a_reload_is_queued_as_a_db_command_whatever_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reload is a database command now (service.request_reload), written
    # from any process; a starting worker clears open commands and loads fresh.
    monkeypatch.setattr(runtime_services, "_process_is_running", lambda: False)
    ada = db.create_agent("Ada", role="Operator")
    assert client.post(f"/api/agents/{ada.id}/schedules", json=_body()).status_code == 201
    assert _open_reloads() == 1


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


EVERY_RULE = {"frequency": "daily", "interval": 1, "every_minutes": 15, "window_start": "09:00",
              "window_end": "17:00", "start_date": "2026-09-01"}


def test_an_every_rule_round_trips_and_has_a_next_run(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    created = client.post(f"/api/agents/{ada.id}/schedules", json=_body(recurrence=EVERY_RULE))
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["recurrence"]["times"] == []
    assert (view["recurrence"]["every_minutes"], view["recurrence"]["window_start"],
            view["recurrence"]["window_end"]) == (15, "09:00", "17:00")
    assert view["summary"] == "Every day, every 15 minutes from 09:00 to 17:00"
    next_run = datetime.fromisoformat(view["next_run_at"]).astimezone()
    assert next_run > datetime.now(timezone.utc)
    # A 15-minute slot inside the 09:00-17:00 window, on the host's clock.
    assert (9, 0) <= (next_run.hour, next_run.minute) <= (17, 0) and next_run.minute % 15 == 0

    patched = client.patch(f"/api/schedules/{view['id']}", json={"recurrence": {**EVERY_RULE, "every_minutes": 120}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["recurrence"]["every_minutes"] == 120
    assert patched.json()["summary"] == "Every day, every 2 hours from 09:00 to 17:00"
    listed = client.get(f"/api/agents/{ada.id}/schedules").json()
    assert listed[0]["recurrence"] == patched.json()["recurrence"]


def test_a_mixed_mode_rule_is_422(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    mixed = {**EVERY_RULE, "times": ["06:00"]}
    assert client.post(f"/api/agents/{ada.id}/schedules", json=_body(recurrence=mixed)).status_code == 422


# ─── Run now and the preview ───


def _created(client: TestClient, agent_id: str, **fields) -> dict:
    response = client.post(f"/api/agents/{agent_id}/schedules", json=_body(**fields))
    assert response.status_code == 201, response.text
    return response.json()


def test_run_now_creates_the_task_and_wakes_the_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[dict] = []

    async def _enqueue(**kwargs) -> None:
        enqueued.append(kwargs)

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _enqueue)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _created(client, ada.id, enabled=False)

    response = client.post(f"/api/schedules/{schedule['id']}/run")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["task"]["schedule_id"] == schedule["id"]
    assert body["task"]["description"] == "Log in and read it."
    assert body["schedule"]["last_outcome"] == "fired"
    assert body["schedule"]["last_task_id"] == body["task"]["id"]
    assert body["schedule"]["enabled"] is False
    assert [(item["trigger_type"], item["task_id"]) for item in enqueued] == [("task_assigned", body["task"]["id"])]


def test_run_now_refusals_are_409_with_a_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.floors import send_home

    async def _enqueue(**_kwargs) -> None:
        return None

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _enqueue)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _created(client, ada.id)
    first = client.post(f"/api/schedules/{schedule['id']}/run").json()

    still_open = client.post(f"/api/schedules/{schedule['id']}/run")
    assert still_open.status_code == 409
    assert still_open.json()["detail"] == {
        "reason": "open", "detail": "The last run is still open", "task_id": first["task"]["id"],
    }

    bob = db.create_agent("Bob", role="Operator")
    away = _created(client, bob.id)
    send_home(bob.id)
    vacation = client.post(f"/api/schedules/{away['id']}/run")
    assert vacation.status_code == 409
    assert vacation.json()["detail"] == {"reason": "vacation", "detail": "Bob is on vacation", "task_id": None}


def test_run_now_on_an_unknown_schedule_is_404(client: TestClient) -> None:
    assert client.post("/api/schedules/nope/run").status_code == 404


def test_run_now_does_not_queue_a_reload(
    client: TestClient, worker_running: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _enqueue(**_kwargs) -> None:
        return None

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _enqueue)
    ada = db.create_agent("Ada", role="Operator")
    schedule = _created(client, ada.id)
    for command in db.list_queued_runtime_commands():
        db.complete_runtime_command(command.id)
    assert client.post(f"/api/schedules/{schedule['id']}/run").status_code == 201
    assert _open_reloads() == 0


def test_preview_lists_the_draft_rules_next_runs(client: TestClient) -> None:
    response = client.post("/api/schedules/preview", json={"recurrence": RULE, "count": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == "Every weekday at 06:00, 12:00"
    runs = [datetime.fromisoformat(item) for item in body["next_runs"]]
    assert len(runs) == 5
    assert all(later > earlier for earlier, later in zip(runs, runs[1:]))
    assert runs[0] > datetime.now(timezone.utc)


@pytest.mark.parametrize(
    "body",
    [
        {"recurrence": {**RULE, "weekdays": []}, "count": 5},
        {"recurrence": RULE, "count": 0},
        {"recurrence": RULE, "count": 21},
        {"recurrence": RULE},
    ],
    ids=["bad-rule", "count-0", "count-21", "no-count"],
)
def test_preview_refuses_bad_drafts_with_422(client: TestClient, body: dict) -> None:
    assert client.post("/api/schedules/preview", json=body).status_code == 422


def test_preview_is_not_captured_by_a_schedule_id_route() -> None:
    from starlette.routing import Match

    scope = {"type": "http", "path": "/api/schedules/preview", "method": "POST"}
    full = [route for route in router.routes if route.matches(scope)[0] == Match.FULL]
    assert [route.name for route in full] == ["preview_schedule"]
    # And through the app, with a real schedule stored: the POST answers a
    # preview, not a lookup of a schedule whose id is "preview".
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    probe = TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})
    ada = db.create_agent("Ada", role="Operator")
    assert probe.post(f"/api/agents/{ada.id}/schedules", json=_body()).status_code == 201
    response = probe.post("/api/schedules/preview", json={"recurrence": RULE, "count": 1})
    assert response.status_code == 200 and "next_runs" in response.json()


# ─── The operator's lock and who set it up ───


def test_the_operator_creates_locked_and_can_open_or_lock_it(client: TestClient) -> None:
    ada = db.create_agent("Ada", role="Operator")
    view = _created(client, ada.id)
    assert (view["agent_can_change"], view["created_by"], view["created_by_name"]) == (False, "__human__", None)
    opened = client.patch(f"/api/schedules/{view['id']}", json={"agent_can_change": True})
    assert opened.status_code == 200, opened.text
    assert opened.json()["agent_can_change"] is True
    open_from_start = _created(client, ada.id, title="Open", agent_can_change=True)
    assert open_from_start["agent_can_change"] is True


def test_an_agent_created_schedule_names_its_agent(client: TestClient) -> None:
    from core.models.schedule import ScheduleCreate
    from core.scheduling.service import ScheduleActor, create_schedule

    ada = db.create_agent("Ada", role="Operator")
    create_schedule(
        ada.id, ScheduleCreate.model_validate(_body()), actor=ScheduleActor("agent", ada.id),
    )
    listed = client.get(f"/api/agents/{ada.id}/schedules").json()
    assert (listed[0]["created_by"], listed[0]["created_by_name"], listed[0]["agent_can_change"]) == (ada.id, "Ada", True)
