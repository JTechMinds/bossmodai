"""Cut G — thread-born CLI / consent / resume keep channel_id.

Cards for work stamped ``source_channel=channel`` + ``notification_channel_id``
must post in that thread, including execution and CLI resume turns that omit
``channel_id`` on the live trigger. Focus-origin work stays Focus-only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import (
    build_activity_resume_trigger,
    build_task_assigned_trigger,
    build_task_resume_trigger,
)
from core.agent_loop.loop import run_turn
from core.agent_loop.notifications import emit_chat_notifications, project_chat_notifications
from core.agent_loop.task_origins import (
    consent_origin_channel_id,
    stamp_trigger_origin_channel,
    task_origin_channel_id,
)
from core.bm_cli.host_path_consent import resume_host_path_consent
from core.bm_cli.types import BossModCliResult
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task


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


def _agent_and_state(*, name: str = "Gerry"):
    agent = db.create_agent(name, role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _channel_for(*agent_ids: str, name: str = "Shared"):
    return db.create_channel(
        name=name,
        member_agent_ids=list(agent_ids),
        created_by=HUMAN_SENDER_ID,
    )


def _channel_task(*, assignee_id: str, channel_id: str, title: str = "Read the host file"):
    return create_or_bind_task(
        title=title,
        description="Need a host path from the thread.",
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


def _focus_task(*, assignee_id: str, title: str = "Read a Focus file"):
    return create_or_bind_task(
        title=title,
        description="Need a host path from Focus.",
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


def _resume_trigger(*, task_id: str) -> dict[str, Any]:
    return {
        "type": "activity_resumed",
        "source_channel": "work",
        "task_id": task_id,
    }


def _host_file(tmp_path: Path, name: str = "note.txt") -> Path:
    host = tmp_path / "grant-root"
    host.mkdir()
    fixture = host / name
    fixture.write_text("thread\n", encoding="utf-8")
    return fixture


def _allow_host(host: Path) -> None:
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()


class _ResumeServices:
    def __init__(self) -> None:
        self.triggers: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.triggers.append(kwargs)


def _script_completions(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> None:
    queue = list(contents)

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        return LLMResponse(
            content=queue.pop(0),
            model="test/mock",
            prompt_tokens=8,
            completion_tokens=4,
            total_tokens=12,
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)


def test_task_origin_channel_id_matches_assign_stamp() -> None:
    gerry, _state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    channel_task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    focus_task = _focus_task(assignee_id=gerry.id).task
    assert task_origin_channel_id(channel_task) == channel.id
    assert task_origin_channel_id(focus_task) is None
    assert consent_origin_channel_id(_resume_trigger(task_id=channel_task.id)) == channel.id
    assert consent_origin_channel_id(_resume_trigger(task_id=focus_task.id)) is None
    assert consent_origin_channel_id(
        {"type": "human_chat", "source_channel": "chat", "task_id": channel_task.id}
    ) is None
    nested = {
        "type": "host_path_consent_resolved",
        "payload": {"channel_id": channel.id, "status": "always_allowed"},
    }
    assert consent_origin_channel_id(nested) == channel.id


def test_resume_builders_stamp_channel_id_for_thread_origin() -> None:
    gerry, _state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    channel_task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    focus_task = _focus_task(assignee_id=gerry.id).task
    activity = activate_work_activity(gerry.id, channel_task)

    assigned = build_task_assigned_trigger(channel_task)
    assert assigned["payload"]["channel_id"] == channel.id
    resumed = build_activity_resume_trigger(activity, reason="Continue the review.")
    assert resumed["payload"]["channel_id"] == channel.id
    task_resume = build_task_resume_trigger(channel_task, reason="Continue the review.")
    assert task_resume["payload"]["channel_id"] == channel.id

    focus_assigned = build_task_assigned_trigger(focus_task)
    assert "channel_id" not in focus_assigned["payload"]
    focus_resume = build_task_resume_trigger(focus_task, reason="Continue Focus work.")
    assert "channel_id" not in focus_resume["payload"]


def test_stamp_trigger_origin_channel_fills_resume_without_dropping_focus() -> None:
    gerry, _state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    channel_task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    trigger = _resume_trigger(task_id=channel_task.id)
    stamp_trigger_origin_channel(trigger)
    assert trigger["channel_id"] == channel.id

    focus = {"type": "human_chat", "source_channel": "chat", "task_id": channel_task.id}
    stamp_trigger_origin_channel(focus)
    assert "channel_id" not in focus


@pytest.mark.asyncio
async def test_thread_origin_execution_cli_posts_consent_to_channel_not_focus(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture
    )
    result = await execute_action(parsed, gerry, state, trigger)
    assert result["event"] == "host_path_consent_required"
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id
    assert result["host_path_consent"]["channel_id"] == channel.id
    assert any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))

    notes = project_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].kind == "host_path_consent"
    assert notes[0].channel_id == channel.id

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
        if item.consent_id == stored.id
    ]
    assert len(cards) == 1
    assert cards[0].notification_kind == "host_path_consent"
    focus_notes = db.list_notifications(agent_id=gerry.id, chat_visible=True)
    assert not any(item.kind == "host_path_consent" for item in focus_notes)
    jim_notes = db.list_notifications(agent_id=jim.id, chat_visible=True)
    assert not any(item.kind == "host_path_consent" for item in jim_notes)


@pytest.mark.asyncio
async def test_thread_origin_request_host_access_posts_to_channel(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Need the thread file"},"th":"ask"}'
        % fixture
    )
    result = await execute_action(parsed, gerry, state, trigger)
    assert result["event"] == "host_path_consent_required"
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id
    assert any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))
    assert not any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_focus_origin_execution_cli_still_posts_consent_to_focus(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    unused = _channel_for(gerry.id, name="Unused")
    task = _focus_task(assignee_id=gerry.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture
    )
    result = await execute_action(parsed, gerry, state, trigger)
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id is None
    assert db.has_consent_notification(stored.id)
    notes = project_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].channel_id is None
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert db.list_channel_messages(unused.id) == []
    focus_notes = db.list_notifications(agent_id=gerry.id, chat_visible=True)
    assert any(item.kind == "host_path_consent" for item in focus_notes)


@pytest.mark.asyncio
async def test_focus_chat_does_not_steal_active_channel_task_origin(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = {
        "type": "human_chat",
        "source_channel": "chat",
        "content": f"Please read {fixture}",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
    }
    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Need it in Focus"},"th":"ask"}'
        % fixture
    )
    result = await execute_action(parsed, gerry, state, trigger)
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id is None
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert not any(item.consent_id for item in db.list_channel_messages(channel.id))
    assert any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_thread_origin_workspace_preference_posts_to_channel(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    _allow_host(fixture.parent)
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"write %s","body":"changed"},"th":"edit"}' % fixture
    )
    result = await execute_action(parsed, gerry, state, trigger)
    assert result["event"] == "workspace_preference_required"
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id
    await emit_chat_notifications(
        agent=gerry,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))
    assert not any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_consent_resume_keeps_channel_id_on_cli_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    trigger = _resume_trigger(task_id=task.id)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture
    )
    first = await execute_action(parsed, gerry, state, trigger)
    stored = db.get_consent_request(first["consent_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id

    services = _ResumeServices()
    updated = await resume_host_path_consent(
        stored.id,
        decision="always_allow",
        services=services,
    )
    assert updated is not None
    resumes = [
        item
        for item in services.triggers
        if item.get("trigger_type") == "host_path_consent_resolved"
    ]
    assert resumes
    assert resumes[0]["payload"]["channel_id"] == channel.id
    assert resumes[0]["source_channel"] == "channel"

    captured: dict[str, Any] = {}

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        captured["channel_id"] = kwargs.get("channel_id")
        captured["trigger_type"] = kwargs.get("trigger_type")
        return BossModCliResult(
            command=command,
            ok=True,
            detail="listed",
            prompt_content="BOSSMOD CLI RESULT\nlisted",
        )

    monkeypatch.setattr("core.bm_cli.runtime.execute_bm_cli", _fake_cli)
    _script_completions(
        monkeypatch,
        ['{"act":"wait","data":{"why":"Host path granted; continuing."},"th":"resume complete"}'],
    )
    resume_trigger = {
        "type": "host_path_consent_resolved",
        "task_id": task.id,
        "payload": {
            "status": "always_allowed",
            "command": f"cat {fixture}",
            "consent_request_id": stored.id,
        },
    }
    outcome = await run_turn(gerry, state, resume_trigger)
    assert outcome.trigger_status == "completed"
    assert captured.get("channel_id") == channel.id
    assert resume_trigger.get("channel_id") == channel.id


@pytest.mark.asyncio
async def test_execution_turn_cli_on_thread_origin_posts_card_in_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    jim = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(gerry.id, jim.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    _script_completions(
        monkeypatch,
        ['{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture],
    )
    trigger = _resume_trigger(task_id=task.id)
    outcome = await run_turn(gerry, state, trigger)
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("event") == "host_path_consent_required"
    consent_id = outcome.result.get("consent_request_id")
    stored = db.get_consent_request(consent_id)
    assert stored is not None
    assert stored.channel_id == channel.id
    assert trigger.get("channel_id") == channel.id
    cards = [item for item in db.list_channel_messages(channel.id) if item.consent_id == stored.id]
    assert len(cards) == 1
    assert not any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )
    assert not any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=jim.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_execution_turn_cli_on_focus_origin_stays_in_focus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    unused = _channel_for(gerry.id, name="Unused")
    task = _focus_task(assignee_id=gerry.id).task
    activate_work_activity(gerry.id, task)
    _script_completions(
        monkeypatch,
        ['{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture],
    )
    trigger = _resume_trigger(task_id=task.id)
    outcome = await run_turn(gerry, state, trigger)
    assert outcome.result.get("event") == "host_path_consent_required"
    stored = db.get_consent_request(outcome.result.get("consent_request_id"))
    assert stored is not None
    assert stored.channel_id is None
    assert trigger.get("channel_id") is None
    assert db.list_channel_messages(unused.id) == []
    assert any(
        item.kind == "host_path_consent"
        for item in db.list_notifications(agent_id=gerry.id, chat_visible=True)
    )


@pytest.mark.asyncio
async def test_active_channel_task_stamps_cli_when_resume_omits_task_id(
    tmp_path: Path,
) -> None:
    fixture = _host_file(tmp_path)
    gerry, state = _agent_and_state()
    channel = _channel_for(gerry.id)
    task = _channel_task(assignee_id=gerry.id, channel_id=channel.id).task
    activate_work_activity(gerry.id, task)
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"cat %s"},"th":"read"}' % fixture
    )
    result = await execute_action(
        parsed,
        gerry,
        state,
        {"type": "activity_resumed", "source_channel": "work"},
    )
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    assert stored.channel_id == channel.id
