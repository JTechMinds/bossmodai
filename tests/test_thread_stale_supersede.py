"""Stale thread race: newer human tip discards in-flight replies at deliver.

Hugh and Debra both wake on one message. A later human tip must not let the
old prose land. The composer stays unlocked. No LLM. No @-only wake filter.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.channel_rounds import begin_channel_response
from core.agent_loop.decision_runtime import apply_decision
from core.messaging import route_human_channel_message
from core.models.channel import THREAD_STALE_SKIP_KIND, THREAD_STALE_SKIP_LINE


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


def _hugh_and_debra() -> tuple[Any, Any, Any]:
    hugh = db.create_agent("Hugh", role="PM", desk_x=1, desk_y=1)
    debra = db.create_agent("Debra", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Hugh, Debra",
        member_agent_ids=[hugh.id, debra.id],
        created_by=hugh.id,
    )
    return hugh, debra, channel


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


def _answer(reply: str) -> dict[str, Any]:
    return {
        "decision": "answer",
        "intentKind": "status_request",
        "reply": reply,
        "proceedUntagged": True,
    }


def _channel_trigger(
    *,
    agent_id: str,
    channel_id: str,
    channel_name: str,
    message_id: str,
    round_id: str,
    content: str,
    trigger_type: str = "channel_message",
) -> dict[str, Any]:
    return {
        "type": trigger_type,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "round_id": round_id,
        "source_message_id": message_id,
        "content": content,
        "from_name": "Human Operator",
        "author_type": "human",
        "trigger_id": agent_id,
    }


def _trigger_payload(row: dict[str, Any] | Any) -> dict[str, Any]:
    raw = row["payload"] if isinstance(row, dict) else getattr(row, "payload", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _queued_channel_rows(agent_id: str) -> list[Any]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] in {"channel_message", "channel_response"}
        and row["status"] == "queued"
    ]


def _skip_lines(channel_id: str) -> list[Any]:
    return [
        item
        for item in db.list_channel_messages(channel_id)
        if item.author_type == "system"
        and item.notification_kind == THREAD_STALE_SKIP_KIND
        and item.content == THREAD_STALE_SKIP_LINE
    ]


def _agent_prose(channel_id: str) -> list[str]:
    return [
        item.content
        for item in db.list_channel_messages(channel_id)
        if item.author_type == "agent"
    ]


@pytest.mark.asyncio
async def test_tip_moved_discards_reply_and_posts_one_skip_line() -> None:
    hugh, debra, channel = _hugh_and_debra()
    services = _RecordingServices()
    first = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Please walk the findings.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    second = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Wait — use the newer brief instead.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )

    state = db.get_agent_state(hugh.id)
    assert state is not None
    result = apply_decision(
        _answer("Stale walkthrough of the first brief."),
        hugh,
        state,
        _channel_trigger(
            agent_id=hugh.id,
            channel_id=channel.id,
            channel_name=channel.name,
            message_id=first["message_id"],
            round_id=first["round_id"],
            content="Please walk the findings.",
        ),
    )

    assert result["event"] == "decision_applied"
    assert "skipped" in result["detail"].lower()
    assert _agent_prose(channel.id) == []
    skips = _skip_lines(channel.id)
    assert len(skips) == 1
    assert skips[0].content == THREAD_STALE_SKIP_LINE
    assert result.get("channel_message", {}).get("message_id") == skips[0].id

    latest_round = db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=second["message_id"],
    )
    assert latest_round is not None
    assert latest_round.id == second["round_id"]


@pytest.mark.asyncio
async def test_multiple_skips_collapse_to_one_line_per_tip() -> None:
    hugh, debra, channel = _hugh_and_debra()
    services = _RecordingServices()
    first = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="First ask.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Newer ask.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )

    hugh_state = db.get_agent_state(hugh.id)
    debra_state = db.get_agent_state(debra.id)
    assert hugh_state is not None and debra_state is not None
    first_trigger = _channel_trigger(
        agent_id=hugh.id,
        channel_id=channel.id,
        channel_name=channel.name,
        message_id=first["message_id"],
        round_id=first["round_id"],
        content="First ask.",
    )
    apply_decision(_answer("Hugh stale prose."), hugh, hugh_state, first_trigger)
    apply_decision(
        _answer("Debra stale prose."),
        debra,
        debra_state,
        {**first_trigger, "trigger_id": debra.id},
    )

    assert _agent_prose(channel.id) == []
    assert len(_skip_lines(channel.id)) == 1


@pytest.mark.asyncio
async def test_skip_requeues_a_fresh_wake_when_the_new_tip_has_no_round() -> None:
    hugh, _debra, channel = _hugh_and_debra()
    first = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="First ask.",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel.id,
        source_message_id=first.id,
    )
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=hugh.id)
    later = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Newer ask with no round yet.",
        source_channel="channel",
    )
    assert db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=later.id,
    ) is None

    state = db.get_agent_state(hugh.id)
    assert state is not None
    result = apply_decision(
        _answer("Stale first-ask reply."),
        hugh,
        state,
        _channel_trigger(
            agent_id=hugh.id,
            channel_id=channel.id,
            channel_name=channel.name,
            message_id=first.id,
            round_id=round_record.id,
            content=first.content,
        ),
    )

    persist_result_triggers(result)
    fresh = db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=later.id,
    )
    assert fresh is not None
    assert fresh.id != round_record.id
    wakes = [
        row
        for row in _queued_channel_rows(hugh.id)
        if _trigger_payload(row).get("source_message_id") == later.id
    ]
    assert len(wakes) == 1
    assert wakes[0]["trigger_type"] == "channel_message"
    assert _trigger_payload(wakes[0]).get("round_id") == fresh.id
    assert len(_skip_lines(channel.id)) == 1


@pytest.mark.asyncio
async def test_new_human_message_cancels_queued_older_rounds_only() -> None:
    hugh, debra, channel = _hugh_and_debra()
    services = _RecordingServices()
    first = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="First ask.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    hugh_old = [
        row
        for row in _queued_channel_rows(hugh.id)
        if _trigger_payload(row).get("round_id") == first["round_id"]
    ]
    assert len(hugh_old) == 1
    claimed = db.claim_trigger(hugh_old[0]["id"])
    assert claimed is not None
    assert claimed.status == "claimed"

    second = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Newer ask.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )

    hugh_claimed = db.get_agent_trigger(claimed.id)
    assert hugh_claimed is not None
    assert hugh_claimed.status == "claimed"
    assert _trigger_payload(hugh_claimed).get("source_message_id") == first["message_id"]

    debra_old = [
        row
        for row in db.list_agent_triggers(debra.id)
        if _trigger_payload(row).get("round_id") == first["round_id"]
    ]
    assert debra_old == []
    debra_candidate = db.get_channel_response_candidate(
        round_id=first["round_id"],
        agent_id=debra.id,
    )
    assert debra_candidate is not None
    assert debra_candidate.status == "observed"

    hugh_candidate = db.get_channel_response_candidate(
        round_id=first["round_id"],
        agent_id=hugh.id,
    )
    assert hugh_candidate is not None
    assert hugh_candidate.status == "pending"

    assert any(
        _trigger_payload(row).get("source_message_id") == second["message_id"]
        for row in _queued_channel_rows(hugh.id)
    )
    assert any(
        _trigger_payload(row).get("source_message_id") == second["message_id"]
        for row in _queued_channel_rows(debra.id)
    )


@pytest.mark.asyncio
async def test_in_flight_channel_response_is_superseded_before_post() -> None:
    hugh, debra, channel = _hugh_and_debra()
    first = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="First ask.",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel.id,
        source_message_id=first.id,
    )
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=hugh.id)
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=debra.id)

    queued, active_now = begin_channel_response(
        hugh,
        {
            "round_id": round_record.id,
            "channel_id": channel.id,
            "source_message_id": first.id,
        },
    )
    assert active_now is True
    assert "joined" in queued["detail"].lower()
    hugh_row = db.get_channel_response_candidate(round_id=round_record.id, agent_id=hugh.id)
    assert hugh_row is not None
    assert hugh_row.status == "responding"

    later = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Newer ask mid-turn.",
        source_channel="channel",
    )

    state = db.get_agent_state(hugh.id)
    assert state is not None
    result = apply_decision(
        _answer("In-flight stale prose must not land."),
        hugh,
        state,
        _channel_trigger(
            agent_id=hugh.id,
            channel_id=channel.id,
            channel_name=channel.name,
            message_id=first.id,
            round_id=round_record.id,
            content=first.content,
            trigger_type="channel_response",
        ),
    )

    assert result["event"] == "decision_applied"
    assert _agent_prose(channel.id) == []
    assert len(_skip_lines(channel.id)) == 1
    refreshed = db.get_channel_response_candidate(round_id=round_record.id, agent_id=hugh.id)
    assert refreshed is not None
    assert refreshed.status == "observed"
    persist_result_triggers(result)
    fresh = db.get_channel_response_round_for_source(
        channel_id=channel.id,
        source_message_id=later.id,
    )
    assert fresh is not None
    assert any(
        _trigger_payload(row).get("source_message_id") == later.id
        for row in _queued_channel_rows(hugh.id)
    )
    assert any(
        _trigger_payload(row).get("source_message_id") == later.id
        for row in _queued_channel_rows(debra.id)
    )
