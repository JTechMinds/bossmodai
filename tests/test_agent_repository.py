"""AgentRepository — keys only increase, and a delete takes everything the agent owns.

Pins the storage-key ledger (a key is never reissued, not after a delete, not
after delete-all, not when a stray folder is on disk), the delete cascade
(private rows gone, shared rows kept and detached, snapshot kept, files gone,
open work cancelled, agent-scoped CLI rules deleted rather than turned
global), the schema guard that keeps a new agent column from being
forgotten, the callers that must go through the repository, and the
one-time orphan cleanup.
"""

from __future__ import annotations

import inspect
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import db.agent_storage_identities as storage_identities
import db.agents as db_agents
import db.host_path_consent as host_path_consent
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from api.routes.agents import _broadcast_archive_side_effects
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions_meetings import _handle_attend_meeting
from core.agent_loop.activity_scheduler import plan_arrival_follow_up
from core.agent_repository import SHARED_OR_RETAINED, agent_repository
from core.bm_cli import filesystem
from core.floors import delete_floor
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from db.connection import _ensure_meeting_host_nullable, _seed_agent_storage_keys, get_connection
from db.floors import create_floor

_REPO_ROOT = Path(__file__).resolve().parents[1]


def setup_function() -> None:
    # A real reseed: it also clears the agents and standing-prefs roots, so
    # key numbering starts at agent_0001 in every test. Both roots and the
    # database are conftest's temp tree, never real data.
    db.reset_database()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _key(agent_id: str) -> str:
    agent = db.get_agent(agent_id)
    assert agent is not None
    return agent.storage_key


def _ledger() -> list[dict[str, Any]]:
    return db.query(
        "SELECT storage_index, storage_key, agent_id, retired_at FROM agent_storage_keys "
        "ORDER BY storage_index"
    )


def _sequence() -> int | None:
    row = db.query_one("SELECT seq FROM sqlite_sequence WHERE name = 'agent_storage_keys'")
    return None if row is None else int(row["seq"])


def _row(table: str, **values: Any) -> dict[str, Any]:
    columns = ", ".join(values)
    marks = ", ".join(f"${index}" for index in range(1, len(values) + 1))
    rows = db.query(
        f"INSERT INTO {table} ({columns}) VALUES ({marks}) RETURNING *",
        list(values.values()),
    )
    return rows[0]


def _count(sql: str, params: list[Any]) -> int:
    row = db.query_one(f"SELECT COUNT(*) AS n FROM {sql}", params)
    assert row is not None
    return int(row["n"])


# ─── Ledger ───


def test_a_deleted_newest_key_is_never_reissued() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    cy = agent_repository.create(name="Cy")
    assert [_key(a.id) for a in (ada, bob, cy)] == ["agent_0001", "agent_0002", "agent_0003"]
    agent_repository.delete(cy.id)
    dee = agent_repository.create(name="Dee")
    assert _key(dee.id) == "agent_0004"
    retired = {row["storage_key"]: row["retired_at"] for row in _ledger()}
    assert retired["agent_0003"] is not None
    assert retired["agent_0004"] is None


def test_keys_keep_rising_after_everyone_is_deleted() -> None:
    agent_repository.create(name="Ada")
    agent_repository.create(name="Bob")
    assert agent_repository.delete_all() == 2
    assert db.list_agents() == []
    cy = agent_repository.create(name="Cy")
    assert _key(cy.id) == "agent_0003"


def test_seeding_skips_every_key_found_on_disk() -> None:
    (filesystem.agents_artifact_root() / "agent_0020").mkdir()
    (filesystem.standing_prefs_root() / "agent_0017.json").write_text("{}", encoding="utf-8")
    # Names that are not keys never move the counter.
    (filesystem.agents_artifact_root() / "legacy-name").mkdir()
    (filesystem.standing_prefs_root() / "agent_0099.txt").write_text("", encoding="utf-8")
    _seed_agent_storage_keys(get_connection())
    ada = agent_repository.create(name="Ada")
    assert _key(ada.id) == "agent_0021"


def test_a_prefs_file_alone_is_enough_to_skip_its_key() -> None:
    (filesystem.standing_prefs_root() / "agent_0033.json").write_text("{}", encoding="utf-8")
    _seed_agent_storage_keys(get_connection())
    assert _key(agent_repository.create(name="Ada").id) == "agent_0034"


def test_seeding_copies_identities_once_and_twice_is_a_no_op() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    # A database from before the ledger: identities, no ledger rows.
    db.execute("DELETE FROM agent_storage_keys")
    db.execute("DELETE FROM sqlite_sequence WHERE name = 'agent_storage_keys'")
    _seed_agent_storage_keys(get_connection())
    first = _ledger()
    assert [(row["storage_key"], row["agent_id"]) for row in first] == [
        ("agent_0001", ada.id),
        ("agent_0002", bob.id),
    ]
    assert all(row["retired_at"] is None for row in first)
    assert _sequence() == 2
    _seed_agent_storage_keys(get_connection())
    assert _ledger() == first
    assert _sequence() == 2
    assert _key(agent_repository.create(name="Cy").id) == "agent_0003"


def test_seeding_never_lowers_the_counter() -> None:
    (filesystem.agents_artifact_root() / "agent_0009").mkdir()
    _seed_agent_storage_keys(get_connection())
    assert _sequence() == 9
    shutil.rmtree(filesystem.agents_artifact_root() / "agent_0009")
    _seed_agent_storage_keys(get_connection())
    assert _sequence() == 9


