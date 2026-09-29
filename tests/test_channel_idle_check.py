"""Thread idle check: a quiet thread privately re-wakes a member who owes work.

Deterministic gates run before the System AI judge. The judge must quote
the transcript verbatim. The wake is a private channel round that posts
nothing, does not touch pass streaks, and keeps the full channel machinery.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime, channel_idle_check
from core.agent_loop.channel_host import pause_thread
from core.agent_loop.channel_idle_check import (
    IdleWake,
    build_idle_check_messages,
    check_channel,
    parse_idle_check_payload,
    scan_idle_channels,
)
from core.agent_loop.channel_router import RouterLine
from core.agent_loop.channel_rounds import (
    advance_channel_round,
    observe_channel_message,
)
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.turn_context import stamp_channel_latest_line
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task
from core.time import ensure_utc
from db import channel_host as host_db
from db import channel_idle_checks as idle_db
from db import channel_response_rounds as channel_round_db
from tests._router_fakes import route_reply

_GO_AHEAD = "Confirmed. Charles, you're clear to run the capture."
_PROMISE = "I'll run that capture first. Harley, can you confirm the plan?"


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


def _team():
    harley = db.create_agent("Harley", role="Feature Planner", desk_x=1, desk_y=1, model_work="identity-big")
    charles = db.create_agent("Charles", role="Engineer", desk_x=2, desk_y=1, model_work="identity-big")
    brian = db.create_agent("Brian", role="Spec Author", desk_x=3, desk_y=1, model_work="identity-big")
    channel = db.create_channel(
        name="M2",
        member_agent_ids=[harley.id, charles.id, brian.id],
        created_by=harley.id,
    )
    return harley, charles, brian, channel


def _enable_system_ai() -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()


def _human(channel_id: str, content: str):
    return db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
    )


def _agent_line(channel_id: str, agent, content: str):
    return db.create_channel_message(
        channel_id=channel_id,
        author_type="agent",
        author_name=agent.name,
        author_agent_id=agent.id,
        content=content,
        source_channel="channel",
    )


def _charles_case(channel, harley, charles):
    """Operator asks, Charles promises and asks for a go-ahead, Harley gives it."""
    _human(channel.id, "Charles, can you run the smoke capture on M2?")
    _agent_line(channel.id, charles, _PROMISE)
    return _agent_line(channel.id, harley, _GO_AHEAD)


def _later() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=600)


def _candidate_number(messages: list[dict[str, str]], name: str) -> int:
    user = next(item["content"] for item in messages if item["role"] == "user")
    block = user.split("Idle candidates:\n", 1)[1].split("\n\n", 1)[0]
    for line in block.splitlines():
        number, member = [part.strip() for part in line.split(" | ")[:2]]
        if member == name:
            return int(number)
    raise AssertionError(f"{name} is not an idle candidate: {block}")


def _judge(monkeypatch: pytest.MonkeyPatch, reply) -> dict[str, Any]:
    """Script the idle-check judge. ``reply`` maps the prompt to raw text."""
    calls: dict[str, Any] = {"n": 0, "prompts": []}

    def _complete(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        calls["prompts"].append("\n".join(item["content"] for item in messages))
        return reply(messages)

    monkeypatch.setattr("core.agent_loop.channel_idle_check.complete_text", _complete)
    return calls


def _no_judge(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    def _boom(messages: list[dict[str, str]]) -> str:
        raise AssertionError("idle-check judge must not be called")

    return _judge(monkeypatch, _boom)


def _wake_charles(messages: list[dict[str, str]]) -> str:
    return json.dumps(
        {"wake": [{"member": _candidate_number(messages, "Charles"), "quote": "I'll run that capture first."}]}
    )


def _channel_task(*, assignee_id: str, channel_id: str, title: str):
    creation = create_or_bind_task(
        title=title,
        description="Owed work.",
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
    assert creation.task is not None
    return creation.task


# ── Pure parse and prompt ────────────────────────────────────────────────


_TRANSCRIPT = "Human Operator: run it\nCharles: I'll run that   capture first.\nHarley: Go ahead."


@pytest.mark.parametrize(
    "raw",
    [
        '{"wake": [], "why": "x"}',
        '{"wake": [{"member": 3, "quote": "Go ahead."}]}',
        '{"wake": [{"member": 1, "quote": "Go ahead."}, {"member": 1, "quote": "run it"}]}',
        '{"wake": [{"member": true, "quote": "Go ahead."}]}',
        '{"wake": [{"member": 1, "quote": "  "}]}',
        '{"wake": [{"member": 1, "quote": "I will run the capture"}]}',
        '{"wake": [{"member": 1, "quote": "Go ahead.", "note": "x"}]}',
        '{"wake": {"member": 1}}',
        "not json",
    ],
)
def test_parse_rejects_bad_payloads(raw: str) -> None:
    assert parse_idle_check_payload(raw, {1: "charles", 2: "harley"}, _TRANSCRIPT) is None


def test_parse_accepts_a_valid_payload_in_order() -> None:
    raw = (
        "```json\n"
        '{"wake": [{"member": 2, "quote": "Go ahead."}, {"member": 1, "quote": "I\'ll run that capture first."}]}'
        "\n```"
    )
    assert parse_idle_check_payload(raw, {1: "charles", 2: "harley"}, _TRANSCRIPT) == [
        IdleWake(agent_id="harley", quote="Go ahead."),
        IdleWake(agent_id="charles", quote="I'll run that capture first."),
    ]
    assert parse_idle_check_payload('{"wake": []}', {1: "charles"}, _TRANSCRIPT) == []


def test_prompt_numbers_candidates_and_shows_no_ids() -> None:
    messages, number_map = build_idle_check_messages(
        transcript=[
            RouterLine(author="Charles", text=_PROMISE, status=False, author_agent_id="agent-charles"),
            RouterLine(author="BossMod", text="Brian Accepted: M2.2 TDD", status=True, author_agent_id=""),
        ],
        candidates=[{"id": "agent-charles", "name": "Charles", "role": "Engineer"}],
        busy=[("Brian", "M2.2 TDD")],
        max_wakes=2,
    )
    assert number_map == {1: "agent-charles"}
    blob = "\n".join(item["content"] for item in messages)
    assert "agent-charles" not in blob
    assert "Recent thread (oldest first):" in blob
    assert "Idle candidates:\n1 | Charles | Engineer" in blob
    assert 'Busy members:\nBrian — working on "M2.2 TDD"' in blob
    assert "[status] Brian Accepted: M2.2 TDD" in blob
    assert "At most 2 entries." in blob


# ── Gates: the judge is never called ─────────────────────────────────────


def test_delay_not_elapsed_does_not_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    latest = _charles_case(channel, harley, charles)
    soon = ensure_utc(latest.created_at) + timedelta(seconds=10)
    assert check_channel(channel.id, now=soon) == []
    assert calls["n"] == 0
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == ""


def test_dormant_thread_is_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    latest = _charles_case(channel, harley, charles)
    dormant = ensure_utc(latest.created_at) + timedelta(minutes=31)
    assert check_channel(channel.id, now=dormant) == []
    assert calls["n"] == 0
    row = db.query_one("SELECT channel_id FROM channel_idle_checks WHERE channel_id = $1", [channel.id])
    assert row is None


def test_active_round_does_not_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    latest = _charles_case(channel, harley, charles)
    db.create_channel_response_round(channel_id=channel.id, source_message_id=latest.id)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def test_paused_thread_does_not_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    _charles_case(channel, harley, charles)
    pause_thread(channel.id)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def test_same_checked_message_does_not_judge_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    latest = _charles_case(channel, harley, charles)
    idle_db.save_channel_idle_check({"channel_id": channel.id, "checked_message_id": latest.id})
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def _task_in(status: str, *, assignee_id: str, channel_id: str, title: str):
    """A thread task moved from pending straight to ``status``."""
    task = _channel_task(assignee_id=assignee_id, channel_id=channel_id, title=title)
    return transition_task(task.id, status, reason=f"Test: {status}.", actor="BossMod")


@pytest.mark.parametrize("status", ["accepted", "active"])
def test_accepted_or_active_task_excludes_a_candidate(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    _task_in(status, assignee_id=charles.id, channel_id=channel.id, title="Smoke capture")
    _human(channel.id, "Charles, can you run the smoke capture on M2?")
    latest = _agent_line(channel.id, charles, _PROMISE)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0
    # The quiet period is still recorded as judged.
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == latest.id


def test_waiting_stalled_or_delegated_tasks_do_not_exclude_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    for status in ("waiting", "stalled", "delegated"):
        _task_in(status, assignee_id=charles.id, channel_id=channel.id, title=f"Old {status} card")
    assert activity_runtime.get_active_work_activity(charles.id) is None
    _human(channel.id, "i see no PR?")
    _agent_line(channel.id, charles, "Opening it now.")
    seen: dict[str, int] = {}

    def _reply(messages: list[dict[str, str]]) -> str:
        seen["charles"] = _candidate_number(messages, "Charles")
        return '{"wake": []}'

    calls = _judge(monkeypatch, _reply)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 1
    assert seen["charles"] == 1


def test_live_work_activity_excludes_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    task = _channel_task(assignee_id=charles.id, channel_id=channel.id, title="Smoke capture")
    assert activity_runtime.activate_work_activity(charles.id, task) is not None
    # Take the task off accepted/active so only the live activity says "working".
    transition_task(task.id, "waiting", reason="Test: waiting.", actor="BossMod")
    assert db.get_task(task.id).status == "waiting"
    assert activity_runtime.get_active_work_activity(charles.id) is not None
    _human(channel.id, "Charles, can you run the smoke capture on M2?")
    _agent_line(channel.id, charles, _PROMISE)
    calls = _no_judge(monkeypatch)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def test_candidate_with_an_open_trigger_is_not_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    calls = _no_judge(monkeypatch)
    _human(channel.id, "Charles, can you run the smoke capture on M2?")
    _agent_line(channel.id, charles, _PROMISE)
    db.create_agent_trigger(
        agent_id=charles.id,
        trigger_type="channel_message",
        source_channel="channel",
        # Not aimed at this thread: the member gate, not the in-flight gate, is under test.
        payload={},
    )
    assert db.has_open_trigger(charles.id)
    assert not db.channel_has_open_trigger(channel.id)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def test_member_who_did_not_speak_in_the_window_is_not_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    db.set_setting("channel_router_transcript_messages", "2", "llm")
    config.reload()
    calls = _no_judge(monkeypatch)
    _agent_line(channel.id, charles, _PROMISE)
    for index in range(3):
        _human(channel.id, f"operator line {index}")
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0


def test_already_woken_member_is_skipped_until_a_new_human_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel = _team()
    _enable_system_ai()
    human = _human(channel.id, "Charles, can you run the smoke capture on M2?")
    _agent_line(channel.id, charles, _PROMISE)
    idle_db.save_channel_idle_check(
        {"channel_id": channel.id, "human_message_id": human.id, "woken_agent_ids": [charles.id]}
    )
    calls = _no_judge(monkeypatch)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0
    newer = _human(channel.id, "Any update, Charles?")
    calls = _judge(monkeypatch, lambda _messages: '{"wake": []}')
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 1
    state = idle_db.get_channel_idle_check(channel.id)
    assert state["woken_agent_ids"] == []
    assert state["human_message_id"] == newer.id
    assert state["checked_message_id"] == newer.id


# ── Nothing in flight, retries, and re-check before waking ──────────────


def _idle_row(channel_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM channel_idle_checks WHERE channel_id = $1", [channel_id])


def test_open_trigger_for_the_thread_blocks_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, brian, channel = _team()
    _enable_system_ai()
    _charles_case(channel, harley, charles)
    # Brian never spoke; his queued wake for this thread is still in flight.
    db.create_agent_trigger(
        agent_id=brian.id,
        trigger_type="channel_message",
        source_channel="channel",
        payload={"channel_id": channel.id},
    )
    calls = _no_judge(monkeypatch)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0
    assert _idle_row(channel.id) is None


def test_live_work_on_a_thread_task_blocks_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, brian, channel = _team()
    _enable_system_ai()
    task = _channel_task(assignee_id=brian.id, channel_id=channel.id, title="M2.2 TDD")
    assert activity_runtime.activate_work_activity(brian.id, task) is not None
    _charles_case(channel, harley, charles)
    calls = _no_judge(monkeypatch)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0
    assert _idle_row(channel.id) is None


def test_live_work_on_another_threads_task_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, brian, channel = _team()
    _enable_system_ai()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[brian.id], created_by=brian.id)
    task = _channel_task(assignee_id=brian.id, channel_id=other.id, title="Other thread work")
    assert activity_runtime.activate_work_activity(brian.id, task) is not None
    latest = _charles_case(channel, harley, charles)
    calls = _judge(monkeypatch, lambda _messages: '{"wake": []}')
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 1
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == latest.id


def test_full_budget_skips_without_counting(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    _charles_case(channel, harley, charles)
    limit = channel_idle_check.max_concurrent_model_calls()
    monkeypatch.setattr(channel_idle_check.budget, "inflight", lambda: limit)
    calls = _no_judge(monkeypatch)
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 0
    state = idle_db.get_channel_idle_check(channel.id)
    assert state["failed_attempts"] == 0
    assert state["checked_message_id"] == ""


def test_no_completion_retries_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    latest = _charles_case(channel, harley, charles)
    calls = _judge(monkeypatch, lambda _messages: None)
    with caplog.at_level(logging.INFO, logger="core.agent_loop.channel_idle_check"):
        for attempt in (1, 2):
            assert check_channel(channel.id, now=_later()) == []
            state = idle_db.get_channel_idle_check(channel.id)
            assert state["checked_message_id"] == ""
            assert state["failed_message_id"] == latest.id
            assert state["failed_attempts"] == attempt
        assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 3
    state = idle_db.get_channel_idle_check(channel.id)
    assert state["checked_message_id"] == latest.id
    assert state["failed_attempts"] == 0
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "channel idle check gave up after 3 attempts" in warnings[0].getMessage()
    # Recorded as checked: a fourth scan does not call the judge.
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 3


def test_new_message_during_judge_drops_the_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    latest = _charles_case(channel, harley, charles)

    def _reply(messages: list[dict[str, str]]) -> str:
        _human(channel.id, "Never mind, I ran it myself.")
        return _wake_charles(messages)

    calls = _judge(monkeypatch, _reply)
    rounds_before = len(db.list_channel_response_rounds(channel.id))
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 1
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] != latest.id
    assert len(db.list_channel_response_rounds(channel.id)) == rounds_before


def test_member_who_started_work_during_judge_is_not_woken(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[charles.id], created_by=charles.id)
    latest = _charles_case(channel, harley, charles)

    def _reply(messages: list[dict[str, str]]) -> str:
        # Charles accepts work elsewhere while the judge is thinking.
        _task_in("accepted", assignee_id=charles.id, channel_id=other.id, title="Smoke capture")
        return _wake_charles(messages)

    calls = _judge(monkeypatch, _reply)
    rounds_before = len(db.list_channel_response_rounds(channel.id))
    assert check_channel(channel.id, now=_later()) == []
    assert calls["n"] == 1
    assert len(db.list_channel_response_rounds(channel.id)) == rounds_before
    state = idle_db.get_channel_idle_check(channel.id)
    assert state["checked_message_id"] == latest.id
    assert state["woken_agent_ids"] == []


# ── End to end ───────────────────────────────────────────────────────────


def _wake_case(monkeypatch: pytest.MonkeyPatch):
    harley, charles, brian, channel = _team()
    _enable_system_ai()
    # The 09/28 incident: an old stalled card must not hide an idle member.
    _task_in("stalled", assignee_id=charles.id, channel_id=channel.id, title="M0.2: Diablo POC scaffold")
    latest = _charles_case(channel, harley, charles)
    calls = _judge(monkeypatch, _wake_charles)
    streaks_before = host_db.get_channel_host_state(channel.id)["pass_streaks"]
    rows_before = [row.id for row in db.list_channel_messages(channel.id)]
    triggers = check_channel(channel.id, now=_later())
    return harley, charles, brian, channel, latest, calls, triggers, streaks_before, rows_before


def test_charles_case_wakes_the_owed_member_privately(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel, latest, calls, triggers, streaks_before, rows_before = _wake_case(
        monkeypatch
    )
    assert calls["n"] == 1
    assert [item["agent_id"] for item in triggers] == [charles.id]
    assert triggers[0]["trigger_type"] == "channel_message"
    payload = triggers[0]["payload"]
    assert payload["idle_check"] is True
    assert payload["author_type"] == "system"
    assert payload["source_message_id"] == latest.id
    assert 'Charles, you said "I\'ll run that capture first."' in payload["content"]
    assert [row.id for row in db.list_channel_messages(channel.id)] == rows_before
    meta = channel_round_db.get_channel_round_meta(payload["round_id"])
    assert meta["router_mode"] == "fallback"
    assert host_db.get_channel_host_state(channel.id)["pass_streaks"] == streaks_before
    assert idle_db.get_channel_idle_check(channel.id)["woken_agent_ids"] == [charles.id]
    for agent in (harley, charles):
        assert agent.id not in calls["prompts"][0]

    # Accept on the private wake binds the task to the thread and posts Accepted.
    trigger = dict(payload)
    trigger["type"] = "channel_message"
    state = db.get_agent_state(charles.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Run the M2 smoke capture",
            "reply": "Starting the smoke capture now.",
        },
        charles,
        state,
        trigger,
    )
    meta = channel_round_db.get_channel_round_meta(payload["round_id"])
    assert meta["work_bind_ids"] == [charles.id]
    task = db.get_task(meta["work_binds"][0]["task_id"])
    assert task.status == "accepted"
    assert task.source_channel == "channel"
    assert task.notification_channel_id == channel.id
    lines = [row.content for row in db.list_channel_messages(channel.id)]
    assert any("Accepted: Run the M2 smoke capture" in line for line in lines), lines
    assert result["event"] != "agent_error"


def test_an_idle_check_pass_leaves_talk_state_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel, _latest, _calls, triggers, streaks_before, _rows = _wake_case(monkeypatch)
    payload = triggers[0]["payload"]
    rounds_before = len(db.list_channel_response_rounds(channel.id))
    trigger = dict(payload)
    trigger["type"] = "channel_message"
    result = observe_channel_message(charles, trigger)
    assert result["trigger_requests"] == []
    assert host_db.get_channel_host_state(channel.id)["pass_streaks"] == streaks_before
    assert len(db.list_channel_response_rounds(channel.id)) == rounds_before
    assert db.get_channel_response_round(payload["round_id"]).status == "completed"


def test_an_idle_check_speak_opens_normal_follow_up_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    _harley, charles, _brian, channel, _latest, _calls, triggers, _streaks, _rows = _wake_case(monkeypatch)
    payload = triggers[0]["payload"]
    prompts: list[str] = []

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        prompts.append("\n".join(item["content"] for item in messages))
        return route_reply(messages, [])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    db.mark_channel_candidate_responded(round_id=payload["round_id"], agent_id=charles.id)
    advance_channel_round(
        dict(payload),
        spoke=True,
        speaker_id=charles.id,
        spoken_text="Running the smoke capture now.",
    )
    assert prompts
    assert "Latest message from Charles (Engineer):\nRunning the smoke capture now." in prompts[0]


def test_stamp_keeps_the_note_as_the_current_message() -> None:
    harley, charles, _brian, channel = _team()
    opener = _human(channel.id, "Charles, can you run the smoke capture on M2?")
    _agent_line(channel.id, harley, _GO_AHEAD)
    base = {
        "type": "channel_message",
        "channel_id": channel.id,
        "source_message_id": opener.id,
        "content": "Thread check: ...",
    }
    control = dict(base)
    stamp_channel_latest_line(charles.id, control)
    assert control["latest_content"] == _GO_AHEAD
    idle = dict(base, idle_check=True, latest_content="stale")
    stamp_channel_latest_line(charles.id, idle)
    assert "latest_content" not in idle
    assert idle["content"] == "Thread check: ..."


# ── Settings and scan ────────────────────────────────────────────────────


def test_unset_system_ai_warns_once_and_records_the_check(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harley, charles, _brian, channel = _team()
    latest = _charles_case(channel, harley, charles)
    calls = _no_judge(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.channel_idle_check"):
        assert scan_idle_channels(_later()) == []
        assert scan_idle_channels(_later()) == []
    warnings = [
        record for record in caplog.records if "channel idle check skipped: System AI is not configured" in record.message
    ]
    assert len(warnings) == 1
    assert calls["n"] == 0
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == latest.id


def test_disabled_scan_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    harley, charles, _brian, channel = _team()
    _enable_system_ai()
    _charles_case(channel, harley, charles)
    db.set_setting("channel_idle_check_enabled", "false", "llm")
    config.reload()

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("disabled idle check must not read threads")

    monkeypatch.setattr(channel_idle_check.idle_db, "get_channel_idle_check", _boom)
    monkeypatch.setattr(channel_idle_check.db, "list_channels", _boom)
    assert scan_idle_channels(_later()) == []


def test_idle_check_settings_are_seeded() -> None:
    assert config.get("channel_idle_check_enabled") == "true"
    assert config.get("channel_idle_check_delay_seconds") == "45"
    assert config.get("channel_idle_check_interval_seconds") == "5"
    assert config.get("channel_idle_check_max_wakes") == "2"
    assert config.get("channel_idle_check_max_age_minutes") == "30"
    assert config.get("channel_idle_check_max_attempts") == "3"
    assert config.get("system_ai_timeout_seconds") == "180"
