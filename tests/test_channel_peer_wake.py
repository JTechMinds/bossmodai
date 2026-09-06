"""Human ingress opens a channel round; agent shares must open a new one.

Cause on main: operator posts wake every member. Agent Task→channel shares
and system completion cards only broadcast, so peers burn the first round
and the findings stay transcript-only.

Bar: agent-authored shares that need peer attention open a new response
round for other members (exclude author). Pure system completion cards
do not wake anyone. No LLM.
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
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.channel_rounds import begin_channel_response, start_channel_peer_round
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.notifications import ChatNotification, persist_channel_notification
from core.messaging import route_human_channel_message
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


def _three_members() -> tuple[Any, Any, Any, Any]:
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Jim, Laura, Jimothy",
        member_agent_ids=[jim.id, laura.id, jimothy.id],
        created_by=jimothy.id,
    )
    return jim, laura, jimothy, channel


def _complete_human_round(channel_id: str, member_ids: list[str]) -> Any:
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content="Jimothy share the review findings, then I want feedback on how to proceed.",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel_id,
        source_message_id=message.id,
    )
    for agent_id in member_ids:
        db.create_channel_response_candidate(round_id=round_record.id, agent_id=agent_id)
        db.mark_channel_candidate_responded(round_id=round_record.id, agent_id=agent_id)
    db.maybe_complete_channel_response_round(round_record.id)
    refreshed = db.get_channel_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"
    return refreshed


def _channel_task(*, assignee_id: str, channel_id: str):
    return create_or_bind_task(
        title="Share review findings",
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


def _channel_message_requests(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        request
        for request in result.get("trigger_requests") or []
        if request.get("trigger_type") == "channel_message"
    ]


def _queued_channel_messages(agent_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == "channel_message" and row["status"] == "queued"
    ]


def _trigger_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _queued_for_round(agent_id: str, round_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in _queued_channel_messages(agent_id)
        if _trigger_payload(row).get("round_id") == round_id
    ]


def _round_count(channel_id: str) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM channel_response_rounds WHERE channel_id = $1",
        [channel_id],
    )
    return int(rows[0]["n"]) if rows else 0


class _SilentBroadcast:
    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        return None


class _RecordingServices:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_start_channel_peer_round_excludes_author() -> None:
    jim, laura, jimothy, channel = _three_members()
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jimothy.id,
        author_name=jimothy.name,
        content="Review summary is ready at /me/jtech-cli-review-summary.md",
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
        channel_name=channel.name,
    )

    assert {item["agent_id"] for item in triggers} == {jim.id, laura.id}
    assert all(item["trigger_type"] == "channel_message" for item in triggers)
    assert all(item["payload"]["round_id"] == triggers[0]["payload"]["round_id"] for item in triggers)
    assert all(item["payload"]["from_agent"] == jimothy.id for item in triggers)
    assert triggers[0]["payload"]["author_type"] == "agent"
    assert db.get_channel_response_candidate(
        round_id=triggers[0]["payload"]["round_id"],
        agent_id=jimothy.id,
    ) is None


@pytest.mark.asyncio
async def test_human_channel_message_still_wakes_every_member() -> None:
    jim, laura, jimothy, channel = _three_members()
    services = _RecordingServices()

    result = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Please share findings, then I want feedback.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )

    assert result["round_id"]
    assert {call["agent_id"] for call in services.calls} == {jim.id, laura.id, jimothy.id}
    assert all(call["trigger_type"] == "channel_message" for call in services.calls)
    assert all(call["payload"]["round_id"] == result["round_id"] for call in services.calls)
    assert all(call["payload"]["author_type"] == "human" for call in services.calls)


@pytest.mark.asyncio
async def test_human_ask_then_agent_findings_share_gives_peers_a_turn() -> None:
    jim, laura, jimothy, channel = _three_members()
    services = _RecordingServices()
    asked = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Jimothy share the review findings, then I want feedback on how to proceed.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    first_round_id = asked["round_id"]
    assert {call["agent_id"] for call in services.calls} == {jim.id, laura.id, jimothy.id}
    for agent_id in (jim.id, laura.id, jimothy.id):
        db.mark_channel_candidate_responded(round_id=first_round_id, agent_id=agent_id)
    db.maybe_complete_channel_response_round(first_round_id)
    first_round = db.get_channel_response_round(first_round_id)
    assert first_round is not None
    assert first_round.status == "completed"

    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Shared Jtech-CLI review findings.",
            "followUpMessage": (
                "Jtech-CLI review summary is ready at /me/jtech-cli-review-summary.md "
                "— highlights plus top issues and recommendations."
            ),
            "doneClaim": {"type": "proof", "ev": "summary posted to the shared channel"},
        },
        jimothy,
        state,
    )
    assert completed["event"] == "status_changed"
    assert completed.get("channel_message")
    assert completed["channel_message"]["author_type"] == "agent"
    assert "/me/jtech-cli-review-summary.md" in completed["channel_message"]["content"]

    peer_wakes = _channel_message_requests(completed)
    assert {item["agent_id"] for item in peer_wakes} == {jim.id, laura.id}
    new_round_ids = {item["payload"]["round_id"] for item in peer_wakes}
    assert len(new_round_ids) == 1
    new_round_id = next(iter(new_round_ids))
    assert new_round_id != first_round_id
    assert all(item["payload"]["author_type"] == "agent" for item in peer_wakes)
    assert all(item["payload"]["from_agent"] == jimothy.id for item in peer_wakes)

    persist_result_triggers(completed)
    assert _queued_for_round(jim.id, new_round_id)
    assert _queued_for_round(laura.id, new_round_id)
    assert not _queued_for_round(jimothy.id, new_round_id)

    queued, active = begin_channel_response(
        jim,
        {
            "round_id": new_round_id,
            "channel_id": channel.id,
            "content": completed["channel_message"]["content"],
        },
    )
    assert queued["event"] == "decision_applied"
    assert active is True


@pytest.mark.asyncio
async def test_non_channel_task_complete_does_not_open_channel_round() -> None:
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=1, desk_y=1)
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
    activity_runtime.activate_work_activity(jimothy.id, creation.task)
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Note is done.",
            "followUpMessage": "Finished the private note.",
            "doneClaim": {"type": "proof", "ev": "desk note written"},
        },
        jimothy,
        state,
    )
    assert completed["event"] == "status_changed"
    assert _channel_message_requests(completed) == []
    assert "channel_message" not in completed


def test_channel_response_reply_does_not_open_nested_peer_round() -> None:
    jim, laura, jimothy, channel = _three_members()
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Jimothy share the review findings.",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel.id,
        source_message_id=message.id,
    )
    for agent_id in (jim.id, laura.id, jimothy.id):
        db.create_channel_response_candidate(round_id=round_record.id, agent_id=agent_id)
        if agent_id != jimothy.id:
            db.mark_channel_candidate_responded(round_id=round_record.id, agent_id=agent_id)
        else:
            db.update_channel_response_candidate(
                round_id=round_record.id,
                agent_id=agent_id,
                status="responding",
                queue_position=3,
            )

    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "On it — I will walk the team through the findings next.",
        },
        jimothy,
        state,
        {
            "type": "channel_response",
            "channel_id": channel.id,
            "round_id": round_record.id,
            "content": message.content,
            "from_name": "Human Operator",
            "author_type": "human",
            "channel_name": channel.name,
            "source_message_id": message.id,
        },
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")
    assert _channel_message_requests(result) == []
    refreshed = db.get_channel_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


def test_task_channel_share_via_decision_reply_opens_peer_round() -> None:
    jim, laura, jimothy, channel = _three_members()
    first_round = _complete_human_round(channel.id, [jim.id, laura.id, jimothy.id])
    creation = _channel_task(assignee_id=jimothy.id, channel_id=channel.id)
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Jtech-CLI review summary is ready at /me/jtech-cli-review-summary.md",
        },
        jimothy,
        state,
        {
            "type": "task_follow_up",
            "task_id": creation.task.id,
            "content": "Share the review findings with the team.",
            "from_name": "Human Operator",
        },
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")
    assert result["channel_message"]["author_type"] == "agent"
    peer_wakes = _channel_message_requests(result)
    assert {item["agent_id"] for item in peer_wakes} == {jim.id, laura.id}
    new_round_id = peer_wakes[0]["payload"]["round_id"]
    assert new_round_id != first_round.id
    persist_result_triggers(result)
    assert _queued_channel_messages(jim.id)
    assert _queued_channel_messages(laura.id)
    assert not _queued_channel_messages(jimothy.id)


def test_system_completion_card_does_not_open_peer_round() -> None:
    jim, laura, jimothy, channel = _three_members()
    before = _round_count(channel.id)
    persist_channel_notification(
        jimothy,
        ChatNotification(
            kind="completion",
            content=(
                'Jimothy finished "Share review findings" and saved it to '
                "/me/jtech-cli-review-summary.md. Claim: artifact "
                "/me/jtech-cli-review-summary.md."
            ),
            source_channel="channel",
            policy="completion_blocked",
            channel_id=channel.id,
        ),
    )
    assert _round_count(channel.id) == before
    assert not _queued_channel_messages(jim.id)
    assert not _queued_channel_messages(laura.id)
    assert not _queued_channel_messages(jimothy.id)
