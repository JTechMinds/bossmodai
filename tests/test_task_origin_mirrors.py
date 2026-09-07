"""Origin-thread visibility: accept / status / claim post one-liners.

Work moving while the origin thread stays quiet is the same class of miss
as empty done. Event-driven mirrors only — no heartbeat or section spam.
"""

from __future__ import annotations

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
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action
from core.agent_loop.channel_rounds import begin_channel_response
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.notifications import emit_chat_notifications, project_chat_notifications
from core.agent_loop.task_origin_mirrors import persist_origin_status_line
from core.agent_loop.turn_helpers import _build_managed_writer_progress_reporter
from core.agent_loop.watchdog import TaskWatchdog
from core.bm_cli.managed_writer import ManagedWriteProgress
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


def _channel_task(*, assignee_id: str, channel_id: str, title: str = "Share review findings"):
    return create_or_bind_task(
        title=title,
        description="Post the review summary for the team.",
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


def _chat_task(*, assignee_id: str, title: str = "Write the weekly report"):
    return create_or_bind_task(
        title=title,
        description="Draft it in Focus.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="chat",
        notification_policy="completion_blocked",
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _round_count(channel_id: str) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM channel_response_rounds WHERE channel_id = $1",
        [channel_id],
    )
    return int(rows[0]["n"]) if rows else 0


def _queued_channel_messages(agent_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == "channel_message" and row["status"] == "queued"
    ]


def test_accept_without_reply_posts_origin_line_and_flips_accepted() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    before = _round_count(channel.id)

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": creation.task.title,
        },
        jimothy,
        state,
        {
            "type": "task_assigned",
            "task_id": creation.task.id,
            "content": "Share the review findings.",
            "from_name": "Human Operator",
        },
    )

    assert result["event"] == "decision_applied"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert result.get("channel_message")
    assert result["channel_message"]["author_type"] == "system"
    assert "accepted" in result["channel_message"]["content"].lower()
    assert creation.task.title in result["channel_message"]["content"]
    messages = db.list_channel_messages(channel.id)
    assert any("accepted" in (item.content or "").lower() for item in messages)
    assert _round_count(channel.id) == before
    assert not _queued_channel_messages(jimothy.id)


def test_accept_with_reply_uses_reply_as_the_origin_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": creation.task.title,
            "reply": "On it — pulling the review notes now.",
        },
        jimothy,
        state,
        {
            "type": "task_assigned",
            "task_id": creation.task.id,
            "content": "Share the review findings.",
            "from_name": "Human Operator",
        },
    )

    assert result["event"] == "decision_applied"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert result.get("channel_message")
    assert result["channel_message"]["author_type"] == "agent"
    assert result["channel_message"]["content"] == "On it — pulling the review notes now."
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("On it — pulling the review notes now.") == 1
    assert not any(item.startswith("Jimothy accepted") for item in contents)


def test_chat_origin_accept_without_reply_posts_focus_line() -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    creation = _chat_task(assignee_id=ada.id)
    assert creation.task is not None
    state = db.get_agent_state(ada.id)
    assert state is not None

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": creation.task.title,
        },
        ada,
        state,
        {
            "type": "task_assigned",
            "task_id": creation.task.id,
            "content": "Please write the weekly report.",
            "from_name": "Human Operator",
        },
    )

    assert result["event"] == "decision_applied"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert result.get("chat_message")
    assert "accepted" in result["chat_message"]["content"].lower()
    notes = db.list_notifications(agent_id=ada.id)
    assert any("accepted" in note.content.lower() for note in notes)


@pytest.mark.asyncio
async def test_waiting_and_complete_claim_project_origin_cards() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    waiting = await execute_action(
        {
            "action": "waiting",
            "reason": "Need the source transcript.",
            "followUpMessage": "Need the source transcript before I can write.",
        },
        jimothy,
        state,
    )
    assert waiting["event"] == "status_changed"
    waiting_notes = project_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "waiting"},
        result=waiting,
    )
    assert waiting_notes
    assert waiting_notes[0].kind == "task_update"
    assert "waiting" in waiting_notes[0].content.lower()
    assert waiting_notes[0].channel_id == channel.id

    activity_runtime.activate_work_activity(jimothy.id, db.get_task(creation.task.id))
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Shared the review.",
            "followUpMessage": "Review summary is on the desk.",
            "doneClaim": {"type": "proof", "ev": "summary posted to the shared channel"},
        },
        jimothy,
        state,
    )
    assert completed["event"] == "status_changed"
    assert completed.get("channel_message")
    complete_notes = project_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "complete"},
        result=completed,
    )
    assert complete_notes
    assert complete_notes[0].kind == "completion"
    assert complete_notes[0].channel_id == channel.id
    assert "claim" in complete_notes[0].content.lower()


