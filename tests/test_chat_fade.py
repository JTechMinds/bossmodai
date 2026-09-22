"""Pressure-gated chat fade. Fails closed to the hard warm window."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.chat_fade import (
    FADE_ID_PREFIX,
    consider_channel_chat_fade,
    note_agent_turn,
    set_chat_fade_scheduler,
)
from core.agent_loop.loop import run_turn
from core.agent_loop.prompt_history import build_prompt_history_view
from core.models.message import HUMAN_SENDER_ID
from db.chat_fade import get_channel_chat_fade, get_chat_fade_gate, upsert_channel_chat_fade
from db.crud import execute


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = "Older turns settled the schedule and left the budget open."


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    set_chat_fade_scheduler(None)


def teardown_function() -> None:
    set_chat_fade_scheduler(None)
    db.close_connection()


def _tokens(text: str, model: str | None = None) -> int:
    return 100


def _patch_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.agent_loop.chat_fade.count_tokens", _tokens)
    monkeypatch.setattr("core.agent_loop.prompt_history.count_tokens", _tokens)


def _capture_scheduler() -> list:
    jobs: list = []

    def _capture(job) -> None:
        jobs.append(job)

    set_chat_fade_scheduler(_capture)
    return jobs


def _agent_channel():
    agent = db.create_agent("Ada", role="QA", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Ops", member_agent_ids=[agent.id], created_by=agent.id)
    db.update_agent_prompt_history_policy(
        agent.id,
        last_n_histories=30,
        max_allowed_history_tokens=1000,
    )
    return agent, channel


def _say(channel_id: str, content: str) -> str:
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
    )
    return message.id


def _fill(channel_id: str, count: int = 8) -> list[str]:
    contents = []
    for index in range(count):
        if index < 3:
            contents.append(f"OLD-TURN-{index} launch note")
        else:
            contents.append(f"TAIL-KEEP-{index} current note")
    return [_say(channel_id, content) for content in contents]


def _history(agent, channel_id: str) -> list[dict]:
    view = build_prompt_history_view(
        agent,
        {"type": "channel_message", "channel_id": channel_id},
    )
    return view.conversation_history


def _enable_system_ai() -> None:
    db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )


def _set_gate(*, turns: int, minutes_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    execute(
        """
        INSERT INTO chat_fade_gate (id, turns_since_run, last_run_at)
        VALUES (1, $1, $2)
        ON CONFLICT(id) DO UPDATE SET
            turns_since_run = excluded.turns_since_run,
            last_run_at = excluded.last_run_at
        """,
        [turns, when],
    )


def _knob(key: str, value: str) -> None:
    db.set_setting(key, value, "llm")


def test_pressure_gate_honors_mode_headroom_turns_and_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    jobs = _capture_scheduler()
    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", lambda *args, **kwargs: SUMMARY)
    _enable_system_ai()

    for index in range(4):
        _say(channel.id, f"note {index}")
    assert _history(agent, channel.id)
    assert jobs == []

    for index in range(4):
        _say(channel.id, f"note {index + 4}")
    _knob("compaction_mode", "off")
    assert _history(agent, channel.id)
    assert jobs == []

    _knob("compaction_mode", "pressure_only")
    _knob("compaction_chat_budget_headroom_percent", "lots")
    assert _history(agent, channel.id)
    assert jobs == []

    _knob("compaction_task_budget_headroom_percent", "0")
    _knob("compaction_chat_budget_headroom_percent", "0")
    assert _history(agent, channel.id)
    assert jobs == []

    _knob("compaction_chat_budget_headroom_percent", "50")
    first = _history(agent, channel.id)
    assert len(jobs) == 1
    assert len(first) == 8

    again = consider_channel_chat_fade(
        channel.id,
        first,
        db.ensure_agent_prompt_history_policy(agent.id),
        agent_id=agent.id,
    )
    assert again is False
    assert len(jobs) == 1

    _set_gate(turns=100, minutes_ago=0)
    assert _history(agent, channel.id)
    assert len(jobs) == 1

    _set_gate(turns=1, minutes_ago=11)
    assert _history(agent, channel.id)
    assert len(jobs) == 1

    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    assert len(jobs) == 2


def test_zeroed_knobs_still_do_not_run_every_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    _knob("compaction_min_turns_between_runs", "0")
    _knob("compaction_cooldown_minutes", "0")
    agent, channel = _agent_channel()
    jobs = _capture_scheduler()
    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", lambda *args, **kwargs: SUMMARY)
    _enable_system_ai()
    _fill(channel.id, 8)

    assert _history(agent, channel.id)
    assert len(jobs) == 1
    note_agent_turn()
    assert _history(agent, channel.id)
    assert len(jobs) == 2
    assert _history(agent, channel.id)
    assert len(jobs) == 2


def test_fail_closed_keeps_the_hard_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    jobs = _capture_scheduler()

    _knob("compaction_mode", "off")
    hard = [item["content"] for item in _history(agent, channel.id)]
    _knob("compaction_mode", "pressure_only")

    unavailable = [item["content"] for item in _history(agent, channel.id)]
    assert unavailable == hard
    assert jobs == []

    _enable_system_ai()
    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", lambda *args, **kwargs: None)
    assert _history(agent, channel.id)
    assert len(jobs) == 1
    jobs[0]()
    assert get_channel_chat_fade(channel.id) is None
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    def _boom(*args, **kwargs):
        raise RuntimeError("system AI down")

    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", _boom)
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    assert get_channel_chat_fade(channel.id) is None
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id="missing-turn",
        summary=SUMMARY,
    )
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=ids[2],
        summary="No",
    )
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=ids[2],
        summary=SUMMARY,
    )
    _knob("compaction_mode", "off")
    assert [item["content"] for item in _history(agent, channel.id)] == hard
    assert all(not str(item["id"]).startswith(FADE_ID_PREFIX) for item in _history(agent, channel.id))


def test_fade_softens_older_turns_without_wiping_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    _fill(channel.id, 8)
    before = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]
    jobs = _capture_scheduler()
    prompts: list[str] = []

    def _complete(messages, max_tokens=180):
        prompts.append(messages[1]["content"])
        return SUMMARY

    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", _complete)
    _enable_system_ai()
    _history(agent, channel.id)
    assert len(jobs) == 1
    jobs[0]()

    after = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]
    assert after == before
    stored = get_channel_chat_fade(channel.id)
    assert stored is not None
    assert stored["summary"] == SUMMARY
    assert "OLD-TURN-0" in prompts[0]
    assert "TAIL-KEEP-7" not in prompts[0]
    assert "/me/notes" not in prompts[0]
    assert "standing_prefs" not in prompts[0]

    faded = _history(agent, channel.id)
    contents = [item["content"] for item in faded]
    assert contents[0] == SUMMARY
    assert str(faded[0]["id"]).startswith(FADE_ID_PREFIX)
    assert all("OLD-TURN" not in content for content in contents)
    assert all(f"TAIL-KEEP-{index}" in " ".join(contents) for index in range(3, 8))
    assert db.list_channel_messages(channel.id)[-1].content == before[-1][1]


def test_channel_history_does_not_block_the_turn_on_system_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    _fill(channel.id, 8)
    _enable_system_ai()
    set_chat_fade_scheduler(None)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    caller = threading.get_ident()
    seen: dict[str, int] = {}

    def _complete(messages, max_tokens=180):
        seen["thread"] = threading.get_ident()
        entered.set()
        release.wait(timeout=5)
        finished.set()
        return SUMMARY

    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", _complete)
    started = time.monotonic()
    try:
        immediate = _history(agent, channel.id)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert any("OLD-TURN-0" in item["content"] for item in immediate)
        assert get_channel_chat_fade(channel.id) is None
        assert entered.wait(2)
        assert not finished.is_set()
        assert seen["thread"] != caller
    finally:
        release.set()
    assert finished.wait(2)
    deadline = time.monotonic() + 2
    while get_channel_chat_fade(channel.id) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    later = _history(agent, channel.id)
    assert later[0]["content"] == SUMMARY
    assert all("OLD-TURN" not in item["content"] for item in later)


def test_human_chat_does_not_queue_fade(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_chat_budget_headroom_percent", "50")
    agent, _channel = _agent_channel()
    jobs = _capture_scheduler()
    _enable_system_ai()
    for index in range(8):
        db.create_message(
            HUMAN_SENDER_ID,
            agent.id,
            f"OLD-TURN-{index} direct note",
            message_type="human",
        )
    view = build_prompt_history_view(agent, {"type": "human_chat"})
    assert view.conversation_history
    assert jobs == []


@pytest.mark.asyncio
async def test_skipped_turn_counts_toward_the_gap_and_returns() -> None:
    agent = db.create_agent("Ada", role="QA", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    outcome = await run_turn(agent, state, {"type": "human_chat"})
    assert outcome.trigger_status == "skipped"
    assert get_chat_fade_gate()["turns_since_run"] == 1


def test_runner_stays_off_standing_prefs_notes_and_soft_blocks() -> None:
    source = (ROOT / "core" / "agent_loop" / "chat_fade.py").read_text(encoding="utf-8")
    assert "read_standing_prefs" not in source
    assert "standing_prefs.json" not in source
    assert "soft_blocks" not in source
    assert "sticky_slot" not in source
    prompt_history = (ROOT / "core" / "agent_loop" / "prompt_history.py").read_text(encoding="utf-8")
    assert "consider_channel_chat_fade" in prompt_history
    assert "apply_channel_chat_fade" in prompt_history
