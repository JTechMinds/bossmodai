"""Rewinding a thread: the cut, the stopped turns, the closed rounds and the route.

Each test runs on a fresh temp database (conftest.py points BOSSMOD_DB_PATH at
one). The runtime is a fake that records calls, so no worker is started.
Unlike a DM rewind, a thread rewind queues nothing again on success: the
tests assert that the fake's ``enqueued`` stays empty unless a cancel or a
cut failed.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.conversation_notes import ThreadScope, load_thread_window
from core.models.channel import ChannelArchivedError
from core.thread_rewind import rewind_thread
from db import attachments as db_att
from db import channel_idle_checks as idle_db
from db.crud import execute, query


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


class _Services:
    """Records runtime calls. ``on_reset`` stands in for the worker's cancel."""

    def __init__(self, on_reset: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.resets: list[str] = []
        self.enqueued: list[dict[str, Any]] = []
        self._on_reset = on_reset

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)
        if self._on_reset is not None:
            await self._on_reset(agent_id)

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.enqueued.append(kwargs)


class _Manager:
    """Records the broadcasts the route sends."""

    def __init__(self) -> None:
        self.channel_resets: list[str] = []
        self.channel_updates: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.world_states = 0

    async def broadcast_channel_reset(self, channel_id: str) -> None:
        self.channel_resets.append(channel_id)

    async def broadcast_channel_updated(self, channel: dict[str, Any]) -> None:
        self.channel_updates.append(channel)

    async def broadcast_activity(self, **kwargs: Any) -> None:
        self.activities.append(kwargs)

    async def broadcast_world_state(self) -> None:
        self.world_states += 1


def _team():
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bo = db.create_agent("Bo", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(name="Standup", member_agent_ids=[ada.id, bo.id], created_by=ada.id)
    return ada, bo, channel


def _line(channel_id: str, content: str, *, agent=None, system: bool = False, at: str | None = None, **extra):
    """One thread row (the operator's unless ``agent`` or ``system``), optionally stamped."""
    if system:
        author = {"author_type": "system", "author_name": "BossMod"}
    elif agent is not None:
        author = {"author_type": "agent", "author_name": agent.name, "author_agent_id": agent.id}
    else:
        author = {"author_type": "human", "author_name": "Human Operator"}
    msg = db.create_channel_message(
        channel_id=channel_id, content=content, source_channel="channel", **author, **extra,
    )
    if at is not None:
        execute("UPDATE channel_messages SET created_at = $1 WHERE id = $2", [at, msg.id])
    return msg


def _wake(
    agent_id: str,
    channel_id: str,
    *,
    trigger_type: str = "channel_message",
    claimed: bool = False,
    round_id: str = "",
    source_message_id: str = "",
):
    trigger = db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type=trigger_type,
        source_channel="channel",
        payload={"channel_id": channel_id, "round_id": round_id, "source_message_id": source_message_id},
    )
    if claimed:
        assert db.claim_trigger(trigger.id) is not None
    return trigger


def _transcript(channel_id: str) -> list[str]:
    return [
        row["content"]
        for row in query(
            "SELECT content FROM channel_messages WHERE channel_id = $1 ORDER BY julianday(created_at), rowid",
            [channel_id],
        )
    ]


def _open_triggers() -> dict[str, str]:
    return {
        str(row["id"]): str(row["status"])
        for row in query("SELECT id, status FROM agent_triggers WHERE status IN ('queued', 'claimed')")
    }


def _round(channel_id: str, source_message_id: str, statuses: dict[str, str]):
    """An active round on one source row, with one candidate per agent in ``statuses``."""
    record = db.create_channel_response_round(channel_id=channel_id, source_message_id=source_message_id)
    for agent_id, status in statuses.items():
        db.create_channel_response_candidate(round_id=record.id, agent_id=agent_id)
        db.update_channel_response_candidate(round_id=record.id, agent_id=agent_id, status=status)
    return record


# ─── The cut ───


