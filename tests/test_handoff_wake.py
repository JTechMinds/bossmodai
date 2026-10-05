"""Wake from intent, structured next_owners, and the next Board card.

System AI picks ambient Talk and Done/handoff rounds. Operator @ is a pin.
Empty speak on a Done/handoff route gets one repair, then stops. Prose
names are not a wake layer. When Done lands and the Board or structured
next_owners names a pending card, that assignee gets a Work bind. A status
round does not.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

import db
from tests._connections import model_connection
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.channel_router import build_router_messages
from core.agent_loop.channel_host import note_human_snapshot
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.channel_work_bind import live_work_bind_ids, record_round_work_bind
from core.agent_loop.decision_contract import parse_decision
from core.agent_loop.decision_runtime import apply_decision
from core.default_prompts import load_default_prompt
from core.models.message import HUMAN_SENDER_ID
from core.tasking.board import next_board_owner_id
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task
from db import channel_response_rounds as channel_round_db
from tests._router_fakes import route_reply


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


def _trio():
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1, connection_id=model_connection("identity-big"))
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1, connection_id=model_connection("identity-big"))
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=3, desk_y=1, connection_id=model_connection("identity-big"))
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[jim.id, laura.id, jimothy.id],
        created_by=jim.id,
    )
    return jim, laura, jimothy, channel


def _enable_system_ai() -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()


def _record_log_tool_evidence(agent_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="cat /projects/review.md",
        content_present=False,
        executor="virtual",
        cwd_before="/",
        cwd_after="/",
        policy_tier="read",
        decision="allowed",
        exit_code=0,
        result_kind="read",
        stdout_preview="ok",
        stderr_preview=None,
        changed_paths=None,
        trigger_type="activity_resumed",
    )


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


def _channel_wakes(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (result.get("trigger_requests") or [])
        if item.get("trigger_type") == "channel_message"
    ]


def _queue(round_id: str) -> list[str]:
    candidates = sorted(
        db.list_channel_response_candidates(round_id),
        key=lambda candidate: (candidate.queue_position or 9999, candidate.created_at),
    )
    return [candidate.agent_id for candidate in candidates]


def _work_wakes(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (result.get("trigger_requests") or [])
        if item.get("trigger_type") == "task_assigned"
    ]


def _script(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> dict[str, int]:
    calls = {"n": 0}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        blob = "\n".join(item.get("content") or "" for item in messages)
        calls["last"] = blob
        if calls["n"] > len(replies):
            raise AssertionError("router was called more times than scripted")
        return route_reply(messages, replies[calls["n"] - 1])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    return calls


def _hold(channel, agent, task) -> None:
    """Reproduce "accepted in the thread": this agent's work is bound on a round."""
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Please take it.",
        source_channel="channel",
    )
    round_row = db.create_channel_response_round(channel_id=channel.id, source_message_id=message.id)
    record_round_work_bind(round_row.id, agent_id=agent.id, task_id=task.id)
    channel_round_db.complete_channel_response_round(round_row.id)
    assert agent.id in live_work_bind_ids(channel.id)


async def _done(
    jimothy,
    channel,
    *,
    follow_up: str,
    next_owners: list[str] | None = None,
    hold: bool = False,
):
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    if hold:
        _hold(channel, jimothy, creation.task)
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    _record_log_tool_evidence(jimothy.id)
    action: dict[str, Any] = {
        "action": "complete",
        "summary": "Shared the review.",
        "followUpMessage": follow_up,
        "doneClaim": {"type": "proof", "ev": "summary posted to the shared channel"},
    }
    if next_owners is not None:
        action["nextOwners"] = next_owners
    completed = await execute_action(action, jimothy, state)
    assert completed["event"] == "status_changed"
    return creation.task, completed