# ─── Delete cascade ───


def _seed_footprint(ada_id: str, bob_id: str) -> dict[str, Any]:
    """One row in every table an agent touches, plus files. Returns the ids to check."""
    seeded: dict[str, Any] = {}
    note = _row(
        "notifications", agent_id=ada_id, kind="receipt", content="Got it",
        source_channel="chat", policy="none",
    )
    _row("notification_links", notification_id=note["id"], target_kind="desk", target_path="/me/a.md")
    seeded["notification"] = note["id"]
    diag = _row(
        "diagnostics", agent_id=ada_id, agent_name="Ada", trigger_type="human_chat", trigger_data="{}",
    )
    _row("diagnostic_steps", diagnostic_id=diag["id"], step_index=0)
    seeded["diagnostic"] = diag["id"]
    db.create_message(from_agent=ada_id, to_agent=HUMAN_SENDER_ID, content="hi")
    db.create_message(from_agent=HUMAN_SENDER_ID, to_agent=ada_id, content="hello", message_type="human")
    db.create_message(from_agent=bob_id, to_agent=ada_id, content="psst")
    seeded["bob_to_human"] = db.create_message(
        from_agent=bob_id, to_agent=HUMAN_SENDER_ID, content="stays",
    ).id

    channel = db.create_channel(name="Desk", member_agent_ids=[ada_id, bob_id], created_by=ada_id)
    seeded["channel"] = channel.id
    line = _row(
        "channel_messages", channel_id=channel.id, author_type="agent", author_agent_id=ada_id,
        author_name="Ada", content="On it", source_channel="channel",
    )
    seeded["channel_message"] = line["id"]
    channel_round = _row(
        "channel_response_rounds", channel_id=channel.id, source_message_id=line["id"], status="active",
    )
    _row("channel_response_candidates", round_id=channel_round["id"], agent_id=ada_id, status="pending")
    _row("channel_host_state", channel_id=channel.id, work_agent_id=ada_id)

    session = _row(
        "meeting_sessions", room_id="room-1", title="Sync", status="active", created_by_agent_id=ada_id,
    )
    seeded["meeting"] = session["id"]
    said = _row(
        "meeting_session_messages", session_id=session["id"], author_type="agent",
        author_agent_id=ada_id, author_name="Ada", content="Agenda", source_channel="meeting",
    )
    seeded["meeting_message"] = said["id"]
    meeting_round = _row(
        "meeting_response_rounds", session_id=session["id"], source_message_id=said["id"], status="active",
    )
    _row("meeting_response_candidates", round_id=meeting_round["id"], agent_id=ada_id, status="pending")
    _row("meeting_session_participants", session_id=session["id"], agent_id=ada_id, state="invited")

    activity = _row("activities", agent_id=ada_id, kind="work", status="completed")
    _row("work_snapshots", activity_id=activity["id"], agent_id=ada_id)

    open_task = create_or_bind_task(
        title="Ship the report",
        description="Board work",
        project=None,
        assigned_to=ada_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=ada_id,
        created_by=ada_id,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task
    assert open_task is not None and open_task.status not in {"complete", "cancelled"}
    seeded["open_task"] = open_task.id
    done = _row(
        "tasks", title="Old work", status="complete", assigned_to=ada_id, owner_id=ada_id,
        created_by=ada_id, requester_id=ada_id,
    )
    seeded["done_task"] = done["id"]
    event = _row(
        "task_events", task_id=done["id"], author_type="agent", author_agent_id=ada_id,
        author_name="Ada", event_type="comment", content="Finished",
    )
    seeded["task_event"] = event["id"]

    consent = _row(
        "host_path_consent_requests", agent_id=ada_id, path="/tmp/x", grant_root="/tmp", reason="read",
    )
    _row("host_path_once_grants", agent_id=ada_id, root="/tmp", consent_id=consent["id"])
    approval = _row("cli_approval_requests", agent_id=ada_id, command="rm -rf build")
    _row(
        "bm_cli_events", agent_id=ada_id, command="ls", executor="virtual", policy_tier="always_allowed",
        decision="allowed", approval_request_id=approval["id"],
    )
    seeded["scoped_rule"] = _row(
        "cli_policy_rules", tier="always_allowed", pattern="deploy", agent_id=ada_id,
    )["id"]
    seeded["global_rule"] = _row("cli_policy_rules", tier="always_allowed", pattern="whoami")["id"]
    seeded["telegram"] = _row("telegram_sessions", telegram_user_id=4242, target_agent_id=ada_id)["id"]
    _row(
        "artifacts", agent_id=ada_id, virtual_path="/me/a.md", absolute_path="/tmp/ada-a.md",
        title="a", kind="file", category="output",
    )
    _row("agent_triggers", agent_id=ada_id, trigger_type="social", source_channel="chat", payload="{}")

    workspace, prefs = agent_repository.owned_paths(_key(ada_id))
    (workspace / "notes.md").write_text("mine", encoding="utf-8")
    prefs.write_text('{"schema_version": 1, "prefs": []}', encoding="utf-8")
    seeded["paths"] = (workspace, prefs)
    return seeded


def test_delete_removes_private_rows_and_detaches_shared_history() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    key = _key(ada.id)
    seeded = _seed_footprint(ada.id, bob.id)
    globals_before = _count("cli_policy_rules WHERE agent_id IS NULL", [])

    posted = agent_repository.delete(ada.id)

    # Private rows are gone.
    for table in (
        "agents WHERE id = $1",
        "agent_state WHERE agent_id = $1",
        "agent_cli_state WHERE agent_id = $1",
        "agent_prompt_history_policies WHERE agent_id = $1",
        "agent_storage_identities WHERE agent_id = $1",
        "notifications WHERE agent_id = $1",
        "diagnostics WHERE agent_id = $1",
        "messages WHERE from_agent = $1 OR to_agent = $1",
        "channel_members WHERE agent_id = $1",
        "channel_response_candidates WHERE agent_id = $1",
        "meeting_response_candidates WHERE agent_id = $1",
        "meeting_session_participants WHERE agent_id = $1",
        "activities WHERE agent_id = $1",
        "work_snapshots WHERE agent_id = $1",
        "host_path_consent_requests WHERE agent_id = $1",
        "host_path_once_grants WHERE agent_id = $1",
        "cli_approval_requests WHERE agent_id = $1",
        "bm_cli_events WHERE agent_id = $1",
        "cli_policy_rules WHERE agent_id = $1",
        "artifacts WHERE agent_id = $1",
        "agent_triggers WHERE agent_id = $1",
    ):
        assert _count(table, [ada.id]) == 0, table
    assert _count("notification_links WHERE notification_id = $1", [seeded["notification"]]) == 0
    assert _count("diagnostic_steps WHERE diagnostic_id = $1", [seeded["diagnostic"]]) == 0

    # Shared rows stay, with this agent detached.
    detached = {
        ("channel_messages", "author_agent_id", seeded["channel_message"]),
        ("meeting_session_messages", "author_agent_id", seeded["meeting_message"]),
        ("meeting_sessions", "created_by_agent_id", seeded["meeting"]),
        ("task_events", "author_agent_id", seeded["task_event"]),
        ("channels", "created_by", seeded["channel"]),
        ("telegram_sessions", "target_agent_id", seeded["telegram"]),
    }
    for table, column, row_id in detached:
        row = db.query_one(f"SELECT {column} AS value FROM {table} WHERE id = $1", [row_id])
        assert row is not None, table
        assert row["value"] is None, (table, column)
    host = db.query_one(
        "SELECT work_agent_id FROM channel_host_state WHERE channel_id = $1", [seeded["channel"]],
    )
    assert host is not None and host["work_agent_id"] is None
    for task_id in (seeded["open_task"], seeded["done_task"]):
        task = db.get_task(task_id)
        assert task is not None
        assert (task.owner_id, task.assigned_to, task.created_by) == (None, None, None)
    assert db.get_task(seeded["done_task"]).requester_id is None
    assert _count("messages WHERE id = $1", [seeded["bob_to_human"]]) == 1

    # The open task was cancelled with the reason and mirrored to its thread;
    # the closed one kept its status.
    assert db.get_task(seeded["open_task"]).status == "cancelled"
    assert db.get_task(seeded["done_task"]).status == "complete"
    assert any(
        "Owner Ada was deleted" in event.content
        for event in db.list_task_events(seeded["open_task"], limit=50)
    )
    lines = [message.content for message in db.list_channel_messages(seeded["channel"], limit=50)]
    assert any("Cancelled — Owner Ada was deleted" in line for line in lines), lines
    assert [item["channel_message"]["channel_id"] for item in posted] == [seeded["channel"]]

    # An agent-scoped rule is deleted, never promoted to a global one.
    assert _count("cli_policy_rules WHERE id = $1", [seeded["scoped_rule"]]) == 0
    assert _count("cli_policy_rules WHERE id = $1 AND agent_id IS NULL", [seeded["global_rule"]]) == 1
    assert _count("cli_policy_rules WHERE agent_id IS NULL", []) == globals_before

    # Recent keeps the snapshot, the ledger keeps the key, the files are gone.
    snapshot = db.query_one("SELECT deleted_at FROM agent_snapshots WHERE agent_id = $1", [ada.id])
    assert snapshot is not None and snapshot["deleted_at"] is not None
    ledger = db.query_one("SELECT retired_at FROM agent_storage_keys WHERE storage_key = $1", [key])
    assert ledger is not None and ledger["retired_at"] is not None
    workspace, prefs = seeded["paths"]
    assert not workspace.exists()
    assert not prefs.exists()
    # Bob is untouched.
    assert db.get_agent(bob.id) is not None


def test_deleting_a_missing_agent_is_a_lookup_error() -> None:
    with pytest.raises(LookupError):
        agent_repository.delete("no-such-agent")


def test_a_failed_file_removal_names_the_path_after_the_rows_are_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada = agent_repository.create(name="Ada")
    key = _key(ada.id)
    workspace = agent_repository.owned_paths(key)[0]

    def _refuse(path: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr("core.agent_repository.shutil.rmtree", _refuse)
    with pytest.raises(OSError) as excinfo:
        agent_repository.delete(ada.id)
    assert str(workspace) in str(excinfo.value)
    assert db.get_agent(ada.id) is None
    ledger = db.query_one("SELECT retired_at FROM agent_storage_keys WHERE storage_key = $1", [key])
    assert ledger is not None and ledger["retired_at"] is not None


def test_deleting_a_meeting_host_keeps_the_meeting_with_no_host() -> None:
    ada = agent_repository.create(name="Ada")
    session = _row("meeting_sessions", room_id="room-1", title="Sync", status="active")
    _row(
        "meeting_session_meta", session_id=session["id"], host_agent_id=ada.id,
        meeting_mode="room", phase="assembling",
    )

    agent_repository.delete(ada.id)

    assert db.get_agent(ada.id) is None
    meta = db.get_meeting_session_meta(session["id"])
    assert meta is not None
    assert meta["host_agent_id"] is None
    assert meta["phase"] == "canceled"


# ─── Deleting a meeting host ends their meetings ───


def _hosted_meeting(host_id: str, *, phase: str, status: str = "active") -> dict[str, Any]:
    """One meeting ``host_id`` hosts, with an open round and a queued turn for it."""
    session = _row("meeting_sessions", room_id="meeting_room", title="Sync", status=status)
    _row(
        "meeting_session_meta", session_id=session["id"], host_agent_id=host_id,
        meeting_mode="room", phase=phase,
    )
    said = db.create_meeting_session_message(
        session_id=session["id"], author_type="system", author_name="BossMod",
        content="MEETING START", source_channel="meeting",
    )
    meeting_round = db.create_meeting_response_round(session_id=session["id"], source_message_id=said.id)
    return {"session": session, "round": meeting_round}


def _queue_round_turn(agent_id: str, round_id: str, session_id: str) -> None:
    db.create_agent_trigger(
        agent_id=agent_id, trigger_type="session_response", source_channel="chat",
        payload={"content": "MEETING START", "session_id": session_id, "round_id": round_id},
    )


def _round_triggers(round_id: str) -> list[dict[str, Any]]:
    return [
        row for row in db.query("SELECT payload FROM agent_triggers WHERE status = 'queued'")
        if round_id in str(row["payload"])
    ]


def _meeting_lines(session_id: str) -> list[str]:
    return [item.content for item in db.list_meeting_session_messages(session_id, limit=50)]


def _in_meeting_from_live_work(agent_id: str, session_id: str) -> tuple[str, str, str]:
    """Put an agent in a meeting the way attendMeeting does: its live work paused under it.

    Returns:
        ``(task id, work activity id, meeting activity id)``.
    """
    task = _row("tasks", title="Ship it", status="active", assigned_to=agent_id, owner_id=agent_id)
    work = db.create_runtime_activity(agent_id, "work", task_id=task["id"], title="Ship it")
    db.update_activity(work.id, status="paused")
    meeting = activity_runtime.start_meeting_activity(
        agent_id, title="Sync", parent_activity_id=work.id, metadata={"session_id": session_id},
    )
    return task["id"], work.id, meeting.id


def test_deleting_the_host_of_an_assembling_meeting_cancels_it() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    meeting = _hosted_meeting(ada.id, phase="assembling")
    session_id, round_id = meeting["session"]["id"], meeting["round"].id
    _queue_round_turn(bob.id, round_id, session_id)

    agent_repository.delete(ada.id)

    meta = db.get_meeting_session_meta(session_id)
    assert meta is not None and meta["phase"] == "canceled" and meta["host_agent_id"] is None
    session = db.get_meeting_session(session_id)
    assert session is not None and session.status == "ended" and session.ended_at is not None
    assert _meeting_lines(session_id)[-1] == "Meeting ended: host Ada was deleted."
    assert db.get_meeting_response_round(round_id).status == "completed"
    assert _round_triggers(round_id) == []


def test_deleting_the_host_of_an_active_meeting_ends_it() -> None:
    ada = agent_repository.create(name="Ada")
    meeting = _hosted_meeting(ada.id, phase="active")
    session_id = meeting["session"]["id"]

    agent_repository.delete(ada.id)

    meta = db.get_meeting_session_meta(session_id)
    assert meta is not None and meta["phase"] == "ended"
    assert db.get_meeting_session(session_id).status == "ended"
    assert _meeting_lines(session_id)[-1] == "Meeting ended: host Ada was deleted."


def test_other_participants_leave_the_ended_meeting_and_resume_their_work() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    session_id = _hosted_meeting(ada.id, phase="active")["session"]["id"]
    task_id, work_id, meeting_id = _in_meeting_from_live_work(bob.id, session_id)

    agent_repository.delete(ada.id)

    assert db.get_activity(meeting_id).status == "completed"
    assert db.get_activity(work_id).status == "active"
    assert activity_runtime.get_active_activity(bob.id).id == work_id
    assert db.has_open_trigger_matching(bob.id, trigger_types=["activity_resumed"], task_id=task_id)


def test_deleting_a_host_leaves_its_finished_meetings_untouched() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    meeting = _hosted_meeting(ada.id, phase="ended", status="ended")
    session_id, round_id = meeting["session"]["id"], meeting["round"].id
    _queue_round_turn(bob.id, round_id, session_id)
    _task_id, _work_id, meeting_id = _in_meeting_from_live_work(bob.id, session_id)
    lines_before = _meeting_lines(session_id)

    agent_repository.delete(ada.id)

    meta = db.get_meeting_session_meta(session_id)
    assert meta is not None and meta["phase"] == "ended" and meta["host_agent_id"] is None
    assert _meeting_lines(session_id) == lines_before
    assert db.get_meeting_response_round(round_id).status == "active"
    assert len(_round_triggers(round_id)) == 1
    assert db.get_activity(meeting_id).status == "active"


def test_the_orphan_purge_ends_an_assembling_meeting_with_no_host_once() -> None:
    bob = agent_repository.create(name="Bob")
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        meeting = _hosted_meeting(_GHOST, phase="assembling")
    finally:
        db.execute("PRAGMA foreign_keys = ON")
    session_id, round_id = meeting["session"]["id"], meeting["round"].id
    _queue_round_turn(bob.id, round_id, session_id)

    counts = agent_repository.purge_orphans()

    assert counts["meetings_ended"] == 1
    meta = db.get_meeting_session_meta(session_id)
    assert meta is not None and meta["phase"] == "canceled" and meta["host_agent_id"] is None
    assert db.get_meeting_session(session_id).status == "ended"
    assert _meeting_lines(session_id)[-1] == "Meeting ended: host was deleted."
    assert _round_triggers(round_id) == []
    lines_after = _meeting_lines(session_id)

    again = agent_repository.purge_orphans()
    assert all(value == 0 for value in again.values())
    assert _meeting_lines(session_id) == lines_after


# ─── A meeting that ended while an attendee was away stays over ───


def _meeting_session_count() -> int:
    return _count("meeting_sessions", [])


@pytest.mark.parametrize("how", ["session_ended", "phase_canceled"])
async def test_attending_a_meeting_that_ended_meanwhile_goes_back_to_work(how: str) -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    session_id = _hosted_meeting(ada.id, phase="assembling")["session"]["id"]
    _task_id, work_id, meeting_id = _in_meeting_from_live_work(bob.id, session_id)
    if how == "session_ended":
        db.end_meeting_session(session_id)
    else:
        db.update_meeting_session_meta(session_id, phase="canceled")
    db.update_agent_state(bob.id, x=18, y=3)  # inside the Meeting Room
    state = db.get_agent_state(bob.id)
    sessions_before, lines_before = _meeting_session_count(), _meeting_lines(session_id)

    result = await _handle_attend_meeting(bob, state, {"action": "attendMeeting"})

    assert result["event"] == "world_feedback"
    assert result["detail"] == "That meeting has ended. Back to your previous work."
    assert db.get_activity(meeting_id).status == "completed"
    assert activity_runtime.get_active_activity(bob.id).id == work_id
    assert _meeting_session_count() == sessions_before
    assert _meeting_lines(session_id) == lines_before


def test_arriving_at_a_meeting_that_ended_on_the_way_resumes_the_work() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    session_id = _hosted_meeting(ada.id, phase="active")["session"]["id"]
    task_id, work_id, meeting_id = _in_meeting_from_live_work(bob.id, session_id)
    activity_runtime.start_movement_activity(bob.id, destination="Meeting Room")

    agent_repository.delete(ada.id)
    # Still walking: the delete does not touch a meeting paused under the walk.
    assert db.get_activity(meeting_id).status == "paused"

    resumed = activity_runtime.resolve_arrival(bob.id)

    assert resumed is not None and resumed.id == work_id
    assert db.get_activity(meeting_id).status == "completed"
    assert activity_runtime.get_active_activity(bob.id).id == work_id
    follow_up = plan_arrival_follow_up(bob.id, resumed, "Meeting Room")
    assert [item["task_id"] for item in follow_up] == [task_id]


def test_ending_a_meeting_drops_only_its_queued_triggers() -> None:
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    session_id = _hosted_meeting(ada.id, phase="assembling")["session"]["id"]
    other_id = _hosted_meeting(bob.id, phase="assembling")["session"]["id"]
    invite = db.create_agent_trigger(
        bob.id, "meeting_invite", "chat", {"content": "Join", "session_id": session_id},
    )
    resume = db.create_agent_trigger(
        bob.id, "activity_resumed", "chat", {"content": "Continue", "session_id": session_id},
    )
    claimed = db.create_agent_trigger(
        bob.id, "activity_resumed", "chat", {"content": "Running", "session_id": session_id},
    )
    db.execute("UPDATE agent_triggers SET status = 'claimed' WHERE id = $1", [claimed.id])
    other = db.create_agent_trigger(
        bob.id, "meeting_invite", "chat", {"content": "Other", "session_id": other_id},
    )

    agent_repository.delete(ada.id)

    assert db.get_agent_trigger(invite.id) is None
    assert db.get_agent_trigger(resume.id) is None
    assert db.get_agent_trigger(claimed.id).status == "claimed"
    assert db.get_agent_trigger(other.id).status == "queued"


class _Manager:
    """Records every broadcast the API makes, by method name and arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, {"args": args, **kwargs}))

        return _record

    def named(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, kwargs in self.calls if called == name]


def test_the_api_delete_paints_the_meeting_ended_line_to_every_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.routes.agents.runtime_services", _Services())
    fake = _Manager()
    monkeypatch.setattr("api.routes.agents.manager", fake)
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    cara = agent_repository.create(name="Cara")
    session_id = _hosted_meeting(ada.id, phase="active")["session"]["id"]
    for agent_id, state in [(ada.id, "arrived"), (bob.id, "arrived"), (cara.id, "invited")]:
        db.upsert_meeting_session_participant(session_id=session_id, agent_id=agent_id, state=state)

    client, headers = _client()
    response = client.delete(f"/api/agents/{ada.id}", headers=headers)

    assert response.status_code == 204, response.text
    painted = fake.named("broadcast_meeting_message")
    assert sorted(item["agent_id"] for item in painted) == sorted([bob.id, cara.id])
    for item in painted:
        assert item["session_id"] == session_id
        assert item["content"] == "Meeting ended: host Ada was deleted."
        assert item["author_type"] == "system" and item["author_name"] == "BossMod"
        assert item["message_id"] and item["created_at"] is not None


async def test_the_archive_side_effects_paint_no_meeting_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Manager()
    monkeypatch.setattr("api.routes.agents.manager", fake)
    lines: list[dict[str, object]] = [
        {"channel_message": {"channel_id": "c1", "content": "Cancelled", "message_id": "m1"}},
        {"chat_message": {"agent_id": "a1", "content": "Cancelled", "message_id": "m2"}},
    ]

    await _broadcast_archive_side_effects(lines)

    assert [name for name, _kwargs in fake.calls] == ["broadcast_channel_message", "broadcast_chat_message"]


# ─── Ending a meeting wakes the worker for resumed work ───


def _wake_commands() -> list[str]:
    return [
        command.id for command in db.list_queued_runtime_commands()
        if command.command_type == "wake_dispatcher"
    ]


def test_ending_a_meeting_wakes_the_worker_once_for_resumed_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOSSMOD_RUNTIME_WORKER", raising=False)
    db.mark_runtime_worker_running(pid=1)
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    cara = agent_repository.create(name="Cara")
    session_id = _hosted_meeting(ada.id, phase="active")["session"]["id"]
    _in_meeting_from_live_work(bob.id, session_id)
    _in_meeting_from_live_work(cara.id, session_id)

    agent_repository.delete(ada.id)

    assert len(_wake_commands()) == 1


def test_ending_a_meeting_without_a_resume_does_not_wake_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOSSMOD_RUNTIME_WORKER", raising=False)
    db.mark_runtime_worker_running(pid=1)
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    session_id = _hosted_meeting(ada.id, phase="active")["session"]["id"]
    # In the meeting with no work paused under it: nothing to resume.
    meeting = activity_runtime.start_meeting_activity(bob.id, title="Sync", metadata={"session_id": session_id})

    agent_repository.delete(ada.id)

    assert db.get_activity(meeting.id).status == "completed"
    assert _wake_commands() == []


def _meeting_meta_sql() -> str:
    row = db.query_one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'meeting_session_meta'")
    assert row is not None
    return str(row["sql"])


def _meeting_host_notnull() -> int:
    for row in db.query("PRAGMA table_info(meeting_session_meta)"):
        if row["name"] == "host_agent_id":
            return int(row["notnull"])
    raise AssertionError("meeting_session_meta has no host_agent_id column")


def test_the_meeting_host_migration_rebuilds_once_and_keeps_rows() -> None:
    # A fresh schema is already nullable: the migration changes nothing.
    assert _meeting_host_notnull() == 0
    fresh_sql = _meeting_meta_sql()
    _ensure_meeting_host_nullable(get_connection())
    assert _meeting_meta_sql() == fresh_sql

    # A database from before the change: host NOT NULL, one meeting in it.
    ada = agent_repository.create(name="Ada")
    session = _row("meeting_sessions", room_id="room-1", title="Sync", status="active")
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("DROP TABLE meeting_session_meta")
        db.execute(
            """
            CREATE TABLE meeting_session_meta (
                session_id        VARCHAR PRIMARY KEY REFERENCES meeting_sessions(id),
                host_agent_id     VARCHAR NOT NULL REFERENCES agents(id),
                meeting_mode      VARCHAR NOT NULL
                                      CHECK (meeting_mode IN ('room', 'remote')),
                phase             VARCHAR NOT NULL
                                      CHECK (phase IN ('assembling', 'active', 'ended', 'canceled')),
                context_packet_id VARCHAR REFERENCES meeting_context_packets(id),
                kickoff_round_id  VARCHAR REFERENCES meeting_response_rounds(id),
                created_at        TIMESTAMP DEFAULT current_timestamp,
                updated_at        TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
    finally:
        db.execute("PRAGMA foreign_keys = ON")
    _row(
        "meeting_session_meta", session_id=session["id"], host_agent_id=ada.id,
        meeting_mode="room", phase="active",
    )
    assert _meeting_host_notnull() == 1

    _ensure_meeting_host_nullable(get_connection())
    assert _meeting_host_notnull() == 0
    meta = db.get_meeting_session_meta(session["id"])
    assert meta is not None
    assert (meta["host_agent_id"], meta["meeting_mode"], meta["phase"]) == (ada.id, "room", "active")

    rebuilt_sql = _meeting_meta_sql()
    _ensure_meeting_host_nullable(get_connection())
    assert _meeting_meta_sql() == rebuilt_sql


# ─── Schema guard ───

_AGENT_COLUMN_RE = re.compile(r"^(agent_id|.+_agent_id|from_agent|to_agent)$")


def test_every_agent_column_is_handled_by_the_delete_or_declared_retained() -> None:
    tables = [row["name"] for row in db.query("SHOW TABLES")]
    agent_columns = {
        (table, row["name"])
        for table in tables
        for row in db.query(f"PRAGMA table_info({table})")
        if _AGENT_COLUMN_RE.match(row["name"])
    }
    assert ("messages", "from_agent") in agent_columns, "introspection found nothing"
    source = "\n".join(
        inspect.getsource(function)
        for function in (
            db_agents.delete_agent_rows,
            host_path_consent.delete_agent_consent,
            storage_identities.retire_agent_storage_key,
            storage_identities.delete_agent_storage_identity,
        )
    )
    unhandled = sorted(
        (table, column)
        for table, column in agent_columns - SHARED_OR_RETAINED
        if not re.search(rf"(?:DELETE FROM|UPDATE)\s+{table}\b[^\n]*\b{column}\b", source)
    )
    assert unhandled == [], (
        "Each agent column must be deleted or detached in delete_agent_rows, "
        "or listed in core.agent_repository.SHARED_OR_RETAINED"
    )
    stale = sorted(SHARED_OR_RETAINED - agent_columns)
    assert stale == [], "SHARED_OR_RETAINED names a column the schema no longer has"


# ─── Callers ───


class _Services:
    """Records runtime resets and whether the agent still existed at the time."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, bool]] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.events.append(("reset", agent_id, db.get_agent(agent_id) is not None))


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_the_api_delete_resets_the_runtime_before_the_repository_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.agents.runtime_services", services)
    ada = agent_repository.create(name="Ada")
    seen: list[str] = []
    real_delete = agent_repository.delete

    def _delete(agent_id: str) -> list[dict[str, Any]]:
        seen.append(agent_id)
        return real_delete(agent_id)

    monkeypatch.setattr(agent_repository, "delete", _delete)
    client, headers = _client()
    response = client.delete(f"/api/agents/{ada.id}", headers=headers)
    assert response.status_code == 204, response.text
    # The reset ran while the agent still existed, then the repository deleted it.
    assert services.events == [("reset", ada.id, True)]
    assert seen == [ada.id]
    assert db.get_agent(ada.id) is None
    assert client.delete(f"/api/agents/{ada.id}", headers=headers).status_code == 404


def test_the_api_delete_all_resets_every_runtime_then_uses_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.agents.runtime_services", services)
    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    calls: list[str] = []
    real_delete_all = agent_repository.delete_all

    def _delete_all() -> int:
        calls.append("delete_all")
        return real_delete_all()

    monkeypatch.setattr(agent_repository, "delete_all", _delete_all)
    client, headers = _client()
    response = client.delete("/api/agents", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "deleted": 2}
    assert sorted(services.events) == sorted([("reset", ada.id, True), ("reset", bob.id, True)])
    assert calls == ["delete_all"]
    assert db.list_agents() == []


async def test_the_floor_delete_deletes_its_agents_through_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = create_floor("Finance")
    ada = agent_repository.create(name="Ada", floor_id=finance.id)
    seen: list[str] = []
    real_delete = agent_repository.delete

    def _delete(agent_id: str) -> list[dict[str, Any]]:
        seen.append(agent_id)
        return real_delete(agent_id)

    monkeypatch.setattr(agent_repository, "delete", _delete)
    services = _Services()
    result = await delete_floor(finance.id, occupants="delete", services=services)
    assert result.agents_deleted == [ada.id]
    assert services.events == [("reset", ada.id, True)]
    assert seen == [ada.id]


_CALLS_DELETE_AGENT_ROWS = re.compile(r"(?<!def )\bdelete_agent_rows\s*\(")


def test_nothing_outside_the_repository_calls_delete_agent_rows() -> None:
    # A call, not a mention: docstrings and the db package export may name it.
    skipped = {"tests", ".venv", ".kilo", ".git", "node_modules"}
    callers = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in _REPO_ROOT.rglob("*.py")
        if not skipped & set(path.relative_to(_REPO_ROOT).parts)
        and _CALLS_DELETE_AGENT_ROWS.search(path.read_text(encoding="utf-8"))
    )
    assert callers == ["core/agent_repository.py"]


# ─── Orphan purge ───

_GHOST = "ghost-agent-id"


def test_orphan_purge_applies_the_delete_rules_once(caplog: pytest.LogCaptureFixture) -> None:
    live = agent_repository.create(name="Live")
    channel = db.create_channel(name="Room", member_agent_ids=[live.id], created_by=HUMAN_SENDER_ID)
    session = _row("meeting_sessions", room_id="room-1", title="Sync", status="active")
    said = _row(
        "meeting_session_messages", session_id=session["id"], author_type="human",
        author_name="Human Operator", content="Hi", source_channel="meeting",
    )
    meeting_round = _row(
        "meeting_response_rounds", session_id=session["id"], source_message_id=said["id"], status="active",
    )
    line = _row(
        "channel_messages", channel_id=channel.id, author_type="human",
        author_name="Human Operator", content="Go", source_channel="channel",
    )
    channel_round = _row(
        "channel_response_rounds", channel_id=channel.id, source_message_id=line["id"], status="active",
    )
    # What an older delete left behind. Foreign keys are switched off only to
    # write rows the app could have written before they were enforced.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        diag = _row(
            "diagnostics", agent_id=_GHOST, agent_name="Ghost", trigger_type="social", trigger_data="{}",
        )
        _row("diagnostic_steps", diagnostic_id=diag["id"], step_index=0)
        db.create_message(from_agent=_GHOST, to_agent=HUMAN_SENDER_ID, content="old")
        db.create_message(from_agent=HUMAN_SENDER_ID, to_agent=_GHOST, content="old", message_type="human")
        kept_dm = db.create_message(from_agent=HUMAN_SENDER_ID, to_agent=live.id, content="kept").id
        kept_broadcast = db.create_message(from_agent=live.id, to_agent=None, content="kept").id
        _row("meeting_session_participants", session_id=session["id"], agent_id=_GHOST, state="invited")
        _row("channel_members", channel_id=channel.id, agent_id=_GHOST)
        _row("channel_response_candidates", round_id=channel_round["id"], agent_id=_GHOST, status="pending")
        _row("meeting_response_candidates", round_id=meeting_round["id"], agent_id=_GHOST, status="pending")
        open_task = _row(
            "tasks", title="Stranded", status="active", assigned_to=_GHOST, owner_id=_GHOST,
            created_by=_GHOST, requester_id=HUMAN_SENDER_ID,
        )
        closed_task = _row(
            "tasks", title="History", status="complete", created_by=_GHOST, requester_id=_GHOST,
            assigned_to=live.id, owner_id=live.id,
        )
        human_task = _row(
            "tasks", title="Live work", status="active", assigned_to=live.id, owner_id=live.id,
            created_by=HUMAN_SENDER_ID, requester_id=HUMAN_SENDER_ID,
        )
        ghost_thread = _row("channels", name="Old", kind="manual", status="active", created_by=_GHOST)
        _row("channel_host_state", channel_id=channel.id, work_agent_id=_GHOST)
        _row(
            "meeting_session_meta", session_id=session["id"], host_agent_id=_GHOST,
            meeting_mode="room", phase="active",
        )
    finally:
        db.execute("PRAGMA foreign_keys = ON")

    caplog.set_level(logging.WARNING, logger="core.agent_repository")
    counts = agent_repository.purge_orphans()
    assert counts == {
        "tasks_cancelled": 1,
        "meetings_ended": 1,
        "diagnostic_steps": 1,
        "diagnostics": 1,
        "messages": 2,
        "meeting_session_participants": 1,
        "channel_members": 1,
        "channel_response_candidates": 1,
        "meeting_response_candidates": 1,
        "tasks.assigned_to": 1,
        "tasks.created_by": 2,
        "tasks.owner_id": 1,
        "tasks.requester_id": 1,
        "channels.created_by": 1,
        "channel_host_state.work_agent_id": 1,
        "meeting_session_meta.host_agent_id": 1,
    }
    assert len([r for r in caplog.records if r.name == "core.agent_repository"]) == 1

    stranded = db.get_task(open_task["id"])
    assert stranded.status == "cancelled"
    assert (stranded.assigned_to, stranded.owner_id, stranded.created_by) == (None, None, None)
    assert stranded.requester_id == HUMAN_SENDER_ID
    assert any(
        "Owner was deleted" in event.content for event in db.list_task_events(open_task["id"], limit=50)
    )
    history = db.get_task(closed_task["id"])
    assert history.status == "complete"
    assert (history.created_by, history.requester_id, history.assigned_to) == (None, None, live.id)
    untouched = db.get_task(human_task["id"])
    assert untouched.status == "active"
    assert untouched.created_by == HUMAN_SENDER_ID
    assert db.get_channel(channel.id).created_by == HUMAN_SENDER_ID
    assert db.get_channel(ghost_thread["id"]).created_by is None
    assert _count("messages WHERE id IN ($1, $2)", [kept_dm, kept_broadcast]) == 2
    assert _count("channel_members WHERE agent_id = $1", [live.id]) == 1
    hosted = db.get_meeting_session_meta(session["id"])
    assert hosted is not None and hosted["host_agent_id"] is None
    assert hosted["phase"] == "ended"

    caplog.clear()
    again = agent_repository.purge_orphans()
    assert set(again) == set(counts)
    assert all(value == 0 for value in again.values())
    assert [r for r in caplog.records if r.name == "core.agent_repository"] == []


# ─── Where the orphan purge runs ───


def test_init_db_does_not_run_the_orphan_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(agent_repository, "purge_orphans", lambda: calls.append("purge") or {})
    db.init_db()
    assert calls == []


class _LifespanServices:
    """Stands in for the runtime gateway so the lifespan never spawns a worker."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def set_event_sink(self, sink: object) -> None:
        pass

    def set_telegram_bridge(self, bridge: object) -> None:
        pass

    async def start(self) -> None:
        self.events.append("runtime_start")

    async def stop(self) -> None:
        self.events.append("runtime_stop")


async def test_the_app_lifespan_purges_once_after_init_and_before_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    from integrations import telegram

    events: list[str] = []
    real_init_db = main.init_db

    def _init_db() -> None:
        events.append("init_db")
        real_init_db()

    async def _no_telegram(**_kwargs: object) -> None:
        return None

    async def _no_telegram_stop() -> None:
        return None

    monkeypatch.setattr(main, "init_db", _init_db)
    monkeypatch.setattr(main, "runtime_services", _LifespanServices(events))
    monkeypatch.setattr(agent_repository, "purge_orphans", lambda: events.append("purge") or {})
    monkeypatch.setattr(telegram, "start", _no_telegram)
    monkeypatch.setattr(telegram, "stop", _no_telegram_stop)

    async with main.lifespan(main.app):
        assert events == ["init_db", "purge", "runtime_start"]
    assert events.count("purge") == 1