async def test_removes_the_cut_row_and_everything_after_it() -> None:
    ada, bo, channel = _team()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[ada.id], created_by=ada.id)
    _line(channel.id, "one", at="2026-10-08 10:00:00")
    _line(channel.id, "two", agent=ada, at="2026-10-08 10:00:01")
    cut = _line(channel.id, "three", at="2026-10-08 10:00:02")
    _line(channel.id, "Round 2", system=True, notification_kind="channel_round_marker", at="2026-10-08 10:00:03")
    _line(channel.id, "Approve?", agent=bo, consent_id="consent-1", at="2026-10-08 10:00:04")
    _line(other.id, "elsewhere", at="2026-10-08 10:00:05")

    services = _Services()
    result = await rewind_thread(channel.id, cut.id, services=services)

    assert _transcript(channel.id) == ["one", "two"]
    assert _transcript(other.id) == ["elsewhere"]
    assert len(result.removed_message_ids) == 3
    assert cut.id in result.removed_message_ids
    assert result.stopped_agent_ids == [] and result.dropped_wakes == 0
    assert services.resets == [] and services.enqueued == []


async def test_same_second_rows_are_cut_in_insertion_order() -> None:
    ada, _bo, channel = _team()
    second = "2026-10-08 10:00:00"
    _line(channel.id, "first", at=second)
    cut = _line(channel.id, "second", agent=ada, at=second)
    _line(channel.id, "third", at=second)

    assert _transcript(channel.id) == ["first", "second", "third"]
    await rewind_thread(channel.id, cut.id, services=_Services())
    assert _transcript(channel.id) == ["first"]


async def test_a_message_from_another_thread_changes_nothing() -> None:
    ada, bo, channel = _team()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[ada.id, bo.id], created_by=ada.id)
    mine = _line(channel.id, "mine")
    theirs = _line(other.id, "theirs")
    queued = _wake(bo.id, channel.id)
    claimed = _wake(ada.id, channel.id, claimed=True)
    services = _Services()

    with pytest.raises(LookupError):
        await rewind_thread(channel.id, theirs.id, services=services)
    with pytest.raises(LookupError):
        await rewind_thread(channel.id, "no-such-message", services=services)
    with pytest.raises(LookupError):
        await rewind_thread("no-such-thread", mine.id, services=services)

    assert _transcript(channel.id) == ["mine"]
    assert _transcript(other.id) == ["theirs"]
    assert _open_triggers() == {queued.id: "queued", claimed.id: "claimed"}
    assert services.resets == [] and services.enqueued == []


async def test_an_archived_thread_raises_and_changes_nothing() -> None:
    ada, bo, channel = _team()
    kept = _line(channel.id, "kept")
    queued = _wake(bo.id, channel.id)
    claimed = _wake(ada.id, channel.id, claimed=True)
    assert db.archive_channel(channel.id).status == "archived"
    services = _Services()

    with pytest.raises(ChannelArchivedError):
        await rewind_thread(channel.id, kept.id, services=services)

    assert _transcript(channel.id) == ["kept"]
    assert _open_triggers() == {queued.id: "queued", claimed.id: "claimed"}
    assert services.resets == [] and services.enqueued == []


# ─── Stopping turns ───


async def test_a_claimed_thread_wake_is_stopped_and_its_row_removed() -> None:
    ada, _bo, channel = _team()
    question = _line(channel.id, "question")
    _wake(ada.id, channel.id, claimed=True, source_message_id=question.id)
    services = _Services()

    result = await rewind_thread(channel.id, question.id, services=services)

    assert services.resets == [ada.id]
    assert result.stopped_agent_ids == [ada.id]
    assert result.dropped_wakes == 1
    assert _open_triggers() == {}
    assert _transcript(channel.id) == []
    # D3: nobody is woken again.
    assert services.enqueued == []