def test_router_prompt_is_intent_first() -> None:
    messages, _numbers = build_router_messages(
        members=[{"id": "jim", "name": "Jim", "role": "PM"}],
        latest_message="Where are we?",
        latest_author="Human Operator",
        transcript=[],
        pending_mention_ids=["jim"],
        sticky="Board next: laura | Laura",
    )
    blob = "\n".join(item["content"] for item in messages)
    assert "from intent, not wording" in blob
    assert "Do not wake people merely because their name appears" not in blob
    assert (
        "A member is addressed when the line hands them work, asks them something, or names them as next"
        in blob
    )
    assert "A member who is only referred to ('per Brian's spec', 'Brian said') is not addressed." in blob
    assert (
        "An ask for one volunteer ('can someone…', 'anyone…') addresses the member whose role fits best, "
        "or two when it spans two roles. An ask to every member ('each of you', 'everyone', 'all of you', "
        "'@all', 'team, each…') addresses every member: name all of them. When the ask is to help or review "
        "a member, the helpers are addressed; wake the member being helped too only when the line also asks "
        "them to act."
    ) in blob
    assert "Recent thread" in blob
    assert "Latest message from" in blob
    assert "Board next: laura | Laura" in blob
    assert '"speak"' in blob and "stay_out" not in blob
    assert "at most" not in blob


def test_next_owners_parses_on_the_decision_envelope() -> None:
    decision = parse_decision(
        '{"act":"reply","work_commit":false,"intent":"status","msg":"Draft is saved.","next_owners":["laura"],"th":"hand off"}'
    )
    assert decision["nextOwners"] == ["laura"]
    action = parse_action(
        '{"say":"Draft is saved.","actions":[{"act":"done","data":{"sum":"Saved."}}],"next_owners":["laura"]}'
    )
    assert action["action"] == "complete"
    assert action["nextOwners"] == ["laura"]
    assert "@" not in (action.get("followUpMessage") or "")
    rejected = parse_action('{"act":"idle","next_owners":"laura","th":"no"}')
    assert rejected["action"] == "_parse_failed"


def test_operator_at_still_hard_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="@Laura where are we?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert triggers[0]["agent_id"] == laura.id
    round_id = triggers[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["pinned_ids"][0] == laura.id


def test_ambient_talk_does_not_wake_a_prose_name(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[jim.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Laura takes the next pass when she can.",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert calls["n"] == 1
    assert [item["agent_id"] for item in triggers] == [jim.id]
    round_id = triggers[0]["payload"]["round_id"]
    assert laura.id not in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]
    assert "Who speaks next?" not in calls["last"]


def test_agent_at_is_not_an_operator_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_name=jimothy.name,
        author_agent_id=jimothy.id,
        content="@Laura the draft is ready.",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name=jimothy.name,
        author_type="agent",
        exclude_agent_ids={jimothy.id},
        from_agent=jimothy.id,
    )
    assert triggers[0]["agent_id"] == jim.id
    round_id = triggers[0]["payload"]["round_id"]
    assert laura.id not in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]


@pytest.mark.asyncio
async def test_done_opens_a_system_ai_peer_round(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[laura.id]])
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    wakes = _channel_wakes(completed)
    assert calls["n"] == 1
    assert [item["agent_id"] for item in wakes] == [laura.id]
    round_id = wakes[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["router_mode"] == "system"
    assert "from intent, not wording" in calls["last"]


@pytest.mark.asyncio
async def test_done_after_channel_accept_opens_the_handoff_round(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[laura.id]])
    _task, completed = await _done(
        jimothy,
        channel,
        follow_up="Plan is in the doc. Next up: Laura writes the spec.",
        hold=True,
    )
    assert calls["n"] == 1
    wakes = _channel_wakes(completed)
    assert [item["agent_id"] for item in wakes] == [laura.id]
    agent_message_id = completed["channel_message"]["message_id"]
    assert completed["channel_message"]["author_type"] == "agent"
    round_row = db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=agent_message_id,
    )
    assert round_row is not None
    assert wakes[0]["payload"]["round_id"] == round_row.id
    assert jimothy.id not in live_work_bind_ids(channel.id)


@pytest.mark.asyncio
async def test_delegate_after_channel_accept_settles_the_bind_and_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id, title="Write the status note")
    assert creation.task is not None
    _hold(channel, jimothy, creation.task)
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "delegated",
            "agentId": laura.id,
            "followUpMessage": "Needs a writer.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    assert calls["n"] == 1
    wakes = _channel_wakes(result)
    assert wakes[0]["agent_id"] == laura.id
    agent_message_id = result["channel_message"]["message_id"]
    round_row = db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=agent_message_id,
    )
    assert round_row is not None
    assert wakes[0]["payload"]["round_id"] == round_row.id
    assert jimothy.id not in live_work_bind_ids(channel.id)


