"""Wake from intent, structured next_owners, and the next Board card.

System AI picks ambient Talk and Done/handoff rounds. Operator @ is a pin.
Empty speak on a Done/handoff route gets one repair, then stops. Prose
names are not a wake layer. When Done lands and the Board or structured
next_owners names a pending card, that assignee gets a Work bind. A status
round does not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.channel_router import build_router_messages
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.decision_contract import parse_decision
from core.agent_loop.decision_runtime import apply_decision
from core.models.message import HUMAN_SENDER_ID
from core.tasking.board import next_board_owner_id
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task
from db import channel_response_rounds as channel_round_db


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
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1, model_work="identity-big")
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1, model_work="identity-big")
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=3, desk_y=1, model_work="identity-big")
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


def _payload(speak: list[str], stay_out: list[str]) -> str:
    return json.dumps({"speak": speak, "stay_out": stay_out})


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
        return replies[calls["n"] - 1]

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    return calls


async def _done(jimothy, channel, *, follow_up: str, next_owners: list[str] | None = None):
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
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
    messages = build_router_messages(
        members=[{"id": "jim", "name": "Jim", "role": "PM"}],
        latest_message="Where are we?",
        pending_mention_ids=["jim"],
        sticky="Board next: laura | Laura",
    )
    blob = "\n".join(item["content"] for item in messages)
    assert "from intent, not wording" in blob
    assert "Do not wake people merely because their name appears" in blob
    assert "Board next: laura | Laura" in blob
    assert '"speak"' in blob and '"stay_out"' in blob
    assert "at most 2" in blob


def test_next_owners_parses_on_the_decision_envelope() -> None:
    decision = parse_decision(
        '{"act":"reply","intent":"status","msg":"Draft is saved.","next_owners":["laura"],"th":"hand off"}'
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
    _script(monkeypatch, [_payload([jim.id], [laura.id, jimothy.id])])
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
    calls = _script(monkeypatch, [_payload([jim.id], [laura.id, jimothy.id])])
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
    _script(monkeypatch, [_payload([jim.id], [laura.id])])
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
    calls = _script(monkeypatch, [_payload([laura.id], [jim.id])])
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    wakes = _channel_wakes(completed)
    assert calls["n"] == 1
    assert [item["agent_id"] for item in wakes] == [laura.id]
    round_id = wakes[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["router_mode"] == "system"
    assert "from intent, not wording" in calls["last"]


@pytest.mark.asyncio
async def test_empty_handoff_speak_repairs_once_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            _payload([], [jim.id, laura.id]),
            _payload([], [jim.id, laura.id]),
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
            _payload([], [jim.id, laura.id]),
            _payload([laura.id], [jim.id]),
        ],
    )
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    assert calls["n"] == 2
    assert [item["agent_id"] for item in _channel_wakes(completed)] == [laura.id]


@pytest.mark.asyncio
async def test_next_owners_hard_wake_without_an_at(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [_payload([], [jim.id, laura.id])])
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
    calls = _script(monkeypatch, [_payload([], [jim.id, laura.id])])
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
    assert _channel_wakes(completed) == []
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == nxt.task.id
    assert work[0]["source_channel"] == "work"
    assert db.get_task(nxt.task.id).status == "pending"


@pytest.mark.asyncio
async def test_deleg_handoff_opens_a_system_ai_round(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [_payload([], [laura.id])])
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
    _script(monkeypatch, [_payload([jim.id], [laura.id])])
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
    calls = _script(monkeypatch, [_payload([jim.id, laura.id], [])])
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
    assert all(item["agent_id"] != laura.id for item in _channel_wakes(completed))
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == audit.task.id
    assert work[0]["source_channel"] == "work"


@pytest.mark.asyncio
async def test_done_work_bind_leaves_other_speakers_on_talk(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [_payload([jim.id, laura.id], [])])
    nxt = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="Review the draft")
    assert nxt.task is not None
    _task, completed = await _done(jimothy, channel, follow_up="Draft is saved.")
    assert [item["agent_id"] for item in _channel_wakes(completed)] == [jim.id]
    work = _work_wakes(completed)
    assert [item["agent_id"] for item in work] == [laura.id]
    assert work[0]["task_id"] == nxt.task.id


def test_status_who_is_up_does_not_invent_work(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, jimothy, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [_payload([jim.id, laura.id, jimothy.id], [])])
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
    calls = _script(monkeypatch, [_payload([], [laura.id])])
    audit = _channel_task(assignee_id=laura.id, channel_id=channel.id, title="G0 re-audit")
    assert audit.task is not None
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Still on the evidence.",
            "proceedUntagged": True,
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
    _script(monkeypatch, [_payload([], [jim.id, laura.id])])
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
    calls = _script(monkeypatch, [_payload([], [jim.id, laura.id])])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Draft is saved.",
            "proceedUntagged": True,
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
