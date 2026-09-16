"""CLI approval cards stamp channel_id and project into the origin thread.

Same family as host-path / Shell Executor consent: create, project, and resume
keep the originating channel so Approve/Reject posts in that thread, not only
Needs you / Focus.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.loop import run_turn
from core.agent_loop.notifications import emit_chat_notifications, project_chat_notifications
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.models.cli_policy import CLI_APPROVAL_KIND
from tests.test_consent_origin import (
    _ResumeServices,
    _agent_and_state,
    _channel_for,
    _channel_task,
    _focus_task,
    _resume_trigger,
    _script_completions,
)


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()
    # CLI approval cards are the Shell-Executor-already-on path (pip install,
    # git push). With shell off, those commands error as disabled builtins.
    _enable_shell()


def teardown_function() -> None:
    db.close_connection()

APPROVAL_CMD = "pip install pytest"


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def test_approval_create_stamps_channel_id() -> None:
    agent, state = _agent_and_state()
    channel = _channel_for(agent.id)
    paused = execute_bm_cli(agent, state, APPROVAL_CMD, channel_id=channel.id)
    assert paused.approval_required is True
    card = (paused.data or {}).get("cli_approval") or {}
    assert card.get("kind") == CLI_APPROVAL_KIND
    assert card.get("channel_id") == channel.id
    assert card.get("command") == APPROVAL_CMD
    stored = db.get_cli_approval_request(paused.approval_request_id)
    assert stored is not None
    assert stored.channel_id == channel.id


def test_git_push_approval_stamps_channel_id() -> None:
    agent, state = _agent_and_state()
    channel = _channel_for(agent.id)
    paused = execute_bm_cli(agent, state, "git push origin main", channel_id=channel.id)
    assert paused.approval_required is True
    stored = db.get_cli_approval_request(paused.approval_request_id)
    assert stored is not None
    assert stored.channel_id == channel.id
    assert (paused.data or {}).get("cli_approval", {}).get("channel_id") == channel.id


def test_focus_approval_create_has_no_channel_id() -> None:
    agent, state = _agent_and_state()
    paused = execute_bm_cli(agent, state, APPROVAL_CMD)
    assert paused.approval_required is True
    card = (paused.data or {}).get("cli_approval") or {}
    assert "channel_id" not in card
    stored = db.get_cli_approval_request(paused.approval_request_id)
    assert stored is not None
    assert stored.channel_id is None


@pytest.mark.asyncio
async def test_thread_origin_cli_posts_approval_to_channel_not_focus() -> None:
    gerry, state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"%s"},"th":"install"}' % APPROVAL_CMD
    )
    result = await execute_action(parsed, gerry, state, trigger)
    assert result["event"] == "cli_approval_required"
    stored = db.get_cli_approval_request(result["approval_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id
    assert result["cli_approval"]["channel_id"] == channel.id

    notes = project_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].kind == CLI_APPROVAL_KIND
    assert notes[0].channel_id == channel.id
    assert notes[0].approval_id == stored.id

    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    cards = [
        item
        for item in db.list_channel_messages(channel.id)
        if item.approval_id == stored.id
    ]
    assert len(cards) == 1
    assert cards[0].notification_kind == CLI_APPROVAL_KIND
    focus_notes = db.list_notifications(agent_id=gerry.id, chat_visible=True)
    assert not any(item.kind == CLI_APPROVAL_KIND for item in focus_notes)
    jim_notes = db.list_notifications(agent_id=jim.id, chat_visible=True)
    assert not any(item.kind == CLI_APPROVAL_KIND for item in jim_notes)


@pytest.mark.asyncio
async def test_focus_origin_approval_stays_in_focus() -> None:
    gerry, state = _agent_and_state()
    unused = _channel_for(gerry.id, name="Unused")
    task = _focus_task(assignee_id=gerry.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"%s"},"th":"install"}' % APPROVAL_CMD
    )
    result = await execute_action(parsed, gerry, state, trigger)
    assert result["event"] == "cli_approval_required"
    stored = db.get_cli_approval_request(result["approval_request_id"])
    assert stored is not None
    assert stored.channel_id is None
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert db.list_channel_messages(unused.id) == []
    assert any(
        item.kind == CLI_APPROVAL_KIND
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_execution_turn_cli_on_thread_origin_posts_approval_in_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gerry, state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    _script_completions(
        monkeypatch,
        ['{"act":"cli","data":{"cmd":"%s"},"th":"install"}' % APPROVAL_CMD],
    )
    trigger = _resume_trigger(task_id=task.id)
    outcome = await run_turn(gerry, state, trigger)
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("event") == "cli_approval_required"
    approval_id = outcome.result.get("approval_request_id")
    stored = db.get_cli_approval_request(approval_id)
    assert stored is not None
    assert stored.channel_id == channel.id
    assert trigger.get("channel_id") == channel.id
    cards = [
        item for item in db.list_channel_messages(channel.id) if item.approval_id == stored.id
    ]
    assert len(cards) == 1
    assert not any(
        item.kind == CLI_APPROVAL_KIND
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_approval_resume_stamps_channel_id() -> None:
    gerry, _state = _agent_and_state()
    channel = _channel_for(gerry.id)
    stored = db.create_cli_approval_request(
        agent_id=gerry.id,
        command=APPROVAL_CMD,
        channel_id=channel.id,
    )
    services = _ResumeServices()
    updated = await resume_cli_approval(
        stored.id,
        approved=True,
        services=services,
    )
    assert updated is not None
    resumes = [
        item
        for item in services.triggers
        if item.get("trigger_type") == "cli_approval_resolved"
    ]
    assert resumes
    assert resumes[0]["payload"]["channel_id"] == channel.id
    assert resumes[0]["source_channel"] == "channel"


@pytest.mark.asyncio
async def test_focus_approval_resume_omits_channel_id() -> None:
    gerry, _state = _agent_and_state()
    stored = db.create_cli_approval_request(agent_id=gerry.id, command=APPROVAL_CMD)
    services = _ResumeServices()
    updated = await resume_cli_approval(
        stored.id,
        approved=False,
        note="not now",
        services=services,
    )
    assert updated is not None
    assert "channel_id" not in services.triggers[0]["payload"]
    assert services.triggers[0]["source_channel"] == "system"


def test_needs_projects_approval_to_origin_thread() -> None:
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    paused = execute_bm_cli(agent=gerry, state=state, command=APPROVAL_CMD, channel_id=channel.id)
    client = _api_client()
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    approvals = [item for item in res.json() if item["kind"] == "approval"]
    assert len(approvals) == 1
    assert approvals[0]["id"] == paused.approval_request_id
    assert approvals[0]["conversation_id"] == channel.id


def test_needs_focus_approval_uses_agent_conversation() -> None:
    gerry, state = _agent_and_state()
    paused = execute_bm_cli(gerry, state, APPROVAL_CMD)
    client = _api_client()
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    approvals = [item for item in res.json() if item["kind"] == "approval"]
    assert len(approvals) == 1
    assert approvals[0]["id"] == paused.approval_request_id
    assert approvals[0]["conversation_id"] == gerry.id


@pytest.mark.asyncio
async def test_channel_transcript_serializes_approval_card() -> None:
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"%s"},"th":"install"}' % APPROVAL_CMD
    )
    result = await execute_action(parsed, gerry, state, trigger)
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    client = _api_client()
    res = client.get(f"/api/channels/{channel.id}", headers=_auth())
    assert res.status_code == 200, res.text
    cards = [
        item
        for item in res.json()["messages"]
        if item.get("notification_kind") == CLI_APPROVAL_KIND
    ]
    assert len(cards) == 1
    assert cards[0]["cli_approval"]["id"] == result["approval_request_id"]
    assert cards[0]["cli_approval"]["channel_id"] == channel.id
    assert cards[0]["cli_approval"]["command"] == APPROVAL_CMD
    assert cards[0]["cli_approval"]["kind"] == CLI_APPROVAL_KIND