@pytest.mark.asyncio
async def test_waiting_after_channel_accept_settles_the_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, _laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[jim.id]])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    _hold(channel, jimothy, creation.task)
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "waiting",
            "reason": "Waiting on the fixture.",
            "followUpMessage": "Jim, can you share the fixture?",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    assert db.get_task(creation.task.id).status == "waiting"
    assert calls["n"] == 1
    agent_message_id = result["channel_message"]["message_id"]
    assert (
        db.get_channel_response_round_for_source(
            channel_id=channel.id,
            source_message_id=agent_message_id,
        )
        is not None
    )
    assert [item["agent_id"] for item in _channel_wakes(result)] == [jim.id]
    assert jimothy.id not in live_work_bind_ids(channel.id)


@pytest.mark.asyncio
async def test_empty_handoff_speak_repairs_once_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            [],
            [],
        ],
    )
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    assert calls["n"] == 2
    assert "Who speaks next?" in calls["last"]
    assert _channel_wakes(completed) == []
    rounds = db.list_channel_response_rounds(channel.id)
    assert len(rounds) == 1
    assert rounds[0].status == "completed"


@pytest.mark.asyncio
async def test_handoff_repair_can_name_the_next_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            [],
            [laura.id],
        ],
    )
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    assert calls["n"] == 2
    assert [item["agent_id"] for item in _channel_wakes(completed)] == [laura.id]


@pytest.mark.asyncio
async def test_next_owners_hard_wake_without_an_at(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    _task, completed = await _done(
        jimothy,
        channel,
        follow_up="Draft is saved.",
        next_owners=[laura.id],
    )
    assert "@" not in completed["channel_message"]["content"]
    assert calls["n"] == 1
    wakes = _channel_wakes(completed)
    assert wakes[0]["agent_id"] == laura.id
    round_id = wakes[0]["payload"]["round_id"]
    assert laura.id in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]


@pytest.mark.asyncio
async def test_board_next_owner_wakes_with_no_chat_syntax(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id, title="Write the draft")
    assert creation.task is not None
    nxt = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="Review the draft")
    assert nxt.task is not None
    assert next_board_owner_id(creation.task, author_id=jimothy.id) == laura.id
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    _record_log_tool_evidence(jimothy.id)
    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Draft is saved.",
            "followUpMessage": "Draft is saved.",
            "doneClaim": {"type": "proof", "ev": "summary posted to the shared channel"},
        },
        jimothy,
        state,
    )
    assert completed["event"] == "status_changed"
    assert "Laura" not in completed["channel_message"]["content"]
    assert "@" not in completed["channel_message"]["content"]
    assert calls["n"] == 1
    assert "Board next:" in calls["last"]
    assert "Who speaks next?" not in calls["last"]
    # The card is handed to Laura as Work, so she is not also pinned in Talk.
    # She stays on the router's Work-bound list; this router named no one.
    assert _channel_wakes(completed) == []
    rounds = db.list_channel_response_rounds(channel.id)
    assert len(rounds) == 1
    meta = channel_round_db.get_channel_round_meta(rounds[0].id)
    assert meta["work_bind_ids"] == [laura.id]
    assert laura.id not in meta["pinned_ids"]
    assert "Work-bound:" in calls["last"]
    assert "| Laura" in calls["last"].split("Work-bound:", 1)[1]
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == nxt.task.id
    assert work[0]["source_channel"] == "work"
    assert db.get_task(nxt.task.id).status == "pending"


@pytest.mark.asyncio
async def test_deleg_handoff_opens_a_system_ai_round(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id, title="Write the status note")
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "delegated",
            "agentId": laura.id,
            "followUpMessage": "Needs a writer.",
        },
        jimothy,
        state,
    )
    assert result["event"] == "status_changed"
    assert "@" not in result["channel_message"]["content"]
    assert calls["n"] == 1
    assert "Who speaks next?" not in calls["last"]
    wakes = _channel_wakes(result)
    assert wakes[0]["agent_id"] == laura.id
    round_id = wakes[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["router_mode"] == "system"


@pytest.mark.asyncio
async def test_done_prose_name_does_not_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id]])
    _task, completed = await _done(
        jimothy,
        channel,
        follow_up="Laura takes the next review.",
    )
    wakes = _channel_wakes(completed)
    assert wakes[0]["agent_id"] == jim.id
    round_id = wakes[0]["payload"]["round_id"]
    assert laura.id not in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]