@pytest.mark.asyncio
async def test_waiting_system_card_does_not_open_peer_round() -> None:
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Jim, Jimothy",
        member_agent_ids=[jim.id, jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    before = _round_count(channel.id)

    waiting = await execute_action(
        {
            "action": "waiting",
            "reason": "Need the source transcript.",
            "followUpMessage": "Need the source transcript before I can write.",
        },
        jimothy,
        state,
    )
    await emit_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "waiting"},
        result=waiting,
    )
    system_lines = [
        item
        for item in db.list_channel_messages(channel.id)
        if item.author_type == "system" and "waiting" in (item.content or "").lower()
    ]
    assert system_lines
    # Agent follow-up opens a peer round; the system status card must not add another.
    assert _round_count(channel.id) == before + 1
    queued = begin_channel_response(
        jim,
        {
            "round_id": next(
                item["payload"]["round_id"]
                for item in waiting.get("trigger_requests") or []
                if item.get("trigger_type") == "channel_message"
            ),
            "channel_id": channel.id,
            "content": waiting["channel_message"]["content"],
        },
    )
    assert queued[1] is True


@pytest.mark.asyncio
async def test_retry_exhaustion_stall_posts_to_origin_channel() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    before = _round_count(channel.id)

    await TurnDispatcher()._notify_human_of_stuck_turn(
        agent=jimothy,
        failure_detail="CLI timed out",
        task=creation.task,
    )
    messages = db.list_channel_messages(channel.id)
    assert any("stalled" in (item.content or "").lower() for item in messages)
    assert _round_count(channel.id) == before
    assert db.get_human_chat_thread(jimothy.id) == []


@pytest.mark.asyncio
async def test_watchdog_stall_posts_to_origin_channel() -> None:
    from datetime import datetime, timedelta, timezone

    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    transition_task(creation.task.id, "accepted", reason="setup", actor="pytest")
    transition_task(creation.task.id, "active", reason="setup", actor="pytest")
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(
        creation.task.id,
        last_progress_at=past,
        last_heartbeat_at=past,
        last_activity=past,
        watchdog_pinged_at=past,
    )
    before = _round_count(channel.id)

    await TaskWatchdog()._check_tasks()

    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "stalled"
    messages = db.list_channel_messages(channel.id)
    assert any("stalled" in (item.content or "").lower() for item in messages)
    assert _round_count(channel.id) == before


@pytest.mark.asyncio
async def test_writing_path_change_posts_once_section_updates_do_not() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    before = _round_count(channel.id)
    reporter = _build_managed_writer_progress_reporter(jimothy, task_id=creation.task.id)

    await reporter(
        ManagedWriteProgress(stage="file_started", detail="Writing /me/review.md", path="/me/review.md")
    )
    await reporter(
        ManagedWriteProgress(
            stage="section",
            detail="Writing section 1/3 of /me/review.md: Intro",
            path="/me/review.md",
            section_index=1,
            section_count=3,
        )
    )
    await reporter(
        ManagedWriteProgress(
            stage="section",
            detail="Writing section 2/3 of /me/review.md: Findings",
            path="/me/review.md",
            section_index=2,
            section_count=3,
        )
    )
    await reporter(
        ManagedWriteProgress(stage="file_started", detail="Writing /me/notes.md", path="/me/notes.md")
    )

    writing = [
        item.content
        for item in db.list_channel_messages(channel.id)
        if (item.content or "").startswith("Writing ")
    ]
    assert writing == ["Writing /me/review.md", "Writing /me/notes.md"]
    assert _round_count(channel.id) == before
    events = db.list_task_events(creation.task.id)
    assert [event.content for event in events if event.content.startswith("Writing ")] == writing


def test_identical_origin_line_is_not_posted_twice() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    line = 'Jimothy accepted "Share review findings".'
    first = persist_origin_status_line(task=creation.task, agent=jimothy, content=line, kind="accepted")
    second = persist_origin_status_line(task=creation.task, agent=jimothy, content=line, kind="accepted")
    assert first.get("channel_message")
    assert second == {}
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count(line) == 1


def test_private_task_does_not_post_to_a_channel() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Unused",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = create_or_bind_task(
        title="Write a private note",
        description="Desk-only wrap-up.",
        project=None,
        assigned_to=jimothy.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="chat",
        notification_policy="completion_blocked",
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": creation.task.title,
        },
        jimothy,
        state,
        {
            "type": "task_assigned",
            "task_id": creation.task.id,
            "content": "Write the private note.",
            "from_name": "Human Operator",
        },
    )
    assert result["event"] == "decision_applied"
    assert "channel_message" not in result
    assert db.list_channel_messages(channel.id) == []


def test_task_events_api_is_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    creation = _chat_task(assignee_id=ada.id)
    assert creation.task is not None
    transition_task(creation.task.id, "accepted", reason="accepted the assignment", actor="Ada")
    transition_task(creation.task.id, "active", reason="started work", actor="Ada")
    stored = db.list_task_events(creation.task.id)
    assert stored[0].created_at <= stored[-1].created_at
    client = _task_api_client(monkeypatch)
    response = client.get(f"/api/tasks/{creation.task.id}/events", headers=_headers())
    assert response.status_code == 200
    events = response.json()
    assert len(events) >= 2
    created = [item["created_at"] for item in events]
    assert created == sorted(created, reverse=True)
    assert events[0]["id"] == stored[-1].id
