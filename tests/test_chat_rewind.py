"""Rewinding an agent DM: the cut, the stopped turn, the requeue and the route.

Each test runs on a fresh temp database (conftest.py points BOSSMOD_DB_PATH at
one). The runtime is a fake that records calls, so no worker is started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.prompt_history import build_prompt_history_view
from core.chat_rewind import rewind_human_chat
from core.models.message import HUMAN_SENDER_ID
from db import attachments as db_att
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
        self.chat_resets: list[str] = []
        self.activities: list[dict[str, Any]] = []

    async def broadcast_chat_reset(self, agent_id: str) -> None:
        self.chat_resets.append(agent_id)

    async def broadcast_activity(self, **kwargs: Any) -> None:
        self.activities.append(kwargs)


def _agent(name: str = "Ada", x: int = 1):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1)


def _say(agent_id: str, content: str, *, human: bool, at: str | None = None):
    """One DM row, optionally stamped with an exact ``created_at``."""
    if human:
        msg = db.create_message(HUMAN_SENDER_ID, agent_id, content, message_type="human")
    else:
        msg = db.create_message(agent_id, HUMAN_SENDER_ID, content, message_type="social")
    if at is not None:
        execute("UPDATE messages SET created_at = $1 WHERE id = $2", [at, msg.id])
    return msg


def _chat_wake(agent_id: str, source_message_id: str, *, claimed: bool = False):
    trigger = db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "hello", "source_message_id": source_message_id},
    )
    if claimed:
        assert db.claim_trigger(trigger.id) is not None
    return trigger


def _thread(agent_id: str) -> list[str]:
    return [msg.content for msg in db.get_human_chat_thread(agent_id, limit=100)]


def _open_triggers(agent_id: str) -> list[dict[str, Any]]:
    return query(
        "SELECT id, trigger_type, status, payload FROM agent_triggers "
        "WHERE agent_id = $1 AND status IN ('queued', 'claimed') ORDER BY created_at, id",
        [agent_id],
    )


# ─── (a) and (b): the cut ───


async def test_a_removes_the_cut_row_and_everything_after_it() -> None:
    agent = _agent()
    _say(agent.id, "one", human=True, at="2026-10-08 10:00:00")
    _say(agent.id, "two", human=False, at="2026-10-08 10:00:01")
    cut = _say(agent.id, "three", human=True, at="2026-10-08 10:00:02")
    _say(agent.id, "four", human=False, at="2026-10-08 10:00:03")
    # Another agent's DM in the same window is a different conversation.
    other = _agent("Bo", x=2)
    _say(other.id, "elsewhere", human=True, at="2026-10-08 10:00:05")

    services = _Services()
    result = await rewind_human_chat(agent.id, cut.id, services=services)

    assert _thread(agent.id) == ["one", "two"]
    assert _thread(other.id) == ["elsewhere"]
    assert len(result.removed_message_ids) == 2
    assert cut.id in result.removed_message_ids
    assert result.stopped_turn is False
    assert services.resets == []


async def test_b_same_second_rows_are_cut_in_insertion_order() -> None:
    agent = _agent()
    second = "2026-10-08 10:00:00"
    _say(agent.id, "first", human=True, at=second)
    cut = _say(agent.id, "second", human=False, at=second)
    _say(agent.id, "third", human=True, at=second)

    assert _thread(agent.id) == ["first", "second", "third"]
    await rewind_human_chat(agent.id, cut.id, services=_Services())
    assert _thread(agent.id) == ["first"]


async def test_c_a_message_from_another_conversation_changes_nothing() -> None:
    agent = _agent()
    other = _agent("Bo", x=2)
    mine = _say(agent.id, "mine", human=True)
    theirs = _say(other.id, "theirs", human=True)
    queued = _chat_wake(agent.id, mine.id)
    claimed_other = _chat_wake(other.id, theirs.id, claimed=True)
    services = _Services()

    with pytest.raises(LookupError):
        await rewind_human_chat(agent.id, theirs.id, services=services)
    with pytest.raises(LookupError):
        await rewind_human_chat(agent.id, "no-such-message", services=services)

    assert _thread(agent.id) == ["mine"]
    assert _thread(other.id) == ["theirs"]
    assert [row["id"] for row in _open_triggers(agent.id)] == [queued.id]
    assert [row["id"] for row in _open_triggers(other.id)] == [claimed_other.id]
    assert services.resets == [] and services.enqueued == []


# ─── (d) to (h): the live turn and the queue ───


async def test_d_a_claimed_chat_turn_is_stopped_and_its_row_removed() -> None:
    agent = _agent()
    question = _say(agent.id, "question", human=True)
    _chat_wake(agent.id, question.id, claimed=True)
    notifications_before = len(db.list_notifications(agent_id=agent.id, limit=100))
    services = _Services()

    result = await rewind_human_chat(agent.id, question.id, services=services)

    assert services.resets == [agent.id]
    assert result.stopped_turn is True
    assert _open_triggers(agent.id) == []
    # A cancel is not a failure: no "stuck turn" notice reaches the DM.
    assert _thread(agent.id) == []
    assert len(db.list_notifications(agent_id=agent.id, limit=100)) == notifications_before
    assert services.enqueued == []


async def test_e_a_claimed_non_chat_turn_is_left_alone() -> None:
    agent = _agent()
    msg = _say(agent.id, "hi", human=True)
    work = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="channel_message",
        source_channel="channel",
        payload={"channel_id": "c-1", "source_message_id": "x"},
    )
    assert db.claim_trigger(work.id) is not None
    services = _Services()

    result = await rewind_human_chat(agent.id, msg.id, services=services)

    assert services.resets == []
    assert result.stopped_turn is False
    rows = _open_triggers(agent.id)
    assert [(row["id"], row["status"]) for row in rows] == [(work.id, "claimed")]


async def test_f_held_wakes_are_dropped_or_queued_again_by_their_source() -> None:
    agent = _agent()
    kept = _say(agent.id, "kept question", human=True, at="2026-10-08 10:00:00")
    cut = _say(agent.id, "partial reply", human=False, at="2026-10-08 10:00:01")
    gone = _say(agent.id, "regretted", human=True, at="2026-10-08 10:00:02")
    stopped = _chat_wake(agent.id, kept.id, claimed=True)
    queued_kept = _chat_wake(agent.id, kept.id)
    _chat_wake(agent.id, gone.id)
    services = _Services()

    result = await rewind_human_chat(agent.id, cut.id, services=services)

    assert _thread(agent.id) == ["kept question"]
    assert result.requeued == 2
    assert _open_triggers(agent.id) == []  # the fake enqueues nothing itself
    payloads = sorted(json.dumps(call["payload"], sort_keys=True) for call in services.enqueued)
    expected = sorted(
        json.dumps(json.loads(trigger.payload), sort_keys=True) for trigger in (stopped, queued_kept)
    )
    assert payloads == expected
    for call in services.enqueued:
        assert call["agent_id"] == agent.id
        assert call["trigger_type"] == "human_chat"
        assert call["source_channel"] == "chat"
        assert call["payload"]["source_message_id"] == kept.id


async def test_g_rows_the_dying_turn_writes_after_the_freeze_are_cut_too() -> None:
    agent = _agent()
    question = _say(agent.id, "question", human=True)
    _chat_wake(agent.id, question.id, claimed=True)

    async def dying_turn_writes(agent_id: str) -> None:
        db.create_message(agent_id, HUMAN_SENDER_ID, "late partial reply", message_type="social")

    result = await rewind_human_chat(agent.id, question.id, services=_Services(dying_turn_writes))

    assert _thread(agent.id) == []
    assert len(result.removed_message_ids) == 2


async def test_h_a_failed_cancel_requeues_and_deletes_nothing() -> None:
    agent = _agent()
    first = _say(agent.id, "first", human=True, at="2026-10-08 10:00:00")
    second = _say(agent.id, "second", human=True, at="2026-10-08 10:00:01")
    _chat_wake(agent.id, first.id, claimed=True)
    held = _chat_wake(agent.id, second.id)

    async def worker_dies(agent_id: str) -> None:
        raise RuntimeError("Runtime worker is not running")

    services = _Services(worker_dies)
    with pytest.raises(RuntimeError):
        await rewind_human_chat(agent.id, first.id, services=services)

    assert _thread(agent.id) == ["first", "second"]
    assert [call["payload"] for call in services.enqueued] == [json.loads(held.payload)]


def test_h_route_returns_503_when_the_cancel_fails(monkeypatch) -> None:
    agent = _agent()
    msg = _say(agent.id, "first", human=True)
    _chat_wake(agent.id, msg.id, claimed=True)

    async def worker_dies(agent_id: str) -> None:
        raise RuntimeError("Timed out waiting for runtime command reset_agent_runtime")

    manager = _Manager()
    client = _client(monkeypatch, _Services(worker_dies), manager)
    res = client.post(f"/api/agents/{agent.id}/chat-rewind", json={"from_message_id": msg.id})

    assert res.status_code == 503
    assert "Timed out" in res.json()["detail"]
    assert _thread(agent.id) == ["first"]
    assert manager.chat_resets == []


# ─── Audit fixes: a failed cut, and dropped wakes ───


async def test_a_failed_cut_requeues_the_held_wakes(monkeypatch) -> None:
    agent = _agent()
    msg = _say(agent.id, "question", human=True)
    held = _chat_wake(agent.id, msg.id)

    def cut_row_vanished(agent_id: str, from_message_id: str) -> list[str]:
        raise LookupError("cleared meanwhile")

    monkeypatch.setattr("core.chat_rewind.db.delete_human_chat_from", cut_row_vanished)
    services = _Services()
    with pytest.raises(LookupError):
        await rewind_human_chat(agent.id, msg.id, services=services)

    assert [call["payload"] for call in services.enqueued] == [json.loads(held.payload)]
    assert _thread(agent.id) == ["question"]


async def test_a_rolled_back_cut_does_not_requeue_the_restored_claim(monkeypatch) -> None:
    agent = _agent()
    first = _say(agent.id, "first", human=True, at="2026-10-08 10:00:00")
    second = _say(agent.id, "second", human=True, at="2026-10-08 10:00:01")
    claimed = _chat_wake(agent.id, first.id, claimed=True)
    queued = _chat_wake(agent.id, second.id)

    def attachments_fail(message_ids: list[str]) -> list[Any]:
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr("core.chat_rewind.db_att.delete_attachments_for_messages", attachments_fail)
    services = _Services()
    with pytest.raises(RuntimeError):
        await rewind_human_chat(agent.id, first.id, services=services)

    rows = {row["id"]: row["status"] for row in _open_triggers(agent.id)}
    assert rows == {claimed.id: "claimed"}
    assert [call["payload"] for call in services.enqueued] == [json.loads(queued.payload)]
    assert _thread(agent.id) == ["first", "second"]


async def test_dropped_wakes_without_a_stopped_turn_repaint_queue_visibility(monkeypatch) -> None:
    agent = _agent()
    msg = _say(agent.id, "regretted", human=True)
    _chat_wake(agent.id, msg.id)
    emitted: list[str] = []

    async def record_emit(agent_id: str) -> None:
        emitted.append(agent_id)

    monkeypatch.setattr("core.chat_rewind.emit_queue_visibility", record_emit)
    services = _Services()
    result = await rewind_human_chat(agent.id, msg.id, services=services)

    assert result.stopped_turn is False and result.requeued == 0
    assert services.resets == [] and services.enqueued == []
    assert emitted == [agent.id]


# ─── (i): what is and is not rewound ───


async def test_i_notifications_stay_and_removed_attachments_go(tmp_path: Path) -> None:
    agent = _agent()
    early = _say(agent.id, "early", human=True, at="2026-10-08 10:00:00")
    cut = _say(agent.id, "late", human=True, at="2026-10-08 10:00:01")
    note = db.create_notification(
        agent_id=agent.id,
        kind="completion",
        content="Task done",
        source_channel="chat",
        policy="all",
        chat_visible=True,
        prompt_visibility=False,
    )
    files = {}
    for name, message in (("early.txt", early), ("late.txt", cut)):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        files[name] = (path, db_att.create_attachment(
            message.id, name, path.stat().st_size, "text/plain", str(path), "text",
            context_type="direct", context_id=agent.id,
        ))

    result = await rewind_human_chat(agent.id, cut.id, services=_Services())

    assert result.removed_attachments == 1
    assert result.unremoved_files == 0
    assert not files["late.txt"][0].exists()
    assert db_att.get_attachment_by_id(files["late.txt"][1].id) is None
    assert files["early.txt"][0].exists()
    assert db_att.get_attachment_by_id(files["early.txt"][1].id) is not None
    notes = db.list_notifications(agent_id=agent.id, limit=100)
    assert [item.id for item in notes] == [note.id]


async def test_i_a_file_that_cannot_be_removed_is_counted(tmp_path: Path) -> None:
    agent = _agent()
    cut = _say(agent.id, "with a file", human=True)
    # A directory cannot be os.remove()d: an OSError that is not "missing".
    stuck = tmp_path / "stuck"
    stuck.mkdir()
    gone = tmp_path / "already-gone.txt"
    db_att.create_attachment(cut.id, "stuck", 1, "text/plain", str(stuck), "text",
                             context_type="direct", context_id=agent.id)
    db_att.create_attachment(cut.id, "gone", 1, "text/plain", str(gone), "text",
                             context_type="direct", context_id=agent.id)

    result = await rewind_human_chat(agent.id, cut.id, services=_Services())

    assert result.removed_attachments == 2
    # The missing file is a warning, not a failure; the directory is counted.
    assert result.unremoved_files == 1
    assert db_att.get_attachments_for_message(cut.id) == []


# ─── (j): the route ───


def _client(monkeypatch, services: _Services, manager: _Manager) -> TestClient:
    monkeypatch.setattr("api.routes.agents.runtime_services", services)
    monkeypatch.setattr("api.routes.agents.manager", manager)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def test_j_route_rewinds_and_broadcasts_the_reset(monkeypatch) -> None:
    agent = _agent()
    _say(agent.id, "kept", human=True, at="2026-10-08 10:00:00")
    cut = _say(agent.id, "cut", human=True, at="2026-10-08 10:00:01")
    _chat_wake(agent.id, cut.id, claimed=True)
    services = _Services()
    manager = _Manager()
    client = _client(monkeypatch, services, manager)

    res = client.post(f"/api/agents/{agent.id}/chat-rewind", json={"from_message_id": cut.id})

    assert res.status_code == 200, res.text
    assert res.json() == {
        "status": "ok",
        "removed_messages": 1,
        "removed_attachments": 0,
        "unremoved_files": 0,
        "stopped_turn": True,
        "requeued": 0,
    }
    assert manager.chat_resets == [agent.id]
    assert len(manager.activities) == 1
    activity = manager.activities[0]
    assert activity["event"] == "chat_rewound"
    assert activity["agent_name"] == agent.name
    assert activity["detail"] == f'Chat rewound for "{agent.name}" (1 messages removed, reply stopped)'
    assert _thread(agent.id) == ["kept"]


def test_j_route_maps_missing_agent_and_message_to_404(monkeypatch) -> None:
    agent = _agent()
    other = _agent("Bo", x=2)
    theirs = _say(other.id, "theirs", human=True)
    manager = _Manager()
    client = _client(monkeypatch, _Services(), manager)

    missing_agent = client.post("/api/agents/nobody/chat-rewind", json={"from_message_id": theirs.id})
    wrong_dm = client.post(f"/api/agents/{agent.id}/chat-rewind", json={"from_message_id": theirs.id})
    empty = client.post(f"/api/agents/{agent.id}/chat-rewind", json={"from_message_id": ""})

    assert missing_agent.status_code == 404
    assert wrong_dm.status_code == 404
    assert wrong_dm.json()["detail"] == "Message not found in this conversation"
    assert empty.status_code == 422
    assert manager.chat_resets == [] and manager.activities == []
    assert _thread(other.id) == ["theirs"]


# ─── (k): what the model sees next ───


async def test_k_the_prompt_no_longer_contains_the_removed_content() -> None:
    agent = _agent()
    _say(agent.id, "kept question", human=True, at="2026-10-08 10:00:00")
    _say(agent.id, "kept answer", human=False, at="2026-10-08 10:00:01")
    cut = _say(agent.id, "I hit repeated runtime failures. Last error: connection refused",
               human=False, at="2026-10-08 10:00:02")
    _say(agent.id, "regretted follow-up", human=True, at="2026-10-08 10:00:03")

    def history_text() -> str:
        view = build_prompt_history_view(db.get_agent(agent.id), {"type": "human_chat"})
        return json.dumps(view.conversation_history)

    before = history_text()
    assert "connection refused" in before and "regretted follow-up" in before

    await rewind_human_chat(agent.id, cut.id, services=_Services())

    after = history_text()
    assert "connection refused" not in after
    assert "regretted follow-up" not in after
    assert "kept question" in after and "kept answer" in after