@pytest.mark.asyncio
async def test_next_owners_pending_card_is_a_work_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[jim.id, laura.id]])
    audit = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="G0 re-audit")
    assert audit.task is not None
    _task, completed = await _done(
        jimothy,
        channel,
        follow_up="Evidence is on the channel.",
        next_owners=[laura.id],
    )
    assert calls["n"] == 1
    assert "Who speaks next?" not in calls["last"]
    # next_owners hands Laura her card as Work: one Work wake, no Talk pin.
    # The router named her second, so she speaks in the router's order.
    talk = _channel_wakes(completed)
    assert [item["agent_id"] for item in talk] == [jim.id]
    round_id = talk[0]["payload"]["round_id"]
    assert _queue(round_id) == [jim.id, laura.id]
    assert laura.id not in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == audit.task.id
    assert work[0]["source_channel"] == "work"


@pytest.mark.asyncio
async def test_done_work_bind_keeps_the_owner_and_other_speakers_on_talk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id, laura.id]])
    nxt = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="Review the draft")
    assert nxt.task is not None
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    # Laura gets her card once, as Work; in Talk she is not forced first.
    talk = _channel_wakes(completed)
    assert [item["agent_id"] for item in talk] == [jim.id]
    round_id = talk[0]["payload"]["round_id"]
    assert _queue(round_id) == [jim.id, laura.id]
    assert laura.id not in channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == nxt.task.id


def test_status_who_is_up_does_not_invent_work(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id, laura.id, jimothy.id]])
    audit = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="G0 re-audit")
    assert audit.task is not None
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="who is up?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert triggers
    assert {item["trigger_type"] for item in triggers} == {"channel_message"}
    round_id = triggers[0]["payload"]["round_id"]
    queued = {item.agent_id for item in db.list_channel_response_candidates(round_id)}
    assert laura.id in queued
    assert db.get_task(audit.task.id).status == "pending"


def test_status_next_owners_do_not_bind_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    audit = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="G0 re-audit")
    assert audit.task is not None
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "Still on the evidence.",
            "nextOwners": [laura.id],
        },
        jimothy,
        state,
        {
            "type": "task_follow_up",
            "task_id": creation.task.id,
            "content": "Who is up?",
            "from_name": "Human Operator",
        },
    )
    assert calls["n"] == 1
    assert _work_wakes(result) == []
    assert _channel_wakes(result)[0]["agent_id"] == laura.id
    assert db.get_task(audit.task.id).status == "pending"


@pytest.mark.asyncio
async def test_soft_blocked_next_card_stays_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[]])
    blocked = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="G0 re-audit")
    assert blocked.task is not None
    transition_task(
        blocked.task.id,
        "blocked",
        reason="Blocked — no progress. @Jim",
        actor="BossMod",
        status_note="Blocked — no progress. @Jim",
    )
    unrelated = _channel_task(assignee_id=jim.id, channel_id=channel.id, title="Hold the notes")
    assert unrelated.task is not None
    transition_task(
        unrelated.task.id,
        "blocked",
        reason="Blocked — no progress. @Laura",
        actor="BossMod",
        status_note="Blocked — no progress. @Laura",
    )
    _task, completed = await _done(
        jimothy,
        channel,
        follow_up="Evidence is on the channel.",
        next_owners=[laura.id],
    )
    assert _work_wakes(completed) == []
    assert db.get_task(blocked.task.id).status == "blocked"
    assert db.get_task(unrelated.task.id).status == "blocked"


def test_decision_next_owners_pin_the_share(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[]])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "Draft is saved.",
            "nextOwners": [laura.id],
        },
        jimothy,
        state,
        {
            "type": "task_follow_up",
            "task_id": creation.task.id,
            "content": "Share the review.",
            "from_name": "Human Operator",
        },
    )
    assert calls["n"] == 1
    wakes = _channel_wakes(result)
    assert wakes[0]["agent_id"] == laura.id
    assert "@" not in result["channel_message"]["content"]


