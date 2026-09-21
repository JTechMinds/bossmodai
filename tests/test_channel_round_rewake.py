"""Ordered channel rounds, off-channel pass, and the agent-turn cap."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.channel_round_plan import (
    DISPATCH_FANOUT,
    DISPATCH_ROUNDS,
    classify_channel_dispatch,
)
from core.agent_loop.channel_rounds import advance_channel_round, start_channel_peer_round
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.runtime_core import AUDIENCE_SOFT_JUDGMENT
from core.messaging import route_human_channel_message
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
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    ada = db.create_agent("Ada", role="QA", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[jim.id, laura.id, ada.id],
        created_by=jim.id,
    )
    return jim, laura, ada, channel


def _message(channel_id: str, content: str):
    return db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
    )


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


class _SilentBroadcast:
    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        return None


class _RecordingServices:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
        )


def test_settings_seed_turn_cap_and_round_cap() -> None:
    assert config.get("max_concurrent_agent_turns") == "2"
    assert config.get("channel_response_round_cap") == "4"
    assert "speak or pass" in AUDIENCE_SOFT_JUDGMENT
    assert "do not post a chat message" in AUDIENCE_SOFT_JUDGMENT
    assert "@" not in AUDIENCE_SOFT_JUDGMENT


def test_mention_then_lead_order_and_status_stays_on_rounds() -> None:
    jim, laura, ada, channel = _trio()
    message = _message(channel.id, "@Laura where are we on the review?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert len(triggers) == 1
    assert triggers[0]["agent_id"] == laura.id
    assert triggers[0]["payload"]["dispatch_mode"] == DISPATCH_ROUNDS
    round_id = triggers[0]["payload"]["round_id"]
    order = [
        candidate.agent_id
        for candidate in sorted(
            db.list_channel_response_candidates(round_id),
            key=lambda candidate: candidate.queue_position or 0,
        )
    ]
    assert order[0] == laura.id
    assert jim.id in order
    assert ada.id in order
    assert classify_channel_dispatch(message.content, [{"id": jim.id, "name": "Jim"}]) == DISPATCH_ROUNDS


def test_everyone_and_distinct_tools_fan_out_ambiguous_stays_rounds() -> None:
    jim, laura, ada, channel = _trio()
    members = [
        {"id": jim.id, "name": "Jim", "role": "PM"},
        {"id": laura.id, "name": "Laura", "role": "Eng"},
        {"id": ada.id, "name": "Ada", "role": "QA"},
    ]
    assert classify_channel_dispatch("@everyone where are we?", members) == DISPATCH_FANOUT
    assert (
        classify_channel_dispatch(
            "@Laura review the diff and @Jim implement the parser",
            members,
        )
        == DISPATCH_FANOUT
    )
    assert classify_channel_dispatch("where are we?", members) == DISPATCH_ROUNDS
    assert classify_channel_dispatch("@Jim @Laura thoughts?", members) == DISPATCH_ROUNDS

    message = _message(channel.id, "@everyone ship the notes in parallel")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert {item["agent_id"] for item in triggers} == {jim.id, laura.id, ada.id}
    assert triggers[0]["payload"]["dispatch_mode"] == DISPATCH_FANOUT


def test_pass_does_not_post_and_advances_the_queue() -> None:
    jim, laura, ada, channel = _trio()
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == jim.id
    state = db.get_agent_state(jim.id)
    assert state is not None
    before = [item.id for item in db.list_channel_messages(channel.id)]
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "I'll stay quiet.",
            "proceedUntagged": True,
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
            "dispatch_mode": DISPATCH_ROUNDS,
        },
    )
    assert "channel_message" not in result
    after = db.list_channel_messages(channel.id)
    assert [item.id for item in after] == before
    assert all("stay quiet" not in item.content.lower() for item in after)
    diags = db.get_diagnostics(agent_id=jim.id)
    assert any(row.get("action_name") == "channel_pass" for row in diags)
    assert result["trigger_requests"]
    assert result["trigger_requests"][0]["agent_id"] != jim.id
    meta = channel_round_db.get_channel_round_meta(triggers[0]["payload"]["round_id"])
    assert jim.id in meta["stepped_out"]


def test_observe_pass_stays_off_channel() -> None:
    jim, _laura, _ada, channel = _trio()
    message = _message(channel.id, "Status check.")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {"decision": "observe", "intentKind": "other", "thought": "Not my specialty."},
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
            "dispatch_mode": DISPATCH_ROUNDS,
        },
    )
    assert "channel_message" not in result
    assert db.list_channel_messages(channel.id)[-1].id == message.id


def test_mid_round_mention_waits_for_the_next_round() -> None:
    jim, laura, ada, channel = _trio()
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    round_id = triggers[0]["payload"]["round_id"]
    order_before = [
        candidate.agent_id
        for candidate in sorted(
            db.list_channel_response_candidates(round_id),
            key=lambda candidate: candidate.queue_position or 0,
        )
    ]
    assert order_before[0] == jim.id
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Shipping today. @Ada please take the next look.",
            "proceedUntagged": True,
        },
        jim,
        state,
        {
            "type": "channel_message",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "round_id": round_id,
            "source_message_id": message.id,
            "content": message.content,
            "from_name": "Human Operator",
            "author_type": "human",
            "dispatch_mode": DISPATCH_ROUNDS,
            "spoken_text": "Shipping today. @Ada please take the next look.",
        },
    )
    assert result.get("channel_message", {}).get("content", "").startswith("Shipping today")
    nxt = result["trigger_requests"][0]
    assert nxt["agent_id"] != ada.id or nxt["payload"]["round_id"] == round_id
    assert nxt["payload"]["round_id"] == round_id
    meta = channel_round_db.get_channel_round_meta(round_id)
    assert ada.id in meta["next_mentions"]
    pending = [
        candidate.agent_id
        for candidate in db.list_channel_response_candidates(round_id)
        if candidate.status == "pending"
    ]
    assert ada.id in pending or nxt["agent_id"] == ada.id
    # Ada was not pulled ahead of the member already next in this round.
    order_after = [
        candidate.agent_id
        for candidate in sorted(
            db.list_channel_response_candidates(round_id),
            key=lambda candidate: candidate.queue_position or 0,
        )
    ]
    assert order_after == order_before
    assert nxt["agent_id"] == order_before[1]


def test_step_out_is_sticky_until_mentioned_and_cap_stops_at_four() -> None:
    jim, laura, _ada, channel = _trio()
    # Two-member snapshot: drop Ada by excluding her.
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
        exclude_agent_ids={_ada.id},
    )
    assert [item["agent_id"] for item in triggers] == [jim.id]
    base = {
        "content": message.content,
        "channel_id": channel.id,
        "from_name": "Human Operator",
        "author_type": "human",
        "source_message_id": message.id,
        "channel_name": channel.name,
        "dispatch_mode": DISPATCH_ROUNDS,
    }
    current = dict(base)
    current["round_id"] = triggers[0]["payload"]["round_id"]
    current["round_index"] = 1

    db.mark_channel_candidate_observed(round_id=current["round_id"], agent_id=jim.id)
    progress = advance_channel_round(current, spoke=False, speaker_id=jim.id)
    assert progress["trigger_requests"][0]["agent_id"] == laura.id

    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=laura_round, agent_id=laura.id)
    spoken = dict(base)
    spoken["round_id"] = laura_round
    spoken["round_index"] = 1
    spoken["spoken_text"] = "@Jim come back in."
    progress = advance_channel_round(
        spoken,
        spoke=True,
        speaker_id=laura.id,
        spoken_text="@Jim come back in.",
    )
    assert progress["trigger_requests"][0]["agent_id"] == jim.id
    assert progress["trigger_requests"][0]["payload"]["round_index"] == 2
    assert progress["round_marker"]["content"] == "Round 2"
    assert db.find_channel_round_marker(
        channel_id=channel.id,
        after_message_id=message.id,
        content="Round 2",
    )

    # Keep both speaking until the cap. Round 2 is already open.
    seen_indexes = {1, 2}
    wake = progress["trigger_requests"][0]
    for _ in range(12):
        round_id = wake["payload"]["round_id"]
        agent_id = wake["agent_id"]
        db.mark_channel_candidate_responded(round_id=round_id, agent_id=agent_id)
        trigger = dict(base)
        trigger.update(wake["payload"])
        progress = advance_channel_round(trigger, spoke=True, speaker_id=agent_id, spoken_text="Still here.")
        if not progress["trigger_requests"]:
            break
        wake = progress["trigger_requests"][0]
        seen_indexes.add(int(wake["payload"]["round_index"]))
    assert seen_indexes == {1, 2, 3, 4}
    assert progress["trigger_requests"] == []
    indexes = []
    for row in db.list_channel_response_rounds(channel.id):
        if row.source_message_id == message.id:
            indexes.append(channel_round_db.get_channel_round_meta(row.id)["round_index"])
    assert sorted(indexes) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_new_human_message_does_not_stack_two_snapshots() -> None:
    _jim, _laura, _ada, channel = _trio()
    services = _RecordingServices()
    await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Where are we?",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Where are we now?",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    active = db.list_channel_response_rounds(channel.id, status="active")
    assert len(active) == 1
    latest = db.get_channel_message(active[0].source_message_id)
    assert latest is not None
    assert "now" in latest.content


def test_turn_cap_is_global_and_repair_waits_behind_channel_lead() -> None:
    jim, laura, _ada, channel = _trio()
    message = _message(channel.id, "Where are we?")
    db.create_agent_trigger(
        agent_id=laura.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Decision parse failed. Continue.", "repair_wake": True},
    )
    db.create_agent_trigger(
        agent_id=jim.id,
        trigger_type="channel_message",
        source_channel="channel",
        payload={
            "content": message.content,
            "channel_id": channel.id,
            "source_message_id": message.id,
        },
    )
    queued = db.list_queued_triggers(limit=10)
    assert queued[0].trigger_type == "channel_message"
    assert queued[0].agent_id == jim.id

    full = TurnDispatcher()
    full._active_turns["slot-a"] = object()
    full._active_turns["slot-b"] = object()
    assert full._claim_available_trigger() is None
    assert db.get_agent_trigger(queued[0].id).status == "queued"

    same_agent = TurnDispatcher()
    same_agent._active_turns[jim.id] = object()
    claimed = same_agent._claim_available_trigger()
    assert claimed is not None
    assert claimed.agent_id == laura.id

    db.set_setting("max_concurrent_agent_turns", "3", "llm")
    config.reload()
    wider = TurnDispatcher()
    wider._active_turns["slot-a"] = object()
    wider._active_turns["slot-b"] = object()
    opened = wider._claim_available_trigger()
    assert opened is not None
    assert opened.agent_id == jim.id
