"""One CLI-result path for both turn types (Revision 7 of the schedules plan).

``map_cli_result`` is the one mapper; ``broadcast_cli_side_effects`` sends a
mapped result's declared activity and operator lines. A decision turn's CLI
step reaches the UI live; an execution turn sends each effect exactly once.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.cli_turn_result import CliSideEffectError, broadcast_cli_side_effects, map_cli_result
from core.bm_cli.results import approval_required_result
from core.bm_cli.types import BossModCliResult
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking.service import create_or_bind_task

NOTE = 'Ada scheduled "Ping the operator": Every day, every 5 minutes'


class _RecordingSink(NullRuntimeEventSink):
    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []
        self.chat: list[dict[str, Any]] = []
        self.channel: list[dict[str, Any]] = []

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append({"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        self.chat.append(kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        self.channel.append(kwargs)


@pytest.fixture()
def sink() -> _RecordingSink:
    recording = _RecordingSink()
    runtime_events.set_sink(recording)
    yield recording
    runtime_events.set_sink(NullRuntimeEventSink())


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _result(*, ok: bool = True, data: dict[str, Any] | None = None, **fields: Any) -> BossModCliResult:
    return BossModCliResult(**{
        "command": "schedules add", "ok": ok, "detail": "Ada scheduled schedule s1",
        "prompt_content": "BOSSMOD CLI RESULT", "kind": "schedules", "data": data, **fields,
    })


def _declared(agent_id: str) -> dict[str, Any]:
    return {
        "origin_chrome": {"agent_id": agent_id, "content": NOTE, "from_type": "system", "from_name": "Ada"},
        "activity": {"event": "schedule_changed", "detail": NOTE, "extra": {"agent_id": agent_id, "schedule_id": "s1"}},
    }


# ─── The mapper ───


def test_declared_effects_map_onto_the_turn_result() -> None:
    ada = db.create_agent("Ada", role="Operator")
    data = _declared(ada.id)
    result = map_cli_result(ada, _result(data=data), command="schedules add")
    assert result["origin_status_messages"] == [data["origin_chrome"]]
    # The announcement rides apart; the step's own outcome is untouched.
    assert result["cli_activity"] == {
        "event": "schedule_changed", "detail": NOTE, "extra": {"agent_id": ada.id, "schedule_id": "s1"},
    }
    assert (result["event"], result["detail"]) == ("bm_cli_result", "Ada scheduled schedule s1")
    assert result["suppress_activity_broadcast"] is True
    assert result["suppress_world_broadcast"] is True
    assert result["agent_name"] == "Ada"


def test_an_activity_without_extra_maps_extra_to_none() -> None:
    ada = db.create_agent("Ada", role="Operator")
    result = map_cli_result(ada, _result(data={"activity": {"event": "x_changed", "detail": "y"}}), command="x")
    assert result["cli_activity"] == {"event": "x_changed", "detail": "y", "extra": None}


def test_a_mutating_command_that_declares_an_activity_still_counts_as_progress() -> None:
    from core.agent_loop.liveness import classify_step, outcome_resets_no_progress

    ada = db.create_agent("Ada", role="Operator")
    cli = _result(data={"activity": {"event": "file_written", "detail": "Ada wrote notes.md"}}, kind="write")
    result = map_cli_result(ada, cli, command="write notes.md")
    action = {"action": "bm_cli", "command": "write notes.md"}
    assert result["event"] == "bm_cli_result"
    assert outcome_resets_no_progress(action, result) is True
    assert classify_step(action, result, frozenset()) == "progress"


def test_a_plain_result_declares_nothing() -> None:
    ada = db.create_agent("Ada", role="Operator")
    result = map_cli_result(ada, _result(data={"status_lines": ["Browsing x"], "extension_id": "bv"}), command="x")
    assert (result["event"], result["detail"]) == ("bm_cli_result", "Ada scheduled schedule s1")
    assert result["suppress_activity_broadcast"] is True
    assert "origin_status_messages" not in result and "cli_activity" not in result
    assert (result["cli_status_lines"], result["cli_extension_id"]) == (["Browsing x"], "bv")


def test_approval_mapping_is_unchanged() -> None:
    ada = db.create_agent("Ada", role="Operator")
    cli = approval_required_result("rm notes.txt", "rm needs approval", approval_request_id="req-1")
    result = map_cli_result(ada, cli, command="rm notes.txt")
    assert result["event"] == "cli_approval_required"
    assert result["detail"] == "Ada requests approval: rm notes.txt"
    assert (result["approval_required"], result["approval_request_id"]) == (True, "req-1")
    assert result["suppress_activity_broadcast"] is False


def test_consent_mapping_is_unchanged() -> None:
    ada = db.create_agent("Ada", role="Operator")
    card = {"id": "c1", "path": "/home/x"}
    cli = _result(ok=False, data={"host_path_consent": card, "consent_reused": True},
                  consent_required=True, consent_request_id="c1")
    result = map_cli_result(ada, cli, command="cat /home/x")
    assert (result["event"], result["detail"]) == ("host_path_consent_required", "Ada requests host-path access: /home/x")
    assert (result["consent_required"], result["consent_request_id"], result["consent_reused"]) == (True, "c1", True)
    assert result["host_path_consent"] == card
    assert result["suppress_activity_broadcast"] is False


def test_a_gate_deny_still_posts_its_blocked_line() -> None:
    ada = db.create_agent("Ada", role="Operator")
    cli = _result(ok=False, data={}, kind="host_deny")
    result = map_cli_result(ada, cli, command="write /etc/x")
    lines = [line["content"] for line in result["origin_status_messages"]]
    assert any("Blocked — host deny" in line for line in lines), lines


def test_the_managed_writer_detail_is_unchanged() -> None:
    ada = db.create_agent("Ada", role="Operator")
    data = {"managed_writer_used": True, "managed_calls": 2, "managed_strategy": "sectioned", "managed_sections": 3}
    result = map_cli_result(ada, _result(data=data), command="write a.md")
    assert result["detail"] == "Ada scheduled schedule s1 via managed writer (sectioned, 2 calls, 3 sections)"
    assert result["managed_writer"]["completed"] is True


@pytest.mark.parametrize(
    "activity",
    [
        "schedule_changed",
        {"event": "schedule_changed"},
        {"event": "", "detail": "x"},
        {"event": "schedule_changed", "detail": "x", "extra": ["agent"]},
        {"event": "schedule_changed", "detail": "x", "agent_id": "a1"},
    ],
    ids=["not-object", "no-detail", "empty-event", "extra-not-object", "unknown-key"],
)
def test_a_malformed_activity_raises(activity: Any) -> None:
    ada = db.create_agent("Ada", role="Operator")
    with pytest.raises(CliSideEffectError):
        map_cli_result(ada, _result(data={"activity": activity}), command="schedules add")


@pytest.mark.parametrize(
    "chrome",
    [None, {"agent_id": "a1"}, {"content": "a line"}],
    ids=["not-object", "no-content", "no-target"],
)
def test_a_malformed_origin_chrome_raises(chrome: Any) -> None:
    ada = db.create_agent("Ada", role="Operator")
    with pytest.raises(CliSideEffectError):
        map_cli_result(ada, _result(data={"origin_chrome": chrome}), command="schedules add")


def test_an_approval_result_keeps_its_outcome_beside_a_declared_activity() -> None:
    ada = db.create_agent("Ada", role="Operator")
    cli = approval_required_result("rm notes.txt", "rm needs approval", approval_request_id="req-1")
    data = {**(cli.data or {}), "activity": {"event": "x_changed", "detail": "y"}}
    result = map_cli_result(ada, replace(cli, data=data), command="rm notes.txt")
    assert (result["event"], result["suppress_activity_broadcast"]) == ("cli_approval_required", False)
    assert result["cli_activity"] == {"event": "x_changed", "detail": "y", "extra": None}


async def test_the_broadcaster_sends_the_outcome_then_the_declaration() -> None:
    ada = db.create_agent("Ada", role="Operator")
    cli = approval_required_result("rm notes.txt", "rm needs approval", approval_request_id="req-1")
    data = {**(cli.data or {}), "activity": {"event": "x_changed", "detail": "y"}}
    recording = _RecordingSink()
    await broadcast_cli_side_effects(
        recording, map_cli_result(ada, replace(cli, data=data), command="rm notes.txt"), agent=ada,
    )
    assert [(item["event"], item["detail"]) for item in recording.activities] == [
        ("cli_approval_required", "Ada requests approval: rm notes.txt"), ("x_changed", "y"),
    ]


def test_the_simulator_opt_out_posts_no_blocked_line() -> None:
    ada = db.create_agent("Ada", role="Operator")
    cli = _result(ok=False, data={}, kind="host_deny")
    result = map_cli_result(ada, cli, command="write /etc/x", surface_gate_block=False)
    assert "origin_status_messages" not in result
    assert db.list_notifications(agent_id=ada.id, limit=10) == []


# ─── The broadcaster ───


async def test_the_broadcaster_sends_the_activity_and_every_line_to_its_sink() -> None:
    ada = db.create_agent("Ada", role="Operator")
    result = map_cli_result(ada, _result(data=_declared(ada.id)), command="schedules add")
    # A gate deny's line is also the result's primary chat message: still sent.
    result["chat_message"] = result["origin_status_messages"][0]
    recording = _RecordingSink()
    await broadcast_cli_side_effects(recording, result, agent=ada)
    assert recording.activities == [{
        "event": "schedule_changed", "detail": NOTE, "agent_name": "Ada",
        "extra": {"agent_id": ada.id, "schedule_id": "s1"},
    }]
    assert [(item["agent_id"], item["content"]) for item in recording.chat] == [(ada.id, NOTE)]


async def test_a_plain_result_broadcasts_nothing() -> None:
    ada = db.create_agent("Ada", role="Operator")
    recording = _RecordingSink()
    await broadcast_cli_side_effects(recording, map_cli_result(ada, _result(data={}), command="pwd"), agent=ada)
    assert recording.activities == [] and recording.chat == [] and recording.channel == []


# ─── Both turn types, end to end with the real `schedules add` ───


def _add_body() -> str:
    return json.dumps({
        "title": "Ping the operator", "instructions": "Send a short ping.",
        "recurrence": {"frequency": "daily", "interval": 1, "start_date": "2026-10-01",
                       "every_minutes": 5, "window_start": "00:00", "window_end": "23:59"},
    })


def _script(monkeypatch, contents: list[str]) -> None:
    from core.llm.client import LLMResponse

    queue = list(contents)

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        return LLMResponse(content=queue.pop(0), model="test/mock", prompt_tokens=8,
                           completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)


def _cli_step() -> str:
    return json.dumps({"act": "cli", "data": {"cmd": "schedules add", "body": _add_body()}, "th": "set it up"})


def _schedule_changes(recording: _RecordingSink) -> list[dict[str, Any]]:
    return [item for item in recording.activities if item["event"] == "schedule_changed"]


async def test_a_decision_turn_schedules_add_reaches_the_ui_live(sink, monkeypatch) -> None:
    from core.agent_loop.loop import run_turn

    ada = db.create_agent("Ada", role="Operator", model_work="test/mock")
    _script(monkeypatch, [
        _cli_step(),
        '{"act":"reply","work_commit":false,"intent":"question","msg":"Scheduled.","th":"done"}',
    ])
    trigger = {
        "type": "human_chat", "content": "Ping me every 5 minutes", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat", "author_type": "human",
    }
    outcome = await run_turn(ada, db.get_agent_state(ada.id), trigger)

    assert outcome.trigger_status == "completed"
    schedule = db.list_schedules_for_agent(ada.id)[0]
    assert _schedule_changes(sink) == [{
        "event": "schedule_changed", "detail": NOTE, "agent_name": "Ada",
        "extra": {"agent_id": ada.id, "schedule_id": schedule.id},
    }]
    assert [item["content"] for item in sink.chat if item["content"] == NOTE] == [NOTE]


async def test_an_execution_turn_schedules_add_broadcasts_each_effect_once(sink, monkeypatch) -> None:
    from core.agent_loop import activity_runtime
    from core.agent_loop.loop import run_turn

    ada = db.create_agent("Ada", role="Operator", model_work="test/mock")
    task = create_or_bind_task(
        title="Set up the pings", description="Ping the operator.", project=None, assigned_to=ada.id,
        requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None,
        work_contract=None, source_channel="chat", notification_policy="completion_blocked",
        notification_channel_id=None, audit_author_name="Human Operator", audit_author_type="human",
    ).task
    activity_runtime.activate_work_activity(ada.id, task, task_status="active")
    _script(monkeypatch, [_cli_step(), '{"act":"idle","data":{},"th":"done"}'])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Resume.", "source_channel": "work"}
    await run_turn(ada, db.get_agent_state(ada.id), trigger)

    schedule = db.list_schedules_for_agent(ada.id)[0]
    assert _schedule_changes(sink) == [{
        "event": "schedule_changed", "detail": NOTE, "agent_name": "Ada",
        "extra": {"agent_id": ada.id, "schedule_id": schedule.id},
    }]
    assert [item["content"] for item in sink.chat if item["content"] == NOTE] == [NOTE]


async def test_a_decision_turn_approval_pause_broadcasts_its_activity_once(sink, monkeypatch) -> None:
    from core.agent_loop.loop import run_turn

    ada = db.create_agent("Ada", role="Operator", model_work="test/mock")
    monkeypatch.setattr(
        "core.agent_loop.decision_turn.execute_bm_cli",
        lambda agent_obj, state_obj, command, content=None, **kwargs: approval_required_result(
            command, "rm needs approval",
        ),
    )
    _script(monkeypatch, ['{"act":"cli","data":{"cmd":"rm notes.txt"},"th":"clean"}'])
    trigger = {
        "type": "human_chat", "content": "Delete notes.txt", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat", "author_type": "human",
    }
    outcome = await run_turn(ada, db.get_agent_state(ada.id), trigger)

    assert outcome.result.get("event") == "cli_approval_required"
    approvals = [item for item in sink.activities if item["event"] == "cli_approval_required"]
    assert approvals == [{
        "event": "cli_approval_required", "detail": "Ada requests approval: rm notes.txt",
        "agent_name": "Ada", "extra": None,
    }]
