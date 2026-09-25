"""Decision parse failures repair up to N, then note and re-queue once."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_parse_fail import (
    DEFAULT_DECISION_REPAIR_ATTEMPTS,
    PARSE_FAIL_IDLE_NOTE,
    PARSE_FAIL_NOTE,
    decision_repair_attempt_limit,
    surface_decision_parse_failure,
)
from core.agent_loop.loop import run_turn
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.agent_loop.turn_helpers import _build_decision_repair_messages
from core.bm_cli.policy_engine import policy_engine
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID

_REPAIR_MARK = "one JSON object, no fences/markdown"
_ENVELOPE = '{"say":"string","actions":[],"work_commit":false}'


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _limit_decision_repairs(attempts: int) -> None:
    db.set_setting("decision_repair_attempts", str(attempts), "llm")
    config.reload()


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _script_completions(
    monkeypatch: pytest.MonkeyPatch,
    contents: list[str],
) -> tuple[list[str], list[list[dict[str, str]]]]:
    queue = list(contents)
    seen: list[str] = []
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append(list(kwargs.get("messages") or []))
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen, prompts


def _joined_repair(kind: str, snippet: str) -> str:
    messages = _build_decision_repair_messages(parsed_error=snippet, kind=kind)
    return "\n".join(item["content"] for item in messages)


def test_decision_repair_attempts_defaults_to_six() -> None:
    assert config.get("decision_repair_attempts") == "6"
    assert decision_repair_attempt_limit() == DEFAULT_DECISION_REPAIR_ATTEMPTS == 6
    settings_js = Path("ui/static/js/settings/settings-system.js").read_text(encoding="utf-8")
    assert "decision_repair_attempts" in settings_js
    assert "Decision Repair Attempts" in settings_js


@pytest.mark.parametrize(
    ("kind", "snippet"),
    [
        ("prose_status", "I'll do the tests next."),
        ("invalid_decision", '{"act":"reply","work_commit":false,"th2":"x"}'),
        ("invalid_json", '{"act":'),
    ],
)
def test_every_decision_repair_kind_includes_corrective_steer(kind: str, snippet: str) -> None:
    text = _joined_repair(kind, snippet)
    assert snippet in text or snippet[:20] in text
    assert _REPAIR_MARK in text
    assert _ENVELOPE in text
    assert "Allowed keys only" in text
    assert "not Done" in text
    assert "fake Done" in text or "Do not invent Board status" in text


@pytest.mark.asyncio
async def test_parse_fail_notes_thread_and_requeues_without_approve_or_wipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(1)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    prose = "I'll do the clone next. No JSON."
    seen, prompts = _script_completions(monkeypatch, [prose, prose])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 2
    repair_text = "\n".join(item.get("content") or "" for item in prompts[1])
    assert "prose status" in repair_text
    assert _REPAIR_MARK in repair_text
    assert _ENVELOPE in repair_text
    assert outcome.result.get("event") == "agent_error"
    assert outcome.result.get("parse_steer") is True
    assert outcome.trigger_status == "completed"
    events = db.list_task_events(task.id)
    assert sum(1 for item in events if (item.content or "").strip() == PARSE_FAIL_NOTE) == 1
    wakes = [
        item
        for item in outcome.result.get("trigger_requests") or []
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
    ]
    assert len(wakes) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.get_agent_state(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
    assert db.list_consent_requests(status="pending") == []

    persist_result_triggers(outcome.result)
    again = surface_decision_parse_failure(
        agent=agent,
        trigger={"type": "human_chat", "task_id": task.id},
    )
    assert again.get("trigger_requests") == []
    events = db.list_task_events(task.id)
    assert sum(1 for item in events if (item.content or "").strip() == PARSE_FAIL_NOTE) == 1


@pytest.mark.asyncio
async def test_soft_blocked_commitment_is_not_left_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Write notes", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    apply_no_progress_block(agent, {"type": "human_chat"})
    assert db.get_task(task.id).status == "blocked"
    seen, _prompts = _script_completions(monkeypatch, ["I'll do that next."])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert any(
        item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
        for item in outcome.result.get("trigger_requests") or []
    )
    assert any((item.content or "").strip() == PARSE_FAIL_NOTE for item in db.list_task_events(task.id))
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_repair_can_recover_before_fail_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(6)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, prompts = _script_completions(
        monkeypatch,
        [
            '{"act":"reply","work_commit":false,"msg":"Continuing.","th2":"nope"}',
            '{"say":"Still on the clone.","actions":[],"work_commit":false}',
        ],
    )
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 2
    repair_text = "\n".join(item.get("content") or "" for item in prompts[1])
    assert "invented or disallowed keys" in repair_text
    assert _REPAIR_MARK in repair_text
    assert outcome.result.get("event") == "decision_applied"
    assert not any((item.content or "").strip() == PARSE_FAIL_NOTE for item in db.list_task_events(task.id))
    assert db.get_task(task.id).status != "complete"


@pytest.mark.asyncio
async def test_truncated_json_repairs_then_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(2)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen, prompts = _script_completions(monkeypatch, ['{"say":', '{"act":', "still prose"])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 3
    for prompt in prompts[1:]:
        text = "\n".join(item.get("content") or "" for item in prompt)
        assert _REPAIR_MARK in text
        assert _ENVELOPE in text
    assert outcome.result.get("parse_steer") is True
    assert outcome.result.get("chat_message")
    # Nothing was open to re-queue, so the note must not claim a re-queue.
    assert PARSE_FAIL_IDLE_NOTE in str(outcome.result["chat_message"].get("content") or "")
    assert PARSE_FAIL_NOTE not in str(outcome.result["chat_message"].get("content") or "")
    notes = db.list_notifications(agent_id=agent.id, limit=8)
    assert sum(1 for item in notes if (item.content or "").strip() == PARSE_FAIL_IDLE_NOTE) == 1
    assert outcome.result.get("trigger_requests") == []