_ECHO_PASS = "If you would only restate what is already in the thread, pass. New substance only."


def test_awoken_channel_turn_has_a_soft_pass_line() -> None:
    decision = load_default_prompt("runtime_contract_decision")
    assert decision.count(_ECHO_PASS) == 2
    # The echo rule stays; the length limit, the "nudged" framing and the
    # next-owner nudge copy are gone from every thread-wake prompt.
    trigger_event = load_default_prompt("runtime_block_trigger_event")
    for text in (decision, trigger_event):
        assert "One line is enough" not in text
        assert "You were nudged" not in text
        assert "data.proceed" not in text
    assert trigger_event.count("Choose speak or pass. Pass uses observe and does not post to the channel.") == 2
    assert decision.count("If you need someone specific to act next, @ them by name. Do not invent @everyone.") == 2
    assert _ECHO_PASS not in Path("prompts/system_prompt.md").read_text(encoding="utf-8")
    assert _ECHO_PASS not in Path("prompts/default_role.md").read_text(encoding="utf-8")


def test_trigger_event_prompt_names_no_extension_commands() -> None:
    # Core prompts stay extension-agnostic: each extension teaches its own
    # commands through its prompt block, injected only when it is enabled.
    trigger_event = load_default_prompt("runtime_block_trigger_event")
    assert re.search(r"\bmail (read|reply|send|archive)\b", trigger_event) is None


def test_channel_accept_records_a_sticky_work_bind() -> None:
    jim, _laura, _jimothy, channel = _trio()
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Please take the notes.",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Write the notes",
            "reply": "I'll take the notes task.",
        },
        jim,
        state,
        {
            "type": "channel_message",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "round_id": triggers[0]["payload"]["round_id"],
            "source_message_id": message.id,
            "content": message.content,
            "from_name": "Human Operator",
            "author_type": "human",
            "dispatch_mode": "rounds",
        },
    )
    round_id = triggers[0]["payload"]["round_id"]
    # Taking the task was Jim's turn; the round moves on to the next member.
    wakes = [(item.get("trigger_type"), item.get("agent_id")) for item in result["trigger_requests"]]
    assert ("activity_resumed", jim.id) in wakes
    assert [agent_id for kind, agent_id in wakes if kind == "channel_message"] == [_queue(round_id)[1]]
    meta = channel_round_db.get_channel_round_meta(round_id)
    assert meta["work_bind_ids"] == [jim.id]
    task_id = meta["work_binds"][0]["task_id"]
    assert db.get_task(task_id).status == "accepted"
    note_human_snapshot(channel.id, [])
    assert channel_round_db.get_channel_round_meta(round_id)["work_bind_ids"] == []
    assert db.get_task(task_id).status == "accepted"


def test_work_bound_member_named_by_the_router_is_woken_and_work_stays_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    prompts: list[str] = []

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        prompts.append("\n".join(item.get("content") or "" for item in messages))
        return route_reply(messages, [laura.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    creation = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="Review the draft")
    assert creation.task is not None
    _hold(channel, laura, creation.task)
    live = activity_runtime.activate_work_activity(laura.id, creation.task)
    assert live is not None
    # A colleague asks the working agent something mid-shift.
    share = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name=jim.name,
        content="Laura, how far along is the review?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=share.id,
        content=share.content,
        from_name=jim.name,
        author_type="agent",
        exclude_agent_ids={jim.id},
        from_agent=jim.id,
    )
    assert [(item["trigger_type"], item["agent_id"]) for item in triggers] == [("channel_message", laura.id)]
    assert "Work-bound:" in prompts[0]
    assert "| Laura" in prompts[0].split("Work-bound:", 1)[1]
    assert laura.id not in prompts[0]
    assert "Being work-bound never excludes a member" in prompts[0]
    state = db.get_agent_state(laura.id)
    assert state is not None
    apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "Halfway through; the fixtures section is next.",
        },
        laura,
        state,
        {**triggers[0]["payload"], "type": "channel_message"},
    )
    still = activity_runtime.get_active_work_activity(laura.id)
    assert still is not None and still.id == live.id
    assert db.get_task(creation.task.id).status in {"accepted", "active"}