async def test_claimed_turns_that_are_not_this_threads_conversation_are_left_alone() -> None:
    ada, bo, channel = _team()
    cy = db.create_agent("Cy", role="Eng", desk_x=3, desk_y=1)
    other = db.create_channel(name="Elsewhere", member_agent_ids=[ada.id, bo.id], created_by=ada.id)
    msg = _line(channel.id, "hi")
    # Task work that reports to this thread is not a conversation turn.
    work = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="task_update", source_channel="channel",
        payload={"channel_id": channel.id}, task_id=None,
    )
    assert db.claim_trigger(work.id) is not None
    elsewhere = _wake(bo.id, other.id, claimed=True)
    dm = db.create_agent_trigger(
        agent_id=cy.id, trigger_type="human_chat", source_channel="chat",
        payload={"content": "hello", "source_message_id": "x"},
    )
    assert db.claim_trigger(dm.id) is not None
    services = _Services()

    result = await rewind_thread(channel.id, msg.id, services=services)

    assert services.resets == []
    assert result.stopped_agent_ids == [] and result.dropped_wakes == 0
    assert _open_triggers() == {work.id: "claimed", elsewhere.id: "claimed", dm.id: "claimed"}


async def test_queued_wakes_for_this_thread_are_dropped_and_other_threads_kept(monkeypatch) -> None:
    ada, bo, channel = _team()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[ada.id, bo.id], created_by=ada.id)
    msg = _line(channel.id, "regretted")
    _wake(ada.id, channel.id)
    _wake(bo.id, channel.id, trigger_type="channel_response")
    kept = _wake(bo.id, other.id)
    emitted: list[str] = []

    async def record_emit(agent_id: str) -> None:
        emitted.append(agent_id)

    monkeypatch.setattr("core.thread_rewind.emit_queue_visibility", record_emit)
    services = _Services()
    result = await rewind_thread(channel.id, msg.id, services=services)

    assert result.dropped_wakes == 2
    assert result.stopped_agent_ids == []
    assert _open_triggers() == {kept.id: "queued"}
    assert services.resets == [] and services.enqueued == []
    # No cancel repainted them, so the rewind repaints each "Queued" line.
    assert emitted == sorted([ada.id, bo.id])


async def test_a_wake_queued_during_the_cancel_is_dropped_too(monkeypatch) -> None:
    ada, bo, channel = _team()
    question = _line(channel.id, "question")
    _wake(ada.id, channel.id, claimed=True, source_message_id=question.id)
    late: list[str] = []

    async def finishing_turn_queues_the_next_speaker(agent_id: str) -> None:
        late.append(_wake(bo.id, channel.id, trigger_type="channel_response").id)
        _line(channel.id, "late partial reply", agent=ada)

    emitted: list[str] = []

    async def record_emit(agent_id: str) -> None:
        emitted.append(agent_id)

    monkeypatch.setattr("core.thread_rewind.emit_queue_visibility", record_emit)
    services = _Services(finishing_turn_queues_the_next_speaker)
    result = await rewind_thread(channel.id, question.id, services=services)

    assert len(late) == 1
    assert _open_triggers() == {}
    assert _transcript(channel.id) == []
    assert len(result.removed_message_ids) == 2
    assert result.dropped_wakes == 2
    assert services.enqueued == []
    # Ada was cancelled (the cancel repaints her); Bo only lost a wake.
    assert emitted == [bo.id]


# ─── Rounds ───


async def test_a_round_sourced_from_a_removed_row_is_deleted_with_its_candidates() -> None:
    ada, bo, channel = _team()
    _line(channel.id, "kept", at="2026-10-08 10:00:00")
    cut = _line(channel.id, "regretted", at="2026-10-08 10:00:01")
    later = _line(channel.id, "Round 2", system=True, at="2026-10-08 10:00:02")
    first = _round(channel.id, cut.id, {ada.id: "responding", bo.id: "queued"})
    second = _round(channel.id, later.id, {bo.id: "pending"})

    await rewind_thread(channel.id, cut.id, services=_Services())

    assert db.get_channel_response_round(first.id) is None
    assert db.get_channel_response_round(second.id) is None
    assert db.list_channel_response_candidates(first.id) == []
    assert db.list_channel_response_candidates(second.id) == []
    assert query("PRAGMA foreign_key_check") == []


