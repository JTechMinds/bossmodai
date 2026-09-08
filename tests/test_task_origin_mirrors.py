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
from core.agent_loop.task_origin_mirrors import (
    format_done_claim_label,
    format_origin_status_line,
    persist_origin_status_line,
)
from core.agent_loop.turn_helpers import _build_managed_writer_progress_reporter
from core.agent_loop.watchdog import TaskWatchdog
from core.bm_cli.managed_writer import ManagedWriteProgress
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking.service import create_or_bind_subtask, create_or_bind_task
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


def test_channel_task_create_posts_created_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.outcome == "create_new_task"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Created: Share review findings") == 1
    assert _round_count(channel.id) == 0
    assert not _queued_channel_messages(jimothy.id)


def test_operator_thread_assign_payload_posts_created_on_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conversation assign sheet's POST body must bind Created to the thread.

    Without source_channel=channel + notification_channel_id, the API defaults
    to source_channel=api and the line lands in Focus.
    """
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    client = _task_api_client(monkeypatch)
    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Share review findings",
            "assigned_to": jimothy.id,
            "source_channel": "channel",
            "notification_channel_id": channel.id,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["outcome"] == "create_new_task"
    assert body["task"]["source_channel"] == "channel"
    assert body["task"]["notification_channel_id"] == channel.id
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Created: Share review findings") == 1
    notes = db.list_notifications(agent_id=jimothy.id)
    assert not any(note.content == "Created: Share review findings" for note in notes)


def test_chat_task_create_posts_created_line() -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    creation = _chat_task(assignee_id=ada.id)
    assert creation.outcome == "create_new_task"
    notes = db.list_notifications(agent_id=ada.id)
    assert any(note.content == "Created: Write the weekly report" for note in notes)


def test_bind_existing_task_does_not_repost_created() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id],
        created_by=HUMAN_SENDER_ID,
    )
    first = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert first.task is not None
    bound = create_or_bind_task(
        title=first.task.title,
        description=first.task.description,
        project=None,
        assigned_to=jimothy.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
        bind_task_id=first.task.id,
    )
    assert bound.outcome == "bind_existing_task"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Created: Share review findings") == 1


def test_subtask_create_posts_created_line_on_origin_thread() -> None:
    jimothy = db.create_agent("Jimothy", role="Lead", desk_x=1, desk_y=1)
    bea = db.create_agent("Bea", role="Writer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id, bea.id],
        created_by=HUMAN_SENDER_ID,
    )
    parent = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert parent.task is not None
    child = create_or_bind_subtask(
        parent_task=parent.task,
        title="Draft the findings note",
        description="Write the child deliverable.",
        project=None,
        assigned_to=bea.id,
        requester_id=jimothy.id,
        owner_id=jimothy.id,
        created_by=jimothy.id,
        work_contract=None,
        source_channel=parent.task.source_channel,
        notification_policy=parent.task.notification_policy,
        notification_channel_id=parent.task.notification_channel_id,
        audit_author_name=jimothy.name,
        audit_author_type="agent",
        audit_author_agent_id=jimothy.id,
    )
    assert child.outcome == "create_new_task"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Created: Share review findings") == 1
    assert contents.count("Created: Draft the findings note") == 1
    assert _round_count(channel.id) == 0


def test_same_title_subtask_still_posts_created() -> None:
    jimothy = db.create_agent("Jimothy", role="Lead", desk_x=1, desk_y=1)
    bea = db.create_agent("Bea", role="Writer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[jimothy.id, bea.id],
        created_by=HUMAN_SENDER_ID,
    )
    parent = _channel_task(assignee_id=jimothy.id, channel_id=channel.id, title="Write the status note")
    assert parent.task is not None
    child = create_or_bind_subtask(
        parent_task=parent.task,
        title="Write the status note",
        description=parent.task.description,
        project=None,
        assigned_to=bea.id,
        requester_id=jimothy.id,
        owner_id=jimothy.id,
        created_by=jimothy.id,
        work_contract=None,
        source_channel=parent.task.source_channel,
        notification_policy=parent.task.notification_policy,
        notification_channel_id=parent.task.notification_channel_id,
        audit_author_name=jimothy.name,
        audit_author_type="agent",
        audit_author_agent_id=jimothy.id,
    )
    assert child.outcome == "create_new_task"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Created: Write the status note") == 2


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
    assert result["channel_message"]["content"] == f"Accepted: {creation.task.title}"
    messages = db.list_channel_messages(channel.id)
    assert any(item.content == f"Accepted: {creation.task.title}" for item in messages)
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
    assert "On it — pulling the review notes now." in contents
    assert f"Accepted: {creation.task.title}" in contents


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
    assert result["chat_message"]["content"] == f"Accepted: {creation.task.title}"
    notes = db.list_notifications(agent_id=ada.id)
    assert any(note.content == f"Accepted: {creation.task.title}" for note in notes)


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
    assert waiting_notes[0].content == "Waiting — Need the source transcript."
    assert waiting_notes[0].channel_id == channel.id
    assert "Waiting — Need the source transcript." in [
        item.content for item in db.list_channel_messages(channel.id)
    ]

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
    assert complete_notes[0].content == "Done — summary posted to the shared channel"
    assert "Done — summary posted to the shared channel" in [
        item.content for item in db.list_channel_messages(channel.id)
    ]


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
    assert any((item.content or "").startswith("Stalled —") for item in messages)
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
    assert any((item.content or "").startswith("Stalled —") for item in messages)
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
    line = "Accepted: Share review findings"
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


def test_locked_operator_copy() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    task = type("T", (), {"title": "Share review findings"})()
    assert format_origin_status_line(kind="created", agent=agent, task=task) == "Created: Share review findings"
    assert format_origin_status_line(kind="accepted", agent=agent, task=task) == "Accepted: Share review findings"
    assert format_origin_status_line(kind="progress", agent=agent, task=task, path="/me/review.md") == "Writing /me/review.md"
    assert format_origin_status_line(kind="waiting", agent=agent, task=task, reason="Need the transcript") == "Waiting — Need the transcript"
    assert format_origin_status_line(kind="stalled", agent=agent, task=task, reason="CLI timed out") == "Stalled — CLI timed out"
    assert format_origin_status_line(kind="declined", agent=agent, task=task, reason="Wrong specialty") == "Declined — Wrong specialty"
    assert format_origin_status_line(kind="rerouted", agent=agent, task=task, target_name="Bea") == "Rerouted to Bea"
    assert format_origin_status_line(
        kind="rerouted", agent=agent, task=task, target_name="Bea", reason="Needs a writer"
    ) == "Rerouted to Bea — Needs a writer"
    assert format_origin_status_line(
        kind="rerouted", agent=agent, task=task, target_name="Bea", reason="Delegated to Bea"
    ) == "Rerouted to Bea"
    assert format_origin_status_line(kind="cancelled", agent=agent, task=task, reason="Operator stopped it") == "Cancelled — Operator stopped it"
    assert format_origin_status_line(kind="blocked_claim", agent=agent, task=task) == "Blocked — checkable claim missing"
    assert format_origin_status_line(
        kind="completion", agent=agent, task=task, claim={"type": "artifact", "path": "/me/review.md"}
    ) == "Done — /me/review.md"
    assert format_done_claim_label(claim={"type": "tests", "evidence": "pytest -q"}) == "pytest -q"


def test_decline_posts_locked_origin_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "decline",
            "intentKind": "work_request",
            "reply": "Wrong specialty for this review.",
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
    assert db.get_task(creation.task.id).status == "declined"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Declined — Wrong specialty for this review." in contents


def test_cancel_posts_locked_origin_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": creation.task.title,
        },
        jimothy,
        state,
        {"type": "task_assigned", "task_id": creation.task.id, "content": "Share findings.", "from_name": "Human"},
    )
    result = apply_decision(
        {
            "decision": "cancel",
            "intentKind": "other",
            "reply": "Operator stopped the review.",
        },
        jimothy,
        db.get_agent_state(jimothy.id),
        {"type": "human_chat", "content": "Stop that.", "from_name": "Human Operator"},
    )
    assert result["event"] == "decision_applied"
    assert db.get_task(creation.task.id).status == "abandoned"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Cancelled — Operator stopped the review." in contents


@pytest.mark.asyncio
async def test_empty_done_posts_blocked_claim_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "complete",
            "summary": "I think this is done.",
            "followUpMessage": "Done without a claim.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "world_feedback"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Blocked — checkable claim missing" in contents
    events = db.list_task_events(creation.task.id)
    assert any(event.content == "Blocked — checkable claim missing" for event in events)


@pytest.mark.asyncio
async def test_delegate_projects_rerouted_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Lead", desk_x=1, desk_y=1)
    bea = db.create_agent("Bea", role="Writer", desk_x=2, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id, bea.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id, title="Write the status note")
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "delegated",
            "agentId": bea.id,
            "followUpMessage": "Needs a writer.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    notes = project_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "delegated"},
        result=result,
    )
    assert notes
    assert notes[0].content == "Rerouted to Bea — Needs a writer."
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Rerouted to Bea — Needs a writer.") == 1
    await emit_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "delegated"},
        result=result,
    )
    assert [item.content for item in db.list_channel_messages(channel.id)].count(
        "Rerouted to Bea — Needs a writer."
    ) == 1


@pytest.mark.asyncio
async def test_abandon_posts_cancelled_origin_line() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "abandoned",
            "reason": "Operator archived the review.",
            "followUpMessage": "Stopping this review.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    assert db.get_task(creation.task.id).status == "abandoned"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Cancelled — Operator archived the review.") == 1
    assert "Blocked — checkable claim missing" not in contents


@pytest.mark.asyncio
async def test_dependency_block_posts_waiting_not_claim_blocked() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "blocked",
            "reason": "Need legal sign-off.",
            "followUpMessage": "Parked until legal signs off.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    assert db.get_task(creation.task.id).status == "blocked"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Waiting — Need legal sign-off.") == 1
    assert "Blocked — checkable claim missing" not in contents
    notes = project_chat_notifications(
        agent=jimothy,
        trigger={"type": "activity_resumed", "source_channel": "channel", "channel_id": channel.id},
        active_activity=None,
        action={"action": "blocked"},
        result=result,
    )
    assert notes
    assert notes[0].content == "Waiting — Need legal sign-off."


@pytest.mark.asyncio
async def test_waiting_execute_persists_origin_line_without_emit() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
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
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Waiting — Need the source transcript.") == 1


def test_clarification_loop_block_posts_waiting_origin_line() -> None:
    from core.agent_loop.decision_replies import _block_task_for_clarification_loop

    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Review", member_agent_ids=[jimothy.id], created_by=HUMAN_SENDER_ID)
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    _block_task_for_clarification_loop(
        task=creation.task,
        latest_question="Which transcript should I use?",
        source_trigger_id=None,
        streak_len=3,
    )
    assert db.get_task(creation.task.id).status == "blocked"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    waiting = [item for item in contents if (item or "").startswith("Waiting —")]
    assert waiting
    assert "Blocked — checkable claim missing" not in contents


def test_handoff_without_reason_projects_rerouted() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    notes = project_chat_notifications(
        agent=agent,
        trigger={"type": "activity_resumed", "source_channel": "chat"},
        active_activity=None,
        action={"action": "delegated"},
        result={
            "chat_notification": {
                "kind": "handoff",
                "task_title": "Write the status note",
                "target_name": "Bea",
                "reason": "",
                "task_id": "task-1",
                "source_channel": "chat",
                "policy": "completion_blocked",
                "human_visible": True,
            }
        },
    )
    assert notes
    assert notes[0].content == "Rerouted to Bea"
