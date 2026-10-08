"""A decision turn saves its ``remember`` sentence before the reply posts.

The save sits between validation and ``apply_decision``: a refused sentence
takes a repair round with nothing posted, an exhausted repair budget posts
the reply without the memory and reports it, and a configuration defect
fails the turn. A saved memory also posts one system line in the boss's DM,
broadcast with the memory's number; a refused one posts none.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import decision_turn, standing_prefs
from core.agent_loop.decision_memory import memory_repair_error
from core.agent_loop.loop import run_turn
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from tests._connections import model_connection

_SAVED = "The boss wants the weekly report as a table, not paragraphs."


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


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _reply(say: str, remember: str) -> str:
    import json

    return json.dumps({"say": say, "remember": remember, "actions": [], "work_commit": False})


def _human_chat() -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": "Stop sending me paragraphs, I want the weekly report as a table.",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


def _agent():
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _agent_replies(agent_id: str) -> list[str]:
    return [
        item.content or ""
        for item in db.get_human_chat_thread(agent_id)
        if item.from_agent == agent_id
    ]


def _record_activity(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    original = decision_turn.manager.broadcast_activity

    async def _recording(**kwargs: Any) -> None:
        seen.append(kwargs)
        await original(**kwargs)

    monkeypatch.setattr(decision_turn.manager, "broadcast_activity", _recording)
    return seen


def _record_chat(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    original = decision_turn.manager.broadcast_chat_message

    async def _recording(**kwargs: Any) -> None:
        seen.append(kwargs)
        await original(**kwargs)

    monkeypatch.setattr(decision_turn.manager, "broadcast_chat_message", _recording)
    return seen


def _memory_notes(agent_id: str) -> list[str]:
    return [note.content for note in db.list_notifications(agent_id=agent_id, limit=20) if note.kind == "memory"]


def _script(
    monkeypatch: pytest.MonkeyPatch,
    contents: list[str],
    on_call: Any = None,
) -> list[list[dict[str, str]]]:
    queue = list(contents)
    calls: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        calls.append(list(kwargs["messages"]))
        if on_call is not None:
            on_call(len(calls))
        return _llm(queue.pop(0))

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return calls


def _limit_line(chars: int) -> None:
    db.set_setting("standing_prefs_line_max_chars", str(chars), "context")
    config.reload()


def _limit_decision_repairs(attempts: int) -> None:
    db.set_setting("decision_repair_attempts", str(attempts), "llm")
    config.reload()


@pytest.mark.asyncio
async def test_a_reply_with_remember_saves_before_the_reply_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent()
    _script(monkeypatch, [_reply("Got it — tables from now on.", _SAVED)])
    activity = _record_activity(monkeypatch)
    at_apply: dict[str, Any] = {}
    original_apply = decision_turn.apply_decision

    def _spy_apply(payload: dict[str, Any], *args: Any) -> dict[str, Any]:
        at_apply["memories"] = [m.text for m in standing_prefs.list_memories(agent.storage_key)]
        at_apply["replies"] = _agent_replies(agent.id)
        return original_apply(payload, *args)

    monkeypatch.setattr(decision_turn, "apply_decision", _spy_apply)

    outcome = await run_turn(agent, state, _human_chat())

    # Saved before apply_decision ran, and nothing was posted before it.
    assert at_apply == {"memories": [_SAVED], "replies": []}
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("memory_saved") == {"id": 1, "text": _SAVED}
    assert [m.text for m in standing_prefs.list_memories(agent.storage_key)] == [_SAVED]
    assert any("tables from now on" in reply for reply in _agent_replies(agent.id))
    assert "memory_add" in outcome.action_summary
    saved = [item for item in activity if item.get("event") == "memory_saved"]
    assert saved == [{"event": "memory_saved", "detail": "Jim saved memory #1", "agent_name": "Jim"}]


@pytest.mark.asyncio
async def test_an_over_limit_remember_takes_one_repair_round_with_nothing_posted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_line(70)
    agent, state = _agent()
    too_long = _SAVED + " Use one row per client and a totals row at the bottom."
    posted_before_repair: list[list[str]] = []

    def _on_call(index: int) -> None:
        if index == 2:
            posted_before_repair.append(_agent_replies(agent.id))
            assert standing_prefs.list_memories(agent.storage_key) == []

    calls = _script(
        monkeypatch,
        [
            _reply("Got it.", too_long),
            _reply("Got it.", _SAVED),
        ],
        on_call=_on_call,
    )

    outcome = await run_turn(agent, state, _human_chat())

    assert len(calls) == 2
    assert posted_before_repair == [[]]
    expected_reason = f"memory is {len(too_long)} characters; the limit is 70. Keep it to 1–2 short sentences."
    repair_text = "\n".join(message["content"] for message in calls[1][len(calls[0]):])
    assert memory_repair_error(expected_reason) in repair_text
    repair_steps = [step for step in outcome.steps if "memory_repair_requested" in str(step.get("result"))]
    assert len(repair_steps) == 1
    assert repair_steps[0]["error"] == expected_reason
    assert [m.text for m in standing_prefs.list_memories(agent.storage_key)] == [_SAVED]
    assert outcome.result.get("memory_saved") == {"id": 1, "text": _SAVED}
    assert len(_agent_replies(agent.id)) == 1
    # One save, one note: the refused first attempt posted nothing.
    assert _memory_notes(agent.id) == ["Jim saved a memory"]


@pytest.mark.asyncio
async def test_an_exhausted_repair_budget_posts_the_reply_without_the_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_line(70)
    _limit_decision_repairs(0)
    agent, state = _agent()
    too_long = _SAVED + " Use one row per client and a totals row at the bottom."
    calls = _script(monkeypatch, [_reply("Got it — tables from now on.", too_long)])
    activity = _record_activity(monkeypatch)

    outcome = await run_turn(agent, state, _human_chat())

    assert len(calls) == 1
    reason = f"memory is {len(too_long)} characters; the limit is 70. Keep it to 1–2 short sentences."
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("memory_not_saved") == reason
    assert "memory_saved" not in outcome.result
    assert standing_prefs.list_memories(agent.storage_key) == []
    assert any("tables from now on" in reply for reply in _agent_replies(agent.id))
    not_saved = [item for item in activity if item.get("event") == "memory_not_saved"]
    assert not_saved == [
        {
            "event": "memory_not_saved",
            "detail": f"Jim could not save a memory: {reason}",
            "agent_name": "Jim",
        }
    ]
    assert "memory_add" not in outcome.action_summary
    assert _memory_notes(agent.id) == []
    assert not [
        line for line in outcome.result.get("origin_status_messages") or []
        if line.get("notification_kind") == "memory"
    ]


@pytest.mark.asyncio
async def test_a_saved_memory_posts_one_dm_note_broadcast_with_its_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent()
    _script(monkeypatch, [_reply("Got it — tables from now on.", _SAVED)])
    chat = _record_chat(monkeypatch)

    outcome = await run_turn(agent, state, _human_chat())

    assert _memory_notes(agent.id) == ["Jim saved a memory"]
    notes = [
        line for line in outcome.result.get("origin_status_messages") or []
        if line.get("notification_kind") == "memory"
    ]
    assert len(notes) == 1
    assert notes[0]["content"] == "Jim saved a memory"
    assert notes[0]["memory_id"] == 1
    broadcast = [item for item in chat if item.get("notification_kind") == "memory"]
    assert len(broadcast) == 1
    assert broadcast[0]["agent_id"] == agent.id
    assert broadcast[0]["content"] == "Jim saved a memory"
    assert broadcast[0]["memory_id"] == 1
    assert broadcast[0]["from_type"] == "system"
    # The reply itself still posts once, and carries no memory number.
    replies = [item for item in chat if item.get("from_type") == "agent"]
    assert [item["content"] for item in replies] == ["Got it — tables from now on."]
    assert all(item.get("memory_id") is None for item in replies)


@pytest.mark.asyncio
async def test_a_config_error_fails_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent()
    _script(monkeypatch, [_reply("Got it.", _SAVED)])

    def _broken_add(storage_key: str, text: str) -> standing_prefs.Memory:
        raise config.ConfigError("Setting 'standing_prefs_section_max_chars' must be at least 1: 0")

    monkeypatch.setattr(standing_prefs, "add_memory", _broken_add)

    with pytest.raises(config.ConfigError, match="standing_prefs_section_max_chars"):
        await run_turn(agent, state, _human_chat())
    assert _agent_replies(agent.id) == []


@pytest.mark.asyncio
async def test_a_corrupt_store_reports_without_a_repair_round(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent, state = _agent()
    store = standing_prefs.standing_prefs_file(agent.storage_key)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")
    calls = _script(monkeypatch, [_reply("Got it — tables from now on.", _SAVED)])
    activity = _record_activity(monkeypatch)

    with caplog.at_level("ERROR", logger="core.agent_loop.decision_turn"):
        outcome = await run_turn(agent, state, _human_chat())

    reason = "memory store is unreadable; refusing to overwrite"
    assert len(calls) == 1
    assert not [step for step in outcome.steps if "memory_repair_requested" in str(step.get("result"))]
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("memory_not_saved") == reason
    assert store.read_text(encoding="utf-8") == "{not json"
    assert any("tables from now on" in reply for reply in _agent_replies(agent.id))
    assert _memory_notes(agent.id) == []
    not_saved = [item for item in activity if item.get("event") == "memory_not_saved"]
    assert not_saved == [
        {"event": "memory_not_saved", "detail": f"Jim could not save a memory: {reason}", "agent_name": "Jim"}
    ]
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(agent.storage_key in message and reason in message for message in errors)