async def test_an_active_round_whose_source_is_kept_is_completed() -> None:
    ada, bo, channel = _team()
    cy = db.create_agent("Cy", role="Eng", desk_x=3, desk_y=1)
    assert db.add_channel_members(channel.id, [cy.id]) == 1
    kept = _line(channel.id, "kept question", at="2026-10-08 10:00:00")
    cut = _line(channel.id, "partial reply", agent=ada, at="2026-10-08 10:00:01")
    record = _round(channel.id, kept.id, {ada.id: "responding", bo.id: "queued", cy.id: "responded"})
    _wake(ada.id, channel.id, claimed=True, round_id=record.id, source_message_id=kept.id)
    _wake(bo.id, channel.id, trigger_type="channel_response", round_id=record.id, source_message_id=kept.id)

    result = await rewind_thread(channel.id, cut.id, services=_Services())

    assert _transcript(channel.id) == ["kept question"]
    assert db.get_channel_response_round(record.id).status == "completed"
    statuses = {item.agent_id: item.status for item in db.list_channel_response_candidates(record.id)}
    assert statuses == {ada.id: "observed", bo.id: "observed", cy.id: "responded"}
    assert db.list_channel_response_rounds(channel.id, status="active") == []
    assert result.stopped_agent_ids == [ada.id] and result.dropped_wakes == 2


# ─── Failure paths ───


async def test_a_failed_cancel_requeues_the_held_wakes_and_deletes_nothing() -> None:
    ada, bo, channel = _team()
    question = _line(channel.id, "question")
    record = _round(channel.id, question.id, {ada.id: "responding", bo.id: "queued"})
    claimed = _wake(ada.id, channel.id, claimed=True, round_id=record.id, source_message_id=question.id)
    held = _wake(bo.id, channel.id, trigger_type="channel_response", round_id=record.id,
                 source_message_id=question.id)

    async def worker_dies(agent_id: str) -> None:
        raise RuntimeError("Runtime worker is not running")

    services = _Services(worker_dies)
    with pytest.raises(RuntimeError):
        await rewind_thread(channel.id, question.id, services=services)

    assert _transcript(channel.id) == ["question"]
    assert _open_triggers() == {claimed.id: "claimed"}
    assert services.enqueued == [{
        "agent_id": bo.id,
        "trigger_type": "channel_response",
        "source_channel": "channel",
        "payload": json.loads(held.payload),
        "task_id": None,
    }]
    assert db.get_channel_response_round(record.id).status == "active"


def test_route_returns_503_when_the_cancel_fails(monkeypatch) -> None:
    ada, _bo, channel = _team()
    msg = _line(channel.id, "first")
    _wake(ada.id, channel.id, claimed=True)

    async def worker_dies(agent_id: str) -> None:
        raise RuntimeError("Timed out waiting for runtime command reset_agent_runtime")

    manager = _Manager()
    client = _client(monkeypatch, _Services(worker_dies), manager)
    res = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": msg.id})

    assert res.status_code == 503
    assert res.json()["detail"].startswith("Could not stop the thread's replies: Timed out")
    assert _transcript(channel.id) == ["first"]
    assert manager.channel_resets == [] and manager.activities == []


async def test_a_failed_cut_requeues_the_step_one_wakes_but_not_the_rolled_back_claims(monkeypatch) -> None:
    ada, bo, channel = _team()
    first = _line(channel.id, "first", at="2026-10-08 10:00:00")
    _line(channel.id, "second", at="2026-10-08 10:00:01")
    record = _round(channel.id, first.id, {ada.id: "responding", bo.id: "queued"})
    claimed = _wake(ada.id, channel.id, claimed=True, round_id=record.id, source_message_id=first.id)
    queued = _wake(bo.id, channel.id, trigger_type="channel_response", round_id=record.id,
                   source_message_id=first.id)

    def attachments_fail(message_ids: list[str]) -> list[Any]:
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr("core.thread_rewind.db_att.delete_attachments_for_messages", attachments_fail)
    services = _Services()
    with pytest.raises(RuntimeError):
        await rewind_thread(channel.id, first.id, services=services)

    # The rollback restored the stopped turn's claim; only step 1's wake is queued again.
    assert _open_triggers() == {claimed.id: "claimed"}
    assert [call["payload"] for call in services.enqueued] == [json.loads(queued.payload)]
    assert _transcript(channel.id) == ["first", "second"]
    assert db.get_channel_response_round(record.id).status == "active"


# ─── Side effects of the cut ───


