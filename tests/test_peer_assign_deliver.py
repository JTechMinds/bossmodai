"""Capability pass item (3) — peer assign → wake → accept → deliver.

Uses the same actions/decisions/triggers the live loop calls. No LLM.
Fixture names stay impersonal.
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
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_runtime import apply_decision
from core.bm_cli.virtual_fs import resolve_cli_path
from core.runtime import runtime_services


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


def _task_api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _queued(agent_id: str, *, trigger_type: str, task_id: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == trigger_type and row["status"] == "queued"
    ]
    if task_id is not None:
        rows = [row for row in rows if row["task_id"] == task_id]
    return rows


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _file_text(storage_key: str, virtual_path: str) -> str:
    resolved = resolve_cli_path(storage_key, "/me", virtual_path)
    assert resolved.real_path is not None
    assert resolved.exists, f"missing deliverable {virtual_path}"
    return resolved.real_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_peer_delegate_assign_accept_write_complete_wakes_assigner() -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    assigner_state = db.get_agent_state(assigner.id)
    worker_state = db.get_agent_state(worker.id)
    assert assigner_state is not None
    assert worker_state is not None

    assigned = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker.id,
            "taskTitle": "Write cap status note",
            "taskDescription": "Write a short status note for the capability pass.",
            "project": "cap-peer",
            "deliverables": [
                {"type": "file", "path": "/me/status-note.md", "description": "Status note"},
            ],
        },
        assigner,
        assigner_state,
    )
    assert assigned["event"] == "status_changed"
    persist_result_triggers(assigned)

    tasks = db.list_tasks(assigned_to=worker.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == "pending"
    assert task.requester_id == assigner.id
    assert task.work_contract is not None
    deliverable_path = task.work_contract.deliverables[0].path
    assert deliverable_path.startswith(f"/projects/cap-peer/{task.id}/")
    assert deliverable_path.endswith("status-note.md")

    wakes = _queued(worker.id, trigger_type="task_assigned", task_id=task.id)
    assert len(wakes) == 1
    wake_payload = _payload(wakes[0])
    assert wake_payload.get("from_agent") == assigner.id
    assert wake_payload.get("from_name") == "Cap Assigner"

    accepted = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": task.title,
            "reply": "I will write the status note.",
        },
        worker,
        worker_state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "content": task.description,
            "from_name": assigner.name,
            "from_agent": assigner.id,
        },
    )
    assert accepted["event"] == "decision_applied"
    persist_result_triggers(accepted)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"

    written = await execute_action(
        {
            "action": "bm_cli",
            "command": f"write {deliverable_path}",
            "content": "# Cap status note\nPeer assign/deliver loop completed.\n",
        },
        worker,
        worker_state,
    )
    assert written["event"] == "bm_cli_result"

    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the status note.",
            "followUpMessage": "Status note is in the shared project folder.",
        },
        worker,
        worker_state,
    )
    assert completed["event"] == "status_changed"
    persist_result_triggers(completed)

    done = db.get_task(task.id)
    assert done is not None
    assert done.status == "complete"
    assert "Peer assign/deliver loop completed." in _file_text(worker.storage_key, deliverable_path)

    observer_triggers = _queued(assigner.id, trigger_type="task_update", task_id=task.id)
    assert observer_triggers
    notes = db.list_notifications(agent_id=assigner.id)
    assert any(item.task_id == task.id for item in notes)
    events = db.list_task_events(task.id)
    assert any(event.event_type == "completion" for event in events)
    assert any(event.event_type == "status_update" and "pending → accepted" in event.content for event in events)
    assert any(event.event_type == "status_update" and "→ complete" in event.content for event in events)


@pytest.mark.asyncio
async def test_peer_assignee_decline_wakes_assigner_and_stays_declined() -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    assigner_state = db.get_agent_state(assigner.id)
    worker_state = db.get_agent_state(worker.id)
    assert assigner_state is not None
    assert worker_state is not None

    assigned = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker.id,
            "taskTitle": "Write declined note",
            "taskDescription": "This assignment should be declined.",
            "project": "cap-peer",
            "deliverables": [
                {"type": "file", "path": "/me/declined-note.md", "description": "Should not exist"},
            ],
        },
        assigner,
        assigner_state,
    )
    persist_result_triggers(assigned)
    task = db.list_tasks(assigned_to=worker.id)[0]
    deliverable_path = task.work_contract.deliverables[0].path if task.work_contract else ""

    declined = apply_decision(
        {
            "decision": "decline",
            "intentKind": "work_request",
            "commitmentKind": "none",
            "reply": "I cannot take this assignment.",
        },
        worker,
        worker_state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "content": task.description,
            "from_name": assigner.name,
            "from_agent": assigner.id,
        },
    )
    assert declined["event"] == "decision_applied"
    persist_result_triggers(declined)

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "declined"
    follow_ups = _queued(assigner.id, trigger_type="task_follow_up", task_id=task.id)
    assert follow_ups
    assert "cannot take this assignment" in _payload(follow_ups[0]).get("content", "").lower()
    resolved = resolve_cli_path(worker.storage_key, "/me", deliverable_path)
    assert not resolved.exists


def test_work_plan_ambiguous_assignee_name_does_not_create_child() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    db.create_agent("Cap Worker", role="Editor", desk_x=3, desk_y=1)
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate cap note", assigned_to=lead.id, created_by=lead.id)

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Worker can take the note.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [{"agentName": "Cap Worker", "taskTitle": "Write cap status note"}],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Coordinate the note",
            "from_name": "Operator",
        },
    )

    assert result["event"] == "world_feedback"
    assert "more than one teammate" in result["detail"].lower()
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert db.list_tasks(parent_task_id=parent.id) == []


def test_create_task_api_peer_requester_queues_wake_and_lists_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    client = _task_api_client(monkeypatch)
    headers = _headers()

    created = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Write cap status note",
            "description": "Write a short status note for the capability pass.",
            "project": "cap-peer",
            "assigned_to": worker.id,
            "requester_id": assigner.id,
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": "/me/status-note.md", "description": "Status note"},
                ]
            },
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["outcome"] == "create_new_task"
    task = body["task"]
    assert task["assigned_to"] == worker.id
    assert task["requester_id"] == assigner.id
    assert task["work_contract"]["deliverables"][0]["path"].startswith(f"/projects/cap-peer/{task['id']}/")

    triggers = client.get(f"/api/agents/{worker.id}/triggers", headers=headers)
    assert triggers.status_code == 200
    rows = triggers.json()
    assert any(
        row["trigger_type"] == "task_assigned"
        and row["task_id"] == task["id"]
        and row["status"] == "queued"
        and row["payload"].get("from_agent") == assigner.id
        for row in rows
    )


def test_create_task_api_unknown_assignee_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _task_api_client(monkeypatch)
    response = client.post(
        "/api/tasks",
        headers=_headers(),
        json={"title": "Orphan assign", "assigned_to": "missing-agent"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Assigned agent not found"
    assert db.list_tasks() == []


def test_get_agent_triggers_unknown_agent_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _task_api_client(monkeypatch)
    response = client.get("/api/agents/missing-agent/triggers", headers=_headers())
    assert response.status_code == 404
