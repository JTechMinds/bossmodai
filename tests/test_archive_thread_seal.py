"""Archived threads are sealed: no writes, denied origin consent, non-live UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.notifications import persist_channel_notification, ChatNotification
from core.models.channel import (
    ChannelArchivedError,
    THREAD_ARCHIVED_CANCEL_LINE,
    THREAD_ARCHIVED_CONSENT_DENY,
)
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task


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


def _api_client(monkeypatch: pytest.MonkeyPatch | None = None) -> TestClient:
    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    if monkeypatch is not None:
        monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _channel_task(*, assignee_id: str, channel_id: str, title: str):
    return create_or_bind_task(
        title=title,
        description="Archive seal fixture.",
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


def _pending_consent(*, agent_id: str, channel_id: str, task_id: str | None):
    return db.create_consent_request(
        agent_id=agent_id,
        path="/tmp/archive-seal/notes.txt",
        grant_root="/tmp/archive-seal",
        reason="Need the host file",
        task_id=task_id,
        channel_id=channel_id,
    )


def test_archived_thread_rejects_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    before = len(db.list_channel_messages(channel.id))

    archived = client.delete(f"/api/channels/{channel.id}", headers=_headers())
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    blocked = client.post(
        f"/api/channels/{channel.id}/messages",
        headers=_headers(),
        json={"content": "still typing"},
    )
    assert blocked.status_code == 404
    with pytest.raises(ChannelArchivedError):
        db.create_channel_message(
            channel_id=channel.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name="Ada",
            content="looping into a dead room",
            source_channel="channel",
        )
    posted = persist_channel_notification(
        agent,
        ChatNotification(
            kind="task_update",
            content="Stalled — should not land",
            source_channel="channel",
            policy="all",
            channel_id=channel.id,
        ),
    )
    assert posted == {}
    assert len(db.list_channel_messages(channel.id)) == before
    assert db.is_channel_archived(channel.id)
    assert db.payload_targets_archived_channel({"channel_id": channel.id})


def test_cancel_tasks_and_archive_denies_pending_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Share review findings")
    assert creation.task is not None
    activity_runtime.activate_work_activity(agent.id, creation.task)
    consent = _pending_consent(agent_id=agent.id, channel_id=channel.id, task_id=creation.task.id)
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="channel_message",
        source_channel="channel",
        payload={"channel_id": channel.id, "content": "keep going"},
        task_id=creation.task.id,
    )

    response = client.post(
        f"/api/channels/{channel.id}/archive",
        headers=_headers(),
        params={"cancel_open_tasks": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"

    stored_task = db.get_task(creation.task.id)
    assert stored_task is not None
    assert stored_task.status == "cancelled"
    resolved = db.get_consent_request(consent.id)
    assert resolved is not None
    assert resolved.status == "denied"
    assert resolved.decision_note == THREAD_ARCHIVED_CONSENT_DENY
    assert resolved.as_card()["decision_note"] == THREAD_ARCHIVED_CONSENT_DENY
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert THREAD_ARCHIVED_CANCEL_LINE in contents
    assert "Cancelled — Operator cancelled" in contents
    open_activities = [
        item for item in db.list_activities(task_id=creation.task.id, limit=20) if item.status in {"active", "paused"}
    ]
    assert open_activities == []
    queued = [row for row in db.list_agent_triggers(agent.id) if row["status"] == "queued"]
    assert queued == []
    assert client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers()).json()["count"] == 0


def test_archive_only_seals_thread_but_leaves_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    live = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Keep this open")
    done = _channel_task(assignee_id=agent.id, channel_id=channel.id, title="Already finished")
    assert live.task is not None and done.task is not None
    transition_task(done.task.id, "accepted", reason="setup", actor="pytest")
    transition_task(done.task.id, "active", reason="setup", actor="pytest")
    transition_task(done.task.id, "complete", reason="finished", actor="pytest")
    consent = _pending_consent(agent_id=agent.id, channel_id=channel.id, task_id=live.task.id)
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="channel_response",
        source_channel="channel",
        payload={"channel_id": channel.id, "content": "reply"},
        task_id=live.task.id,
    )

    archived = client.delete(f"/api/channels/{channel.id}", headers=_headers())
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert db.get_task(live.task.id).status == "pending"
    assert db.get_task(done.task.id).status == "complete"
    after = client.get(f"/api/channels/{channel.id}/open-tasks", headers=_headers())
    assert after.status_code == 200
    assert after.json()["count"] == 1
    assert after.json()["tasks"][0]["id"] == live.task.id

    resolved = db.get_consent_request(consent.id)
    assert resolved is not None
    assert resolved.status == "denied"
    assert resolved.decision_note == THREAD_ARCHIVED_CONSENT_DENY
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert THREAD_ARCHIVED_CANCEL_LINE not in contents

    queued = [row for row in db.list_agent_triggers(agent.id) if row["status"] == "queued"]
    channel_bound = []
    resumes = []
    for row in queued:
        payload = json.loads(row["payload"]) if row.get("payload") else {}
        if str(payload.get("channel_id") or "").strip() == channel.id:
            channel_bound.append(row)
        if row.get("trigger_type") == "host_path_consent_resolved":
            resumes.append(payload)
    assert channel_bound == []
    assert len(resumes) == 1
    assert "channel_id" not in resumes[0]
    assert resumes[0]["decision_note"] == THREAD_ARCHIVED_CONSENT_DENY
    assert resumes[0]["status"] == "denied"

    blocked = client.post(
        f"/api/channels/{channel.id}/messages",
        headers=_headers(),
        json={"content": "new card please"},
    )
    assert blocked.status_code == 404
    listed = client.get("/api/channels", headers=_headers())
    assert listed.status_code == 200
    assert listed.json() == []
    hidden = client.get("/api/channels", headers=_headers(), params={"status": "archived"})
    assert hidden.status_code == 200
    assert hidden.json()[0]["id"] == channel.id


def test_reopen_unseals_archived_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    agent = db.create_agent("Reviewer", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Planning", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    archived = client.delete(f"/api/channels/{channel.id}", headers=_headers())
    assert archived.status_code == 200
    blocked = client.post(
        f"/api/channels/{channel.id}/messages",
        headers=_headers(),
        json={"content": "still sealed"},
    )
    assert blocked.status_code == 404

    opened = client.post(f"/api/channels/{channel.id}/reopen", headers=_headers())
    assert opened.status_code == 200
    assert opened.json()["status"] == "active"
    assert opened.json()["archived_at"] is None
    assert not db.is_channel_archived(channel.id)

    posted = client.post(
        f"/api/channels/{channel.id}/messages",
        headers=_headers(),
        json={"content": "room is live again"},
    )
    assert posted.status_code == 200
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "room is live again" in contents