async def test_the_idle_check_records_the_new_last_line() -> None:
    ada, _bo, channel = _team()
    kept = _line(channel.id, "kept", at="2026-10-08 10:00:00")
    cut = _line(channel.id, "regretted", agent=ada, at="2026-10-08 10:00:01")
    state = idle_db.get_channel_idle_check(channel.id)
    state["checked_message_id"] = cut.id
    idle_db.save_channel_idle_check(state)

    await rewind_thread(channel.id, cut.id, services=_Services())
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == kept.id

    await rewind_thread(channel.id, kept.id, services=_Services())
    assert idle_db.get_channel_idle_check(channel.id)["checked_message_id"] == ""


async def test_a_rewind_deletes_the_thread_conversation_notes() -> None:
    """Rewound rows may be folded into the notes, so the notes go with the cut."""
    ada, _bo, channel = _team()
    kept = _line(channel.id, "kept question", at="2026-10-08 10:00:00")
    _line(channel.id, "kept answer", agent=ada, at="2026-10-08 10:00:01")
    cut = _line(channel.id, "regretted follow-up", at="2026-10-08 10:00:02")
    assert db.claim_conversation_notes_run("channel", channel.id, stale_after=timedelta(minutes=5))
    assert db.commit_conversation_notes(
        "channel", channel.id, expected_prior_through_id=None,
        through_message_id=kept.id, notes="- an old summary", source_tokens=1,
    )

    await rewind_thread(channel.id, cut.id, services=_Services())

    assert db.get_conversation_notes("channel", channel.id) is None


async def test_removed_attachments_go_and_a_stuck_file_is_counted(tmp_path: Path) -> None:
    _ada, _bo, channel = _team()
    early = _line(channel.id, "early", at="2026-10-08 10:00:00")
    cut = _line(channel.id, "late", at="2026-10-08 10:00:01")
    kept_file = tmp_path / "early.txt"
    kept_file.write_text("early", encoding="utf-8")
    removed_file = tmp_path / "late.txt"
    removed_file.write_text("late", encoding="utf-8")
    # A directory cannot be os.remove()d: an OSError that is not "missing".
    stuck = tmp_path / "stuck"
    stuck.mkdir()
    kept_row = db_att.create_attachment(early.id, "early.txt", 5, "text/plain", str(kept_file), "text",
                                        context_type="thread", context_id=channel.id)
    removed_row = db_att.create_attachment(cut.id, "late.txt", 4, "text/plain", str(removed_file), "text",
                                           context_type="thread", context_id=channel.id)
    db_att.create_attachment(cut.id, "stuck", 1, "text/plain", str(stuck), "text",
                             context_type="thread", context_id=channel.id)

    result = await rewind_thread(channel.id, cut.id, services=_Services())

    assert result.removed_attachments == 2
    assert result.unremoved_files == 1
    assert not removed_file.exists()
    assert db_att.get_attachment_by_id(removed_row.id) is None
    assert db_att.get_attachments_for_message(cut.id) == []
    assert kept_file.exists()
    assert db_att.get_attachment_by_id(kept_row.id) is not None


async def test_the_thread_window_no_longer_contains_the_removed_content() -> None:
    ada, _bo, channel = _team()
    _line(channel.id, "kept question", at="2026-10-08 10:00:00")
    _line(channel.id, "kept answer", agent=ada, at="2026-10-08 10:00:01")
    cut = _line(channel.id, "I hit repeated runtime failures", agent=ada, at="2026-10-08 10:00:02")
    _line(channel.id, "regretted follow-up", at="2026-10-08 10:00:03")

    def window_text() -> str:
        window = load_thread_window(ThreadScope.channel(channel.id), perspective_agent_id=None)
        return json.dumps(window.rows)

    before = window_text()
    assert "repeated runtime failures" in before and "regretted follow-up" in before

    await rewind_thread(channel.id, cut.id, services=_Services())

    after = window_text()
    assert "repeated runtime failures" not in after
    assert "regretted follow-up" not in after
    assert "kept question" in after and "kept answer" in after


# ─── The route ───


