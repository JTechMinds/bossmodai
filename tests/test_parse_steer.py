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
    INVALID_DECISION_STEER,
    PROSE_STATUS_STEER,
    classify_json_parse_failure,
    kind_for_schema_error,
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
    assert parse_failure_should_repair(
        kind="invalid_decision", repair_attempts=0, max_repairs=2
    ) is False
    assert kind_for_schema_error("unexpected top-level keys: _needsApproval") == (
        "invalid_decision"
    )
    assert kind_for_schema_error('missing "act"') == "invalid_json"
    steer = parse_failure_steer("prose_status")
    assert PROSE_STATUS_STEER in steer
    assert "Do not park @Operator" in steer
    assert "do not invent a desk" in steer.lower()
    assert not steer.startswith("Blocked")


def test_parse_direct_turn_prose_is_parse_failed_prose_kind() -> None:
    parsed = parse_direct_turn_response("In progress — waiting on pytest.")
    assert parsed["decision"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "prose_status"


def test_parse_action_prose_is_parse_failed_prose_kind() -> None:
    parsed = parse_action("Working. No JSON.")
    assert parsed["action"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "prose_status"


def test_invented_needs_approval_is_invalid_decision_not_schema_key() -> None:
    raw = (
        '{"act":"reply","intent":"status","msg":"Continuing.",'
        '"_needsApproval":true,"th":"need a card"}'
    )
    parsed = parse_direct_turn_response(raw)
    assert parsed["decision"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "invalid_decision"
    assert "_needsApproval" in str(parsed.get("_raw_snippet") or "")
    assert "_needsApproval" not in parsed or parsed.get("decision") == "_parse_failed"
    steer = parse_failure_steer("invalid_decision", parsed.get("_raw_snippet", ""))
    assert INVALID_DECISION_STEER in steer
    assert "Do not invent approval fields" in steer
    assert "Do not park @Operator" in steer
    assert "do not invent a desk" in steer.lower()
    assert not steer.startswith("Blocked")
    valid = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Continuing.","th":"ok"}'
    )
    assert valid.get("decision") == "answer"
    assert "_needsApproval" not in valid


def test_parse_action_invented_needs_approval_is_invalid_decision() -> None:
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"sed -i s/a/b/ tests/x.py"},'
        '"_needsApproval":true,"th":"edit"}'
    )
    assert parsed["action"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "invalid_decision"
    assert "_needsApproval" in str(parsed.get("_raw_snippet") or "")


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
    assert "Do not park @Operator" in detail
    assert not detail.startswith("Blocked")
    assert outcome.result.get("parse_steer") is True


@pytest.mark.asyncio
async def test_decision_invented_needs_approval_fail_closes_without_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen = _script_completions(
        monkeypatch,
        [
            '{"act":"reply","intent":"status","msg":"I will continue.",'
            '"_needsApproval":true,"th":"waiting"}'
        ],
    )
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Continue.",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 1
    assert outcome.result.get("event") == "agent_error"
    detail = str(outcome.result.get("detail") or "")
    assert "Do not invent approval fields" in detail
    assert "Approve/Reject" in detail
    assert "Do not park @Operator" in detail
    assert "do not invent a desk" in detail.lower()
    assert "_needsApproval" in detail
    assert not detail.startswith("Blocked")
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
    assert "Do not park @Operator" in detail
    assert not detail.startswith("Blocked")
