"""Conversation notes: background compaction of DM and thread history.

Each test runs on a fresh temp database (conftest.py points BOSSMOD_DB_PATH at
one). System AI is stubbed by patching ``complete_text`` and
``system_ai_is_configured`` in the module, and runs are captured through
``set_conversation_notes_scheduler`` and awaited here, so nothing is left in
the background. Token counts are patched to fixed sizes so the thresholds are
exact: with a 1000-token budget, start is 800, keep 500, the notes cap 100
and the hard limit 1500.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from api.websocket import ConnectionManager
from core import config
from core.agent_loop import conversation_notes as notes_mod
from core.agent_loop.conversation_notes import (
    NOTES_FROM_NAME,
    NOTES_ID_PREFIX,
    NOTES_LEAD,
    RUN_FAILED_EMPTY,
    RUN_FAILED_TOO_LONG,
    RUN_FAILED_UNEXPECTED,
    ThreadScope,
    _cut_index,
    load_thread_window,
    maybe_schedule_compaction,
    set_conversation_notes_scheduler,
)
from core.agent_loop.prompt_history import build_prompt_history_view
from core.llm import context_builder
from core.models.message import HUMAN_SENDER_ID
from core.runtime.services import RuntimeServices
from db.crud import execute, query

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"
NOTES = "- Ada owes the plan draft\n- Budget is open"


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    _setting("conversation_history_budget_tokens", "1000")
    set_conversation_notes_scheduler(None)


def teardown_function() -> None:
    set_conversation_notes_scheduler(None)
    db.close_connection()


# ─── Helpers ───


def _setting(key: str, value: str) -> None:
    db.set_setting(key, value, "context")
    config.reload()


def _agent(name: str = "Ada", x: int = 1):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1)


def _say(agent_id: str, content: str, *, human: bool) -> str:
    if human:
        return db.create_message(HUMAN_SENDER_ID, agent_id, content, message_type="human").id
    return db.create_message(agent_id, HUMAN_SENDER_ID, content, message_type="social").id


def _dm(agent_id: str, count: int, *, prefix: str = "turn") -> list[str]:
    """Alternate operator and agent rows, operator first."""
    return [_say(agent_id, f"{prefix} {index}", human=index % 2 == 0) for index in range(count)]


def _flat_tokens(monkeypatch: pytest.MonkeyPatch, size: int = 100, *, long_marker: str | None = None) -> None:
    """Every rendered entry costs ``size`` tokens; text with ``long_marker`` costs 500."""
    def _count(text: str) -> int:
        if long_marker is not None and long_marker in text:
            return 500
        return size

    monkeypatch.setattr(notes_mod, "_count", _count)


def _system_ai(monkeypatch: pytest.MonkeyPatch, replies: list[Any], calls: list[list[dict[str, str]]]) -> None:
    """Stub System AI: each call records its messages and pops the next reply."""
    monkeypatch.setattr(notes_mod, "system_ai_is_configured", lambda: True)

    def _complete(messages, *, max_tokens=None):
        calls.append([dict(message) for message in messages])
        reply = replies.pop(0)
        if callable(reply):
            return reply()
        return reply

    monkeypatch.setattr(notes_mod, "complete_text", _complete)


def _capture() -> list:
    jobs: list = []
    set_conversation_notes_scheduler(jobs.append)
    return jobs


def _window(scope: ThreadScope):
    return load_thread_window(scope, perspective_agent_id=None)


def _compact(scope: ThreadScope, jobs: list) -> None:
    """Schedule from the current window and run the captured job to the end."""
    assert maybe_schedule_compaction(scope, _window(scope)) is True
    asyncio.run(jobs.pop(0)())


def _notes(scope: ThreadScope) -> dict[str, Any] | None:
    return db.get_conversation_notes(scope.kind, scope.key)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _get(path: str):
    return _client().get(path, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


# ─── 1, 2: when a run starts ───


def test_below_the_start_line_nothing_is_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 7)
    window = _window(scope)
    assert window.tail_tokens == 700
    assert maybe_schedule_compaction(scope, window) is False
    assert jobs == []
    assert _notes(scope) is None


def test_at_the_start_line_exactly_one_run_is_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 8)
    assert maybe_schedule_compaction(scope, _window(scope)) is True
    assert len(jobs) == 1
    assert _notes(scope)["run_state"] == "running"
    # A second turn while the run is in flight schedules nothing.
    assert maybe_schedule_compaction(scope, _window(scope)) is False
    assert len(jobs) == 1


def test_a_turn_schedules_through_the_prompt_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [], [])
    jobs = _capture()
    agent = _agent()
    _dm(agent.id, 8)
    build_prompt_history_view(agent, {"type": "human_chat"})
    assert len(jobs) == 1


def test_disabled_or_unconfigured_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 9)
    monkeypatch.setattr(notes_mod, "system_ai_is_configured", lambda: False)
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    assert maybe_schedule_compaction(scope, _window(scope)) is False
    assert maybe_schedule_compaction(scope, _window(scope)) is False
    warned = [r for r in caplog.records if "no System AI is configured" in r.getMessage()]
    assert len(warned) == 1, "warned once per scope per process"
    monkeypatch.setattr(notes_mod, "system_ai_is_configured", lambda: True)
    _setting("conversation_compaction_enabled", "false")
    assert maybe_schedule_compaction(scope, _window(scope)) is False
    assert jobs == []


def test_no_event_loop_and_no_scheduler_does_not_claim(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [], [])
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 8)
    caplog.set_level(logging.INFO, logger="core.agent_loop.conversation_notes")
    assert maybe_schedule_compaction(scope, _window(scope)) is False
    assert any("no event loop" in r.getMessage() for r in caplog.records)
    assert _notes(scope) is None


def test_with_a_running_loop_the_run_is_a_background_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    calls: list = []
    _system_ai(monkeypatch, [NOTES], calls)
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 8)

    async def _turn() -> None:
        assert maybe_schedule_compaction(scope, _window(scope)) is True
        # The turn goes on; the run has not happened yet.
        assert _notes(scope)["notes"] is None
        tasks = list(notes_mod._tasks)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    asyncio.run(_turn())
    assert _notes(scope)["notes"] == NOTES
    assert notes_mod._tasks == set()


# ─── 3, 4: the cut and the summarizer input ───


def test_the_cut_keeps_the_newest_half_and_pulls_in_the_answered_row() -> None:
    rows = [{"from_agent": "h" if index % 2 == 0 else "a"} for index in range(10)]
    sizes = [100] * 10
    # Rows 5..9 total the 500 keep tokens; row 5 (agent) answers row 4 (human).
    assert _cut_index(rows, sizes, 500) == 4
    same = [{"from_agent": "h"} for _ in range(10)]
    assert _cut_index(same, sizes, 500) == 5
    # One huge row: everything is kept, nothing to summarize.
    assert _cut_index([{"from_agent": "h"}], [5000], 500) == 0


def test_a_run_folds_the_oldest_rows_and_anchors_after_them(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    calls: list = []
    _system_ai(monkeypatch, [NOTES], calls)
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 10)
    _compact(scope, jobs)
    row = _notes(scope)
    assert row["run_state"] == "idle"
    assert row["last_error"] is None
    assert row["notes"] == NOTES
    # Keep 500 = rows 5..9, plus row 4 that row 5 answers: rows 0..3 folded.
    assert row["through_message_id"] == ids[3]
    assert row["source_tokens"] == 400
    user = calls[0][1]["content"]
    assert "Previous notes:\n(none)" in user
    assert all(f"turn {index}" in user for index in range(4))
    assert all(f"turn {index}" not in user for index in range(4, 10))


def test_prior_notes_are_folded_in_and_no_row_is_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    calls: list = []
    _system_ai(monkeypatch, [NOTES, "- newer notes"], calls)
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    long_text = "word " * 2000
    _say(agent.id, long_text, human=True)
    _dm(agent.id, 9)
    _compact(scope, jobs)
    first_user = calls[0][1]["content"]
    assert f"[Boss]: {long_text}" in first_user, "a row is folded in whole"
    _dm(agent.id, 6, prefix="later")
    _compact(scope, jobs)
    second_user = calls[1][1]["content"]
    assert f"Previous notes:\n{NOTES}" in second_user
    assert _notes(scope)["notes"] == "- newer notes"
    system = calls[1][0]["content"]
    assert "never \"I\" or \"you\"" in system and "Stay under 100 tokens" in system


# ─── 5: what the next turn sees ───


def test_the_next_view_is_the_notes_entry_then_rows_after_the_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 10)
    _compact(scope, jobs)
    history = build_prompt_history_view(db.get_agent(agent.id), {"type": "human_chat"}).conversation_history
    head = history[0]
    assert head["id"] == f"{NOTES_ID_PREFIX}human_dm:{agent.id}"
    assert head["from_agent"] == "__system__"
    assert head["from_name"] == NOTES_FROM_NAME
    assert head["content"] == NOTES_LEAD + NOTES
    assert [item["id"] for item in history[1:]] == ids[4:]
    # Rendered by the context builder as a labelled user line.
    rendered = f"[{head['from_name']}]: {head['content']}"
    assert rendered.startswith("[Conversation notes]: Summary of earlier messages")
    # Transcript rows are never deleted.
    assert [m.id for m in db.get_human_chat_thread(agent.id, limit=100)] == ids


# ─── 6: compare-and-swap ───


def test_a_commit_on_a_stale_anchor_is_superseded(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    ids = _dm(agent.id, 4)
    kind, key = "human_dm", agent.id
    assert db.claim_conversation_notes_run(kind, key, stale_after=timedelta(minutes=5))
    assert db.commit_conversation_notes(
        kind, key, expected_prior_through_id=None, through_message_id=ids[1], notes="first", source_tokens=1,
    )
    # A slower run that also read "no notes" must not overwrite them.
    assert not db.commit_conversation_notes(
        kind, key, expected_prior_through_id=None, through_message_id=ids[2], notes="stale", source_tokens=1,
    )
    row = db.get_conversation_notes(kind, key)
    assert (row["notes"], row["through_message_id"]) == ("first", ids[1])
    assert (row["run_state"], row["last_error"]) == ("idle", "superseded")
    # Nor point at a row that no longer exists.
    assert not db.commit_conversation_notes(
        kind, key, expected_prior_through_id=ids[1], through_message_id="gone", notes="x", source_tokens=1,
    )
    assert db.get_conversation_notes(kind, key)["notes"] == "first"


def test_a_run_overtaken_by_another_commit_is_released(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 10)

    def _concurrent() -> str:
        # Another writer lands notes while this run is waiting on System AI.
        assert db.commit_conversation_notes(
            scope.kind, scope.key, expected_prior_through_id=None,
            through_message_id=ids[1], notes="other", source_tokens=1,
        )
        return NOTES

    _system_ai(monkeypatch, [_concurrent], [])
    jobs = _capture()
    _compact(scope, jobs)
    row = _notes(scope)
    assert row["notes"] == "other"
    assert (row["run_state"], row["last_error"]) == ("idle", "superseded")


# ─── 7, 8: failures keep the old notes ───


def test_over_cap_notes_get_one_shorter_retry_then_fail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch, long_marker="TOO LONG")
    calls: list = []
    _system_ai(monkeypatch, [NOTES, "TOO LONG draft", "TOO LONG again"], calls)
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 10)
    _compact(scope, jobs)
    kept = _notes(scope)
    _dm(agent.id, 6, prefix="later")
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    _compact(scope, jobs)
    row = _notes(scope)
    assert row["run_state"] == "failed"
    assert row["last_error"] == RUN_FAILED_TOO_LONG
    # The numbers are for the log, not the operator.
    assert any("500 tokens, limit 100" in r.getMessage() for r in caplog.records)
    assert (row["notes"], row["through_message_id"]) == (kept["notes"], kept["through_message_id"])
    retry = calls[-1]
    assert retry[-2] == {"role": "assistant", "content": "TOO LONG draft"}
    assert "the limit is 100" in retry[-1]["content"]


def test_a_shorter_retry_within_the_cap_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch, long_marker="TOO LONG")
    _system_ai(monkeypatch, ["TOO LONG draft", NOTES], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 10)
    _compact(scope, jobs)
    assert _notes(scope)["notes"] == NOTES


def test_no_answer_fails_the_run_and_keeps_the_old_notes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES, None], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 10)
    _compact(scope, jobs)
    kept = _notes(scope)
    _dm(agent.id, 6, prefix="later")
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    _compact(scope, jobs)
    row = _notes(scope)
    assert (row["run_state"], row["last_error"]) == ("failed", RUN_FAILED_EMPTY)
    assert row["last_run_at"] is not None
    assert (row["notes"], row["through_message_id"]) == (kept["notes"], kept["through_message_id"])
    assert any("failed" in r.getMessage() for r in caplog.records)


def test_an_exception_fails_the_run_with_exc_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)

    def _boom() -> str:
        raise KeyError("x")

    _system_ai(monkeypatch, [_boom], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 10)
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    _compact(scope, jobs)
    row = _notes(scope)
    assert row["run_state"] == "failed"
    # The operator sees plain text; the technical error is only in the log.
    assert row["last_error"] == RUN_FAILED_UNEXPECTED
    logged = [r for r in caplog.records if r.exc_info]
    assert logged and isinstance(logged[0].exc_info[1], KeyError)
    assert str(logged[0].exc_info[1]) == "'x'"
    # A failed run is not in flight: the next turn above the line retries.
    assert maybe_schedule_compaction(scope, _window(scope)) is True


def test_nothing_to_compact_releases_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch, size=900)
    _system_ai(monkeypatch, [], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _say(agent.id, "one huge message", human=True)
    _compact(scope, jobs)
    row = _notes(scope)
    assert (row["run_state"], row["last_error"], row["notes"]) == ("idle", "nothing to compact", None)


# ─── 9: a missing anchor ───


def test_a_missing_anchor_drops_the_notes(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 10)
    _compact(scope, jobs)
    anchor = _notes(scope)["through_message_id"]
    # Removed behind the delete paths' back, so only the loader can notice.
    execute("DELETE FROM messages WHERE id = $1", [anchor])
    caplog.set_level(logging.INFO, logger="core.agent_loop.conversation_notes")
    window = _window(scope)
    assert window.notes_entry is None
    assert [row["id"] for row in window.rows] == [i for i in ids if i != anchor]
    assert _notes(scope) is None
    assert any("anchor" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_the_delete_paths_delete_the_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = _agent("Ada", 1)
    bo = _agent("Bo", 2)
    ids = _dm(ada.id, 4)
    peer = db.create_message(ada.id, bo.id, "hello Bo", message_type="social").id

    def _store(kind: str, key: str, through: str) -> None:
        assert db.claim_conversation_notes_run(kind, key, stale_after=timedelta(minutes=5))
        assert db.commit_conversation_notes(
            kind, key, expected_prior_through_id=None, through_message_id=through, notes="n", source_tokens=1,
        )

    _store("human_dm", ada.id, ids[1])
    db.delete_human_chat_thread(ada.id)
    assert db.get_conversation_notes("human_dm", ada.id) is None

    ids = _dm(ada.id, 4)
    _store("human_dm", ada.id, ids[1])
    pair = ThreadScope.agent_dm(ada.id, bo.id)
    _store(pair.kind, pair.key, peer)
    assert db.delete_agent_rows(ada.id) is True
    assert db.get_conversation_notes("human_dm", ada.id) is None
    assert db.get_conversation_notes(pair.kind, pair.key) is None

    channel = db.create_channel(name="Ops", member_agent_ids=[bo.id], created_by=bo.id)
    rows = [
        db.create_channel_message(
            channel_id=channel.id, author_type="human", author_name="Boss",
            content=f"line {index}", source_channel="channel",
        ).id
        for index in range(3)
    ]
    _store("channel", channel.id, rows[1])
    db.delete_channel_message(rows[2])
    assert db.get_conversation_notes("channel", channel.id) is not None
    db.delete_channel_message(rows[1])
    assert db.get_conversation_notes("channel", channel.id) is None


# ─── 10: the hard limit ───


def test_the_hard_limit_drops_the_oldest_rows_without_warning_on_a_transcript_read(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)
    _setting("conversation_compaction_enabled", "false")
    _system_ai(monkeypatch, [], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 20)
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    window = _window(scope)
    assert window.ceiling_hit is True
    assert window.tail_tokens == 1500
    assert [row["id"] for row in window.rows] == ids[5:]
    assert window.visible_from_message_id == ids[5]
    # Transcript reads repeat on every UI refresh; the UI shows ceiling_hit.
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert maybe_schedule_compaction(scope, window) is False
    assert jobs == []


def test_the_hard_limit_warns_once_on_an_agent_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flat_tokens(monkeypatch)
    _setting("conversation_compaction_enabled", "false")
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 20)
    caplog.set_level(logging.WARNING, logger="core.agent_loop.conversation_notes")
    window = load_thread_window(scope, perspective_agent_id=agent.id)
    assert window.ceiling_hit is True
    warned = [r for r in caplog.records if "hard limit" in r.getMessage() and r.levelno == logging.WARNING]
    assert len(warned) == 1


# ─── 11: one state for an agent pair ───


def test_an_agent_dm_is_one_scope_from_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES], [])
    jobs = _capture()
    ada = _agent("Ada", 1)
    bo = _agent("Bo", 2)
    assert ThreadScope.agent_dm(ada.id, bo.id) == ThreadScope.agent_dm(bo.id, ada.id)
    for index in range(10):
        sender, receiver = (ada, bo) if index % 2 == 0 else (bo, ada)
        db.create_message(sender.id, receiver.id, f"peer {index}", message_type="social")
    _compact(ThreadScope.agent_dm(ada.id, bo.id), jobs)

    def _view(me, other) -> list[dict]:
        trigger = {"type": "peer_message", "from_agent": other.id}
        return build_prompt_history_view(db.get_agent(me.id), trigger).conversation_history

    from_ada = _view(ada, bo)
    from_bo = _view(bo, ada)
    assert [item["id"] for item in from_ada] == [item["id"] for item in from_bo]
    assert from_ada[0]["content"] == NOTES_LEAD + NOTES
    res = _get(f"/api/conversations/agent_dm/{bo.id}:{ada.id}/window")
    assert res.status_code == 200, res.text
    assert res.json()["visible_from_message_id"] == from_ada[1]["id"]


# ─── 12: a channel reads only its floor ───


def test_a_channel_summary_skips_off_floor_authors(monkeypatch: pytest.MonkeyPatch) -> None:
    from db.floors import create_floor

    _flat_tokens(monkeypatch)
    calls: list = []
    _system_ai(monkeypatch, [NOTES], calls)
    jobs = _capture()
    ada = _agent("Ada", 1)
    channel = db.create_channel(name="Ops", member_agent_ids=[ada.id], created_by=ada.id)
    for index in range(10):
        if index % 2 == 0:
            db.create_channel_message(
                channel_id=channel.id, author_type="human", author_name="Boss",
                content=f"boss {index}", source_channel="channel",
            )
        else:
            db.create_channel_message(
                channel_id=channel.id, author_type="agent", author_agent_id=ada.id,
                author_name="Ada", content=f"ada {index}", source_channel="channel",
            )
    db.update_agent(ada.id, floor_id=create_floor("Finance").id)
    _compact(ThreadScope.channel(channel.id), jobs)
    user = calls[0][1]["content"]
    assert "boss 0" in user and "boss 2" in user
    assert "ada 1" not in user and "ada 3" not in user


# ─── 13: only the history segment changes ───


def test_compaction_changes_only_the_history_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    _dm(agent.id, 10)
    trigger = {"type": "human_chat"}

    def _context() -> list[dict[str, Any]]:
        current = db.get_agent(agent.id)
        history = build_prompt_history_view(current, trigger).conversation_history
        return context_builder.build_context(
            context_builder.TurnContext(
                agent=current,
                state=db.get_agent_state(agent.id),
                trigger=trigger,
                conversation_history=history,
                prompt_notifications=[],
                reference_materials=[],
            )
        )

    before = _context()
    # Building that turn's history put the thread over the start line.
    assert len(jobs) == 1
    asyncio.run(jobs.pop(0)())
    assert _notes(scope)["notes"] == NOTES
    after = _context()

    def _system(messages: list[dict[str, Any]]) -> list[str]:
        return [json.dumps(m, sort_keys=True) for m in messages if m["role"] == "system"]

    assert _system(before) == _system(after)
    assert before[-1] == after[-1], "the trigger is untouched"
    history_before = [m for m in before if m["role"] != "system"][:-1]
    history_after = [m for m in after if m["role"] != "system"][:-1]
    assert history_before != history_after
    assert history_after[0]["content"].startswith("[Conversation notes]: " + NOTES_LEAD)


# ─── 14: the transcript and the agent agree ───


def test_the_window_api_agrees_with_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [NOTES], [])
    jobs = _capture()
    agent = _agent()
    scope = ThreadScope.human_dm(agent.id)
    ids = _dm(agent.id, 10)
    _compact(scope, jobs)
    history = build_prompt_history_view(db.get_agent(agent.id), {"type": "human_chat"}).conversation_history
    res = _get(f"/api/conversations/human_dm/{agent.id}/window")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visible_from_message_id"] == history[1]["id"] == ids[4]
    assert body["ceiling_hit"] is False
    assert body["notes"]["through_message_id"] == ids[3]
    assert body["notes"]["notes"] == NOTES
    assert body["notes"]["run_state"] == "idle"
    assert body["notes"]["last_error"] is None

    # The transcript returns the whole window plus `limit` rows before it.
    rows = _get(f"/api/agents/{agent.id}/messages?limit=2").json()
    assert [row["id"] for row in rows] == ids[2:]


def test_the_window_api_refuses_what_does_not_exist() -> None:
    agent = _agent()
    assert _get("/api/conversations/human_dm/nobody/window").status_code == 404
    assert _get(f"/api/conversations/agent_dm/{agent.id}:nobody/window").status_code == 404
    assert _get(f"/api/conversations/agent_dm/{agent.id}/window").status_code == 404
    assert _get("/api/conversations/channel/nothing/window").status_code == 404
    assert _get(f"/api/conversations/meeting/{agent.id}/window").status_code == 404
    fresh = _get(f"/api/conversations/human_dm/{agent.id}/window").json()
    assert fresh == {"notes": None, "visible_from_message_id": None, "ceiling_hit": False}


def test_a_failed_first_run_is_reported_without_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    _flat_tokens(monkeypatch)
    _system_ai(monkeypatch, [None], [])
    jobs = _capture()
    agent = _agent()
    _dm(agent.id, 10)
    _compact(ThreadScope.human_dm(agent.id), jobs)
    body = _get(f"/api/conversations/human_dm/{agent.id}/window").json()
    assert body["notes"]["run_state"] == "failed"
    assert body["notes"]["last_error"] == RUN_FAILED_EMPTY
    assert body["notes"]["notes"] is None and body["notes"]["through_message_id"] is None


# ─── 15: a crashed run cannot wedge a thread ───


def test_a_stale_running_claim_is_reclaimable() -> None:
    agent = _agent()
    stale = timedelta(minutes=6)
    assert db.claim_conversation_notes_run("human_dm", agent.id, stale_after=stale)
    assert not db.claim_conversation_notes_run("human_dm", agent.id, stale_after=stale)
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=7)
    execute(
        "UPDATE conversation_notes SET run_started_at = $1 WHERE scope_kind = 'human_dm' AND scope_key = $2",
        [long_ago, agent.id],
    )
    assert db.claim_conversation_notes_run("human_dm", agent.id, stale_after=stale)


# ─── Settings, migration and the live event ───


def test_invalid_settings_name_the_key() -> None:
    db.execute("UPDATE settings SET value = '60' WHERE key = 'conversation_compaction_keep_percent'")
    db.execute("UPDATE settings SET value = '50' WHERE key = 'conversation_compaction_start_percent'")
    config.reload()
    with pytest.raises(config.ConfigError, match="conversation_compaction_keep_percent"):
        notes_mod.read_settings()


def test_boot_drops_chat_fade_and_the_policy_window_columns() -> None:
    db.execute("CREATE TABLE channel_chat_fades (channel_id VARCHAR PRIMARY KEY, summary TEXT)")
    db.execute("CREATE TABLE chat_fade_gate (id INTEGER PRIMARY KEY)")
    db.execute(
        "ALTER TABLE agent_prompt_history_policies ADD COLUMN last_n_histories "
        "INTEGER NOT NULL DEFAULT 30 CHECK (last_n_histories >= 0)"
    )
    db.execute(
        "ALTER TABLE agent_prompt_history_policies ADD COLUMN max_allowed_history_tokens "
        "INTEGER NOT NULL DEFAULT 2000 CHECK (max_allowed_history_tokens >= 0)"
    )
    agent = _agent()
    db.close_connection()
    db.init_db()
    tables = {row["name"] for row in query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "channel_chat_fades" not in tables and "chat_fade_gate" not in tables
    assert "conversation_notes" in tables
    columns = {row["name"] for row in query("PRAGMA table_info(agent_prompt_history_policies)")}
    assert not columns & {"last_n_histories", "max_allowed_history_tokens"}
    assert db.get_agent_prompt_history_policy(agent.id) is not None


@pytest.mark.asyncio
async def test_the_notes_event_reaches_the_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Sink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None:
            self.calls.append((scope_kind, scope_key))

    services = RuntimeServices()
    sink = _Sink()
    services.set_event_sink(sink)
    await services._dispatch_event(
        {"kind": "conversation_notes", "data": {"scope_kind": "channel", "scope_key": "c1"}}
    )
    assert sink.calls == [("channel", "c1")]

    manager = ConnectionManager()
    sent: list[dict[str, Any]] = []

    async def _broadcast(message: dict[str, Any]) -> None:
        sent.append(message)

    monkeypatch.setattr(manager, "broadcast", _broadcast)
    await manager.broadcast_conversation_notes("human_dm", "a1")
    assert sent == [{"type": "conversation_notes_updated", "data": {"scope_kind": "human_dm", "scope_key": "a1"}}]


# ─── The transcript divider (JS) ───


def test_the_transcript_divider_marks_the_agents_context() -> None:
    from tests.test_ui_conversation import CONVERSATION_MODULES

    result = subprocess.run(
        ["node", str(Path(__file__).resolve().parent / "js_conversation_notes_harness.cjs"),
         *[str(path) for path in CONVERSATION_MODULES]],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    divider_id = "conversation-notes-human_dm-a"

    # After the last summarized row; the rows before the window are faded and
    # point at the divider; the rows the agent reads are not.
    assert payload["dmRows"] == [
        {"key": "m1", "faded": True, "describedBy": divider_id},
        {"key": "m2", "faded": True, "describedBy": divider_id},
        {"key": "conversation-notes:human_dm:a", "faded": False, "describedBy": None},
        {"key": "m3", "faded": False, "describedBy": None},
        {"key": "m4", "faded": False, "describedBy": None},
    ]
    collapsed = payload["collapsed"]
    assert collapsed["id"] == divider_id
    assert payload["compactedTitle"] in collapsed["text"]
    assert collapsed["toggle"] is True
    assert collapsed["expanded"] == "false" and collapsed["bodyHidden"] is True
    assert collapsed["controls"] == f"{divider_id}-body"
    assert collapsed["bodyText"] == "- Ada owes the plan draft"
    assert collapsed["failure"] == "Last compaction failed: System AI returned no notes"

    assert payload["expanded"]["expanded"] == "true"
    assert payload["expanded"]["bodyHidden"] is False

    # Live: only this conversation's notes event refetches, and the rebuilt
    # divider is still open.
    assert payload["otherScopeRefetched"] is False
    assert payload["refetched"] is True
    assert payload["repainted"] is True
    assert payload["afterRepaint"]["expanded"] == "true"
    assert payload["afterRepaint"]["bodyHidden"] is False

    # No notes but rows past the hard limit: the plain divider, no toggle.
    ceiling = payload["ceiling"]
    assert ceiling["text"] == payload["ceilingTitle"]
    assert ceiling["toggle"] is False and ceiling["failure"] == ""
    assert payload["threadRows"][0]["key"] == "conversation-notes:channel:t1"


def test_the_divider_uses_tokens_and_the_topic_is_known() -> None:
    css = (ROOT / "ui" / "static" / "css" / "conversation.css").read_text(encoding="utf-8")
    tokens = (ROOT / "ui" / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    assert ".msg-turn-faded { opacity: var(--faded-opacity); }" in css
    assert "--faded-opacity:" in tokens
    focus = (JS / "conversation" / "conversation-focus-invalidate.js").read_text(encoding="utf-8")
    bus = (JS / "core" / "bus.js").read_text(encoding="utf-8")
    assert "'conversation_notes_updated'" in focus and "'conversation_notes_updated'" in bus
    controller = (JS / "conversation" / "conversation.js").read_text(encoding="utf-8")
    assert "cardCtx.notesDisclosure = BossModConversationWindow.createDisclosure();" in controller
    html = (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")
    assert html.index("js/conversation/conversation-window.js") < html.index("js/conversation/conversation.js")
