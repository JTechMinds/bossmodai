import os
from pathlib import Path

import db

from core.agent_loop.meeting_orchestrator import maybe_start_meeting_kickoff_round


def setup_function() -> None:
    # Important: do not call db.reset_database() in tests; it wipes on-disk artifacts.
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()


def test_meeting_kickoff_requires_all_accounted_for() -> None:
    host = db.create_agent("Host", role="PM", desk_x=1, desk_y=1)
    alice = db.create_agent("Alice", role="Eng", desk_x=2, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=3, desk_y=1)

    session = db.create_meeting_session("meeting_room", title="Test Meeting", created_by_agent_id=host.id)
    packet = db.create_meeting_context_packet(session_id=session.id, summary="Test Topic", payload={"topic": "Test Topic"})
    db.upsert_meeting_session_meta(
        session_id=session.id,
        host_agent_id=host.id,
        meeting_mode="room",
        phase="assembling",
        context_packet_id=packet["id"],
    )
    db.upsert_meeting_session_participant(session_id=session.id, agent_id=host.id, state="arrived")
    db.upsert_meeting_session_participant(session_id=session.id, agent_id=alice.id, state="arrived")
    db.upsert_meeting_session_participant(session_id=session.id, agent_id=bob.id, state="invited")

    assert maybe_start_meeting_kickoff_round(session_id=session.id) == []

    db.update_meeting_session_participant_state(session_id=session.id, agent_id=bob.id, state="declined", reason="Busy")
    kickoff = maybe_start_meeting_kickoff_round(session_id=session.id)
    assert kickoff and kickoff[0]["trigger_type"] == "session_response"
