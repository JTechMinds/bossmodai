"""Operator task actions: edit, reassign, change requirements, resume, mark complete.

Drives PATCH /api/tasks/{id}, POST /api/tasks/{id}/resume, /complete and
/cancel through the real router against the isolated test database
(conftest.py). No LLM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking.transitions import transition_task
from db.floors import create_floor


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


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _queued(agent_id: str, *, trigger_type: str, task_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == trigger_type and row["status"] == "queued" and row["task_id"] == task_id
    ]


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row["payload"]
    return raw if isinstance(raw, dict) else json.loads(raw)


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/tasks", headers=_headers(), json=body)
    assert response.status_code == 201, response.text
    return response.json()["task"]


def _move(task_id: str, *statuses: str) -> None:
    for status in statuses:
        transition_task(task_id, status, reason=f"Test moved it to {status}.", actor="Test")


def test_title_only_edit_changes_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    _move(task["id"], "accepted", "active")
    db.delete_queued_triggers_for_task(task["id"])

    response = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"title": "Write the launch note"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Write the launch note"
    assert body["status"] == "active"
    assert _queued(writer.id, trigger_type="task_assigned", task_id=task["id"]) == []
    events = db.list_task_events(task["id"], limit=50)
    assert any(item.content == "Operator edited the task: title." for item in events)


def test_requirements_change_re_presents_a_stalled_task(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    _move(task["id"], "accepted", "active")
    activity = activate_work_activity(writer.id, db.get_task(task["id"]))
    _move(task["id"], "stalled")

    response = client.patch(
        f"/api/tasks/{task['id']}",
        headers=_headers(),
        json={"work_contract": {"deliverables": [
            {"type": "file", "path": "/me/out/a.md"}, {"type": "file", "path": "/me/out/b.md"},
        ]}},
    )

    assert response.status_code == 200, response.text
    stored = db.get_task(task["id"])
    assert stored is not None
    assert stored.status == "pending"
    assert [item.path for item in stored.work_contract.deliverables] == ["/me/out/a.md", "/me/out/b.md"]
    assert db.get_activity(activity.id).status == "cancelled"
    assert len(_queued(writer.id, trigger_type="task_assigned", task_id=task["id"])) == 1
    events = db.list_task_events(task["id"], limit=50)
    assert any(item.content == "Operator edited the task: requirements (2 files)." for item in events)


def test_reassign_moves_assignee_and_owner_and_posts_rerouted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    first = db.create_agent("Cap One", role="Writer", desk_x=1, desk_y=1)
    second = db.create_agent("Cap Two", role="Writer", desk_x=2, desk_y=1)
    channel = db.create_channel(name="launch", member_agent_ids=[first.id, second.id], created_by=HUMAN_SENDER_ID)
    task = _create(
        client,
        title="Write the note",
        assigned_to=first.id,
        source_channel="channel",
        notification_channel_id=channel.id,
    )
    assert task["owner_id"] == first.id
    _move(task["id"], "accepted", "active")

    response = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"assigned_to": second.id})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assigned_to"] == second.id
    assert body["owner_id"] == second.id
    assert body["status"] == "pending"
    assert len(_queued(second.id, trigger_type="task_assigned", task_id=task["id"])) == 1
    assert _queued(first.id, trigger_type="task_assigned", task_id=task["id"]) == []
    lines = [item.content for item in db.list_channel_messages(channel.id, limit=20)]
    assert "Cap One Rerouted to Cap Two — Reassigned by the operator" in lines


def test_reassign_specialty_mismatch_is_409_until_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=2, desk_y=1)
    task = _create(
        client,
        title="Review the security audit",
        description="Audit the package and report findings.",
        assigned_to=auditor.id,
    )

    denied = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"assigned_to": writer.id})

    assert denied.status_code == 409
    body = denied.json()
    assert body["outcome"] == "specialty_mismatch"
    assert "Writer" in body["reason"]
    assert [item["id"] for item in body["suggested_assignees"]] == [auditor.id]
    assert db.get_task(task["id"]).assigned_to == auditor.id

    confirmed = client.patch(
        f"/api/tasks/{task['id']}",
        headers=_headers(),
        json={"assigned_to": writer.id, "confirm_specialty_mismatch": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["assigned_to"] == writer.id


def test_cross_floor_reassign_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    finance = create_floor("Finance")
    first = db.create_agent("Cap One", role="Writer", desk_x=1, desk_y=1)
    second = db.create_agent("Cap Two", role="Writer", desk_x=2, desk_y=1)
    elsewhere = db.create_agent("Cap Away", role="Writer", desk_x=3, desk_y=1, floor_id=finance.id)
    channel = db.create_channel(name="launch", member_agent_ids=[first.id, second.id], created_by=HUMAN_SENDER_ID)
    task = _create(
        client,
        title="Write the note",
        assigned_to=first.id,
        source_channel="channel",
        notification_channel_id=channel.id,
    )

    response = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"assigned_to": elsewhere.id})

    assert response.status_code == 403
    assert db.get_task(task["id"]).assigned_to == first.id


def test_edit_of_a_closed_task_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    assert client.post(f"/api/tasks/{task['id']}/cancel", headers=_headers()).status_code == 200

    response = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"title": "Too late"})

    assert response.status_code == 409


def test_edit_needs_a_field_and_a_real_title(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)

    empty = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={})
    blank = client.patch(f"/api/tasks/{task['id']}", headers=_headers(), json={"title": "   "})

    assert empty.status_code == 422
    assert blank.status_code == 422


def test_operator_complete_closes_the_task_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(
        client,
        title="Write the note",
        assigned_to=writer.id,
        work_contract={"deliverables": [{"type": "file", "path": "/me/never-written.md"}]},
    )
    _move(task["id"], "accepted", "active")
    listed = {row["id"]: row for row in client.get("/api/tasks", headers=_headers()).json()}
    assert listed[task["id"]]["operator_can_complete"] is True

    response = client.post(
        f"/api/tasks/{task['id']}/complete", headers=_headers(), json={"summary": "Delivered by email."}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    assert body["completion_summary"] == "Delivered by email."
    assert body["closed_at"]
    assert body["operator_can_complete"] is False
    events = db.list_task_events(task["id"], limit=50)
    assert any(item.content == "Marked complete by the operator: Delivered by email." for item in events)

    again = client.post(f"/api/tasks/{task['id']}/complete", headers=_headers(), json={"summary": "Again."})
    assert again.status_code == 200
    assert again.json()["completion_summary"] == "Delivered by email."


def test_operator_complete_of_a_pending_task_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    listed = {row["id"]: row for row in client.get("/api/tasks", headers=_headers()).json()}
    assert listed[task["id"]]["operator_can_complete"] is False

    response = client.post(f"/api/tasks/{task['id']}/complete", headers=_headers(), json={"summary": "Done."})
    blank = client.post(f"/api/tasks/{task['id']}/complete", headers=_headers(), json={"summary": "  "})

    assert response.status_code == 409
    assert blank.status_code == 422
    assert db.get_task(task["id"]).status == "pending"


def test_operator_complete_with_open_children_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    lead = db.create_agent("Cap Lead", role="Writer", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    parent = _create(client, title="Write the launch", assigned_to=lead.id)
    child = _create(client, title="Write the launch note", assigned_to=worker.id, parent_task_id=parent["id"])
    _move(parent["id"], "accepted", "active")

    response = client.post(f"/api/tasks/{parent['id']}/complete", headers=_headers(), json={"summary": "Done."})

    assert response.status_code == 409
    assert response.json()["task_ids"] == [child["id"]]
    assert db.get_task(parent["id"]).status == "active"


def test_operator_complete_of_a_child_tells_the_parent_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    lead = db.create_agent("Cap Lead", role="Writer", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    parent = _create(client, title="Write the launch", assigned_to=lead.id)
    child = _create(client, title="Write the launch note", assigned_to=worker.id, parent_task_id=parent["id"])
    _move(child["id"], "accepted")

    response = client.post(
        f"/api/tasks/{child['id']}/complete", headers=_headers(), json={"summary": "Note is in the doc."}
    )

    assert response.status_code == 200, response.text
    updates = _queued(lead.id, trigger_type="task_update", task_id=parent["id"])
    assert len(updates) == 1
    payload = _payload(updates[0])
    assert payload["attention_kind"] == "completion_report"
    assert payload["content"] == 'Child task "Write the launch note" marked complete by the operator: Note is in the doc.'


def test_resume_hands_a_stalled_task_back_to_its_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    _move(task["id"], "accepted", "active")
    activity = activate_work_activity(writer.id, db.get_task(task["id"]))
    _move(task["id"], "stalled")
    listed = next(row for row in client.get("/api/tasks", headers=_headers()).json() if row["id"] == task["id"])
    assert listed["operator_can_resume"] is True

    response = client.post(f"/api/tasks/{task['id']}/resume", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["operator_can_resume"] is False
    assert body["assigned_to_name"] == "Cap Writer"
    assert db.get_task(task["id"]).status == "pending"
    assert db.get_activity(activity.id).status == "cancelled"
    assert len(_queued(writer.id, trigger_type="task_assigned", task_id=task["id"])) == 1
    events = db.list_task_events(task["id"], limit=50)
    assert any(item.content == "Operator resumed the task." for item in events)


def test_resume_refuses_an_active_a_closed_or_an_unassigned_task(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    active = _create(client, title="Write the note", assigned_to=writer.id)
    _move(active["id"], "accepted", "active")
    closed = _create(client, title="Write the launch note", assigned_to=writer.id)
    assert client.post(f"/api/tasks/{closed['id']}/cancel", headers=_headers()).status_code == 200
    unassigned = _create(client, title="Write the recap")
    _move(unassigned["id"], "stalled")

    busy = client.post(f"/api/tasks/{active['id']}/resume", headers=_headers())
    over = client.post(f"/api/tasks/{closed['id']}/resume", headers=_headers())
    nobody = client.post(f"/api/tasks/{unassigned['id']}/resume", headers=_headers())
    missing = client.post("/api/tasks/no-such-task/resume", headers=_headers())

    assert busy.status_code == 400
    assert busy.json()["detail"] == "Only a blocked or stalled task with an assignee can be resumed"
    assert db.get_task(active["id"]).status == "active"
    assert over.status_code == 409
    assert db.get_task(closed["id"]).status == "cancelled"
    assert nobody.status_code == 400
    assert db.get_task(unassigned["id"]).status == "stalled"
    assert missing.status_code == 404


def test_single_cancel_returns_the_listed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _create(client, title="Write the note", assigned_to=writer.id)
    listed = next(row for row in client.get("/api/tasks", headers=_headers()).json() if row["id"] == task["id"])

    response = client.post(f"/api/tasks/{task['id']}/cancel", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == set(listed)
    assert body["status"] == "cancelled"
    assert body["assigned_to_name"] == "Cap Writer"
    assert body["operator_can_complete"] is False
    assert body["operator_can_resume"] is False


def test_a_dm_origin_create_pushes_its_created_line_live(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.websocket import manager

    sent: list[dict[str, Any]] = []

    async def _spy(**data: Any) -> None:
        sent.append(data)

    monkeypatch.setattr(manager, "broadcast_chat_message", _spy)
    client = _client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)

    task = _create(
        client, title="Write the note", assigned_to=writer.id,
        source_channel="chat", notification_policy="completion_blocked",
    )

    assert [item["agent_id"] for item in sent] == [writer.id]
    assert "Write the note" in sent[0]["content"]
    assert sent[0]["message_id"]
    assert db.get_task(task["id"]).source_channel == "chat"
