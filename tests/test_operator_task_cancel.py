"""Operator kill path: cancel, bulk cancel, archive does not silently kill tasks."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import TERMINAL_TASK_STATUSES, is_terminal_task_status, transition_task


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


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _channel_task(*, assignee_id: str, channel_id: str, title: str):
    return create_or_bind_task(
        title=title,
        description="Operator kill-path fixture.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def test_terminal_set_hides_done_cancelled_declined_abandoned() -> None:
    assert TERMINAL_TASK_STATUSES == frozenset({"complete", "abandoned", "declined", "cancelled"})
    assert is_terminal_task_status("cancelled")
    assert not is_terminal_task_status("active")
    assert not is_terminal_task_status("delegated")


def test_cancel_task_sets_cancelled_and_posts_locked_origin_line() -> None:
    client = _api_client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Share review findings")
    assert creation.task is not None
    activity_runtime.activate_work_activity(agent.id, creation.task)
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="task_assigned",
        source_channel="channel",
        payload={"task_id": creation.task.id},
        task_id=creation.task.id,
    )

    response = client.post(f"/api/tasks/{creation.task.id}/cancel", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["status_note"] == "Operator cancelled"

    stored = db.get_task(creation.task.id)
    assert stored is not None
    assert stored.status == "cancelled"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Cancelled — Operator cancelled" in contents
    events = db.list_task_events(creation.task.id)
    assert any("→ cancelled" in event.content for event in events if event.event_type == "status_update")
    open_activities = [
        item for item in db.list_activities(task_id=creation.task.id, limit=20) if item.status in {"active", "paused"}
    ]
    assert open_activities == []
    queued = [row for row in db.list_agent_triggers(agent.id) if row["status"] == "queued" and row["task_id"] == creation.task.id]
    assert queued == []


def test_cancel_is_idempotent_and_rejects_other_terminal() -> None:
    client = _api_client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    live = db.create_task(title="Live work", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    done = db.create_task(title="Already done", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    transition_task(done.id, "accepted", reason="setup", actor="pytest")
    transition_task(done.id, "active", reason="setup", actor="pytest")
    transition_task(done.id, "complete", reason="finished", actor="pytest")

    first = client.post(f"/api/tasks/{live.id}/cancel", headers=_headers())
    assert first.status_code == 200
    again = client.post(f"/api/tasks/{live.id}/cancel", headers=_headers())
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"

    blocked = client.post(f"/api/tasks/{done.id}/cancel", headers=_headers())
    assert blocked.status_code == 409
    assert db.get_task(done.id).status == "complete"


def test_bulk_cancel_cancels_each_selected_task() -> None:
    client = _api_client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    first = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Write the brief")
    second = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Write the recap")
    assert first.task is not None and second.task is not None

    response = client.post(
        "/api/tasks/cancel",
        headers=_headers(),
        json={"task_ids": [first.task.id, second.task.id]},
    )
    assert response.status_code == 200
    body = response.json()
    assert {row["id"] for row in body} == {first.task.id, second.task.id}
    assert all(row["status"] == "cancelled" for row in body)
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Cancelled — Operator cancelled") == 2


def test_archive_leaves_open_tasks_until_operator_cancels() -> None:
    client = _api_client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    live = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Keep this open")
    done = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Already finished")
    assert live.task is not None and done.task is not None
    transition_task(done.task.id, "accepted", reason="setup", actor="pytest")
    transition_task(done.task.id, "active", reason="setup", actor="pytest")
    transition_task(done.task.id, "complete", reason="finished", actor="pytest")

    listed = client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers())
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["count"] == 1
    assert payload["tasks"][0]["id"] == live.task.id

    archived = client.delete(f"/api/channels/{channel.id}", headers=_headers())
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert db.get_task(live.task.id).status == "pending"
    assert db.get_task(done.task.id).status == "complete"
    after = client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers())
    assert after.status_code == 200
    assert after.json()["count"] == 1


def test_cancel_then_archive_closes_open_origin_tasks() -> None:
    client = _api_client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    live = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Close with the thread")
    assert live.task is not None

    opened = client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers()).json()
    cancelled = client.post(
        "/api/tasks/cancel",
        headers=_headers(),
        json={"task_ids": [row["id"] for row in opened["tasks"]]},
    )
    assert cancelled.status_code == 200
    archived = client.delete(f"/api/channels/{channel.id}", headers=_headers())
    assert archived.status_code == 200
    assert db.get_task(live.task.id).status == "cancelled"
    assert client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers()).json()["count"] == 0
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Cancelled — Operator cancelled" in contents
