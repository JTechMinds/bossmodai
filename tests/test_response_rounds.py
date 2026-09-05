"""HA-STRUCT-P1-07 — meeting/channel response rounds share one implementation."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.channel_rounds import (
    begin_channel_response,
    finalize_channel_response,
    observe_channel_message,
)
from core.agent_loop.meeting_rounds import (
    begin_session_response,
    finalize_session_response,
    observe_session_message,
)
from core.agent_loop.response_rounds import (
    begin_shared_response,
    finalize_shared_response,
    observe_shared_message,
)
from db import channel_response_rounds, meeting_response_rounds
from db import response_rounds as shared


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


def test_shared_sql_is_single_implementation() -> None:
    meeting_source = open(meeting_response_rounds.__file__, encoding="utf-8").read()
    channel_source = open(channel_response_rounds.__file__, encoding="utf-8").read()
    shared_source = open(shared.__file__, encoding="utf-8").read()
    assert "INSERT INTO" not in meeting_source
    assert "INSERT INTO" not in channel_source
    assert "def reserve_slot" in shared_source
    assert "def mark_observed" in shared_source
    assert "def maybe_complete_round" in shared_source
    assert meeting_response_rounds.reserve_response_slot.__module__ == "db.meeting_response_rounds"
    assert channel_response_rounds.reserve_channel_response_slot.__module__ == "db.channel_response_rounds"


def test_loop_facades_delegate_to_shared_helpers() -> None:
    assert observe_session_message.__code__.co_names
    meeting_source = open(Path("core/agent_loop/meeting_rounds.py"), encoding="utf-8").read()
    channel_source = open(Path("core/agent_loop/channel_rounds.py"), encoding="utf-8").read()
    assert "observe_shared_message" in meeting_source
    assert "begin_shared_response" in meeting_source
    assert "finalize_shared_response" in meeting_source
    assert "observe_shared_message" in channel_source
    assert "def observe_shared_message" in open(
        Path("core/agent_loop/response_rounds.py"), encoding="utf-8"
    ).read()
    assert observe_shared_message is not observe_session_message
    assert begin_shared_response is not begin_session_response
    assert finalize_shared_response is not finalize_session_response


def _two_agents():
    host = db.create_agent("Host", role="PM", desk_x=1, desk_y=1)
    alice = db.create_agent("Alice", role="Eng", desk_x=2, desk_y=1)
    return host, alice


def test_observe_meeting_message_marks_candidate_and_completes_round() -> None:
    host, alice = _two_agents()
    session = db.create_meeting_session("meetingRoom", title="Sync", created_by_agent_id=host.id)
    message = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content="Please read this.",
        source_channel="chat",
    )
    round_record = db.create_meeting_response_round(
        session_id=session.id,
        source_message_id=message.id,
    )
    db.create_meeting_response_candidate(round_id=round_record.id, agent_id=alice.id)

    result = observe_session_message(
        alice,
        {"round_id": round_record.id, "session_id": session.id},
    )

    assert result["event"] == "decision_applied"
    assert "observe" in result["detail"].lower()

    candidate = db.get_meeting_response_candidate(round_id=round_record.id, agent_id=alice.id)
    assert candidate is not None
    assert candidate.status == "observed"
    refreshed = db.get_meeting_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


def test_channel_reserve_then_complete_advances_queue() -> None:
    host, alice = _two_agents()
    bob = db.create_agent("Bob", role="Eng", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[host.id, alice.id, bob.id],
        created_by=host.id,
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Who can take this?",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel.id,
        source_message_id=message.id,
    )
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=alice.id)
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=bob.id)

    trigger = {"round_id": round_record.id, "channel_id": channel.id, "content": "Who can take this?"}
    first, active = begin_channel_response(alice, trigger)
    second, second_active = begin_channel_response(bob, trigger)
    assert first["event"] == "decision_applied"
    assert active is True
    assert second_active is False
    alice_row = db.get_channel_response_candidate(round_id=round_record.id, agent_id=alice.id)
    assert alice_row is not None
    assert alice_row.status == "responding"
    bob_queued = db.get_channel_response_candidate(round_id=round_record.id, agent_id=bob.id)
    assert bob_queued is not None
    assert bob_queued.status == "queued"

    followups = finalize_channel_response(
        agent_id=alice.id,
        trigger={
            "round_id": round_record.id,
            "channel_id": channel.id,
            "content": "Who can take this?",
            "channel_name": "Ops",
        },
        responded=True,
    )
    assert len(followups) == 1
    assert followups[0]["trigger_type"] == "channel_response"
    assert followups[0]["agent_id"] == bob.id

    bob_row = db.get_channel_response_candidate(round_id=round_record.id, agent_id=bob.id)
    assert bob_row is not None
    assert bob_row.status == "responding"
    alice_done = db.get_channel_response_candidate(round_id=round_record.id, agent_id=alice.id)
    assert alice_done is not None
    assert alice_done.status == "responded"

    finalize_channel_response(
        agent_id=bob.id,
        trigger={"round_id": round_record.id, "channel_id": channel.id},
        responded=False,
    )
    refreshed = db.get_channel_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


def test_meeting_reserve_then_complete_advances_queue() -> None:
    host, alice = _two_agents()
    session = db.create_meeting_session("meetingRoom", title="Sync", created_by_agent_id=host.id)
    message = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content="Share status.",
        source_channel="chat",
    )
    round_record = db.create_meeting_response_round(
        session_id=session.id,
        source_message_id=message.id,
    )
    db.create_meeting_response_candidate(round_id=round_record.id, agent_id=alice.id)

    result, active = begin_session_response(
        alice,
        {"round_id": round_record.id, "session_id": session.id},
    )
    assert result["event"] == "decision_applied"
    assert active is True

    followups = finalize_session_response(
        agent_id=alice.id,
        trigger={"round_id": round_record.id, "session_id": session.id, "meeting_title": "Sync"},
        responded=True,
    )
    assert followups == []
    refreshed = db.get_meeting_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"
