"""HA-TEST-P1-03 — one shared-channel round observe. No LLM."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.channel_rounds import observe_channel_message


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


def test_observe_channel_message_marks_candidate_and_completes_round() -> None:
    host = db.create_agent("Host", role="PM", desk_x=1, desk_y=1)
    alice = db.create_agent("Alice", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[host.id, alice.id],
        created_by=host.id,
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Please read this.",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(
        channel_id=channel.id,
        source_message_id=message.id,
    )
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=alice.id)

    result = observe_channel_message(
        alice,
        {"round_id": round_record.id, "channel_id": channel.id},
    )

    assert result["event"] == "decision_applied"
    assert "observe" in result["detail"].lower()

    candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=alice.id)
    assert candidate is not None
    assert candidate.status == "observed"

    refreshed = db.get_channel_response_round(round_record.id)
    assert refreshed is not None
    assert refreshed.status == "completed"