def _client(monkeypatch, services: _Services, manager: _Manager) -> TestClient:
    monkeypatch.setattr("api.routes.agents.runtime_services", services)
    monkeypatch.setattr("api.routes.agents.manager", manager)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def test_route_rewinds_and_broadcasts_the_reset(monkeypatch) -> None:
    ada, _bo, channel = _team()
    _line(channel.id, "kept", at="2026-10-08 10:00:00")
    cut = _line(channel.id, "cut", at="2026-10-08 10:00:01")
    _wake(ada.id, channel.id, claimed=True, source_message_id=cut.id)
    services = _Services()
    manager = _Manager()
    client = _client(monkeypatch, services, manager)

    res = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": cut.id})

    assert res.status_code == 200, res.text
    assert res.json() == {
        "status": "ok",
        "removed_messages": 1,
        "removed_attachments": 0,
        "unremoved_files": 0,
        "stopped_agents": 1,
        "dropped_wakes": 1,
    }
    assert manager.channel_resets == [channel.id]
    assert len(manager.channel_updates) == 1
    summary = manager.channel_updates[0]
    assert summary["id"] == channel.id
    assert summary["latest_message"]["content"] == "kept"
    assert len(manager.activities) == 1
    activity = manager.activities[0]
    assert activity["event"] == "thread_rewound"
    assert activity["agent_name"] is None
    assert activity["detail"] == 'Thread "Standup" rewound (1 messages removed, replies stopped)'
    assert manager.world_states == 1
    assert services.resets == [ada.id] and services.enqueued == []
    assert _transcript(channel.id) == ["kept"]


def test_route_sends_the_world_only_when_it_stopped_a_reply(monkeypatch) -> None:
    _ada, _bo, channel = _team()
    _line(channel.id, "kept", at="2026-10-08 10:00:00")
    regretted = _line(channel.id, "regretted", at="2026-10-08 10:00:01")
    manager = _Manager()
    client = _client(monkeypatch, _Services(), manager)

    res = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": regretted.id})

    assert res.status_code == 200, res.text
    assert res.json()["stopped_agents"] == 0
    assert manager.activities[0]["detail"] == 'Thread "Standup" rewound (1 messages removed)'
    assert manager.world_states == 0


def test_route_maps_missing_thread_and_message_to_404_and_archived_to_409(monkeypatch) -> None:
    ada, bo, channel = _team()
    other = db.create_channel(name="Elsewhere", member_agent_ids=[ada.id, bo.id], created_by=ada.id)
    mine = _line(channel.id, "mine")
    theirs = _line(other.id, "theirs")
    manager = _Manager()
    client = _client(monkeypatch, _Services(), manager)

    missing_thread = client.post("/api/channels/nowhere/rewind", json={"from_message_id": mine.id})
    wrong_thread = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": theirs.id})
    empty = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": ""})
    assert db.archive_channel(other.id).status == "archived"
    archived = client.post(f"/api/channels/{other.id}/rewind", json={"from_message_id": theirs.id})

    assert missing_thread.status_code == 404
    assert missing_thread.json()["detail"] == "Thread not found"
    assert wrong_thread.status_code == 404
    assert wrong_thread.json()["detail"] == "Message not found in this thread"
    assert empty.status_code == 422
    assert archived.status_code == 409
    assert archived.json()["detail"] == "Thread archived"
    assert manager.channel_resets == [] and manager.activities == [] and manager.channel_updates == []
    assert _transcript(channel.id) == ["mine"]
    assert _transcript(other.id) == ["theirs"]


def test_route_maps_an_archive_that_races_the_check_to_409(monkeypatch) -> None:
    """Archived between the route's check and the core's: the same 409."""
    _ada, _bo, channel = _team()
    msg = _line(channel.id, "mine")

    async def archived_meanwhile(channel_id: str, from_message_id: str, *, services: Any):
        raise ChannelArchivedError(channel_id)

    monkeypatch.setattr("api.routes.agents.rewind_thread", archived_meanwhile)
    manager = _Manager()
    client = _client(monkeypatch, _Services(), manager)

    res = client.post(f"/api/channels/{channel.id}/rewind", json={"from_message_id": msg.id})

    assert res.status_code == 409
    assert res.json()["detail"] == "Thread archived"
    assert manager.channel_resets == []
