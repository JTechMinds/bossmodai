"""Prose-status parse failures fail-closed with a steer, not a burn loop."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.actions import parse_action
from core.agent_loop.decision_contract import parse_direct_turn_response
from core.agent_loop.loop import run_turn
from core.agent_loop.parse_steer import (
    PROSE_STATUS_STEER,
    classify_json_parse_failure,
    parse_failure_should_repair,
    parse_failure_steer,
)
from core.bm_cli.policy_engine import policy_engine
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID


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


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _script_completions(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[str]:
    queue = list(contents)
    seen: list[str] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


def test_classify_prose_status_vs_invalid_json() -> None:
    assert classify_json_parse_failure("Still working on the tests.") == "prose_status"
    assert classify_json_parse_failure("No JSON here") == "prose_status"
    assert classify_json_parse_failure('{"act":') == "invalid_json"
    assert parse_failure_should_repair(
        kind="prose_status", repair_attempts=0, max_repairs=2
    ) is False
    assert parse_failure_should_repair(
        kind="invalid_json", repair_attempts=0, max_repairs=2
    ) is True
    assert PROSE_STATUS_STEER in parse_failure_steer("prose_status")
    assert "@Operator" not in parse_failure_steer("prose_status")
    assert "desk" not in parse_failure_steer("prose_status").lower() or "do not invent a desk" in parse_failure_steer("prose_status").lower()


def test_parse_direct_turn_prose_is_parse_failed_prose_kind() -> None:
    parsed = parse_direct_turn_response("In progress — waiting on pytest.")
    assert parsed["decision"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "prose_status"


def test_parse_action_prose_is_parse_failed_prose_kind() -> None:
    parsed = parse_action("Working. No JSON.")
    assert parsed["action"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "prose_status"


@pytest.mark.asyncio
async def test_decision_prose_fail_closes_without_repair_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen = _script_completions(monkeypatch, ["Still validating the clone. No JSON."])
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
    assert outcome.result.get("event") == "agent_error"
    detail = str(outcome.result.get("detail") or "")
    assert "Emit the required JSON" in detail
    assert "@Operator" not in detail
    assert outcome.result.get("parse_steer") is True


@pytest.mark.asyncio
async def test_execution_prose_fail_closes_without_repair_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    from core.agent_loop.activity_runtime import activate_work_activity

    activate_work_activity(agent.id, task)
    seen = _script_completions(monkeypatch, ["In progress on tests. Prose only."])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "task_id": task.id,
            "source_channel": "work",
        },
    )
    assert len(seen) == 1
    assert outcome.result.get("event") == "agent_error"
    detail = str(outcome.result.get("detail") or "")
    assert "Emit the required JSON" in detail
    assert "@Operator" not in detail
