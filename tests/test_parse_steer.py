"""Prose-status parse failures fail-closed with a steer, not a burn loop."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.decision_contract import ConversationDecision, parse_direct_turn_response
from core.agent_loop.loop import run_turn
from core.agent_loop.parse_steer import (
    INVALID_DECISION_STEER,
    PROSE_STATUS_STEER,
    classify_json_parse_failure,
    kind_for_schema_error,
    parse_failure_should_repair,
    parse_failure_steer,
    peel_decision_envelope,
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


def _limit_decision_repairs(attempts: int) -> None:
    db.set_setting("decision_repair_attempts", str(attempts), "llm")
    config.reload()


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
    assert parse_failure_should_repair(
        kind="prose_status", repair_attempts=0, max_repairs=6, decision=True
    ) is True
    assert parse_failure_should_repair(
        kind="invalid_decision", repair_attempts=5, max_repairs=6, decision=True
    ) is True
    assert parse_failure_should_repair(
        kind="invalid_json", repair_attempts=6, max_repairs=6, decision=True
    ) is False
    assert kind_for_schema_error("unexpected top-level keys: _needsApproval") == (
        "invalid_decision"
    )
    assert kind_for_schema_error(
        'missing "act"',
        {"decision": "answer", "_needsApproval": True},
    ) == "invalid_decision"
    assert kind_for_schema_error('missing "act"') == "invalid_json"
    assert kind_for_schema_error(
        'missing "act"',
        {"say": "Committed.", "actions": []},
    ) == "invalid_json"
    steer = parse_failure_steer("prose_status")
    assert PROSE_STATUS_STEER in steer
    assert "say plus optional actions" in steer
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
    assert "approval_required" in steer
    assert "request id" in steer
    assert "invented JSON fields" in steer
    assert "Do not park @Operator" in steer
    assert "do not invent a desk" in steer.lower()
    assert not steer.startswith("Blocked")
    other = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Continuing.","extraField":1}'
    )
    assert other["decision"] == "_parse_failed"
    assert other.get("_parse_kind") == "invalid_decision"
    say_extra = parse_direct_turn_response(
        '{"say":"Committed.","actions":[],"extraField":1}'
    )
    assert say_extra["decision"] == "_parse_failed"
    assert say_extra.get("_parse_kind") == "invalid_decision"
    valid = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Continuing.","th":"ok"}'
    )
    assert valid.get("decision") == "answer"
    assert "_needsApproval" not in valid
    assert "_needsApproval" not in ConversationDecision.model_fields


def test_invented_th2_key_is_invalid_decision_not_schema_key() -> None:
    raw = (
        '{"act":"reply","intent":"status","msg":"Continuing.",'
        '"th2":"need a card"}'
    )
    parsed = parse_direct_turn_response(raw)
    assert parsed["decision"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "invalid_decision"
    assert "th2" in str(parsed.get("_raw_snippet") or "")
    assert "th2" not in parsed or parsed.get("decision") == "_parse_failed"
    steer = parse_failure_steer("invalid_decision", parsed.get("_raw_snippet", ""))
    assert INVALID_DECISION_STEER in steer
    assert "Do not invent approval fields" in steer
    assert "Do not park @Operator" in steer
    assert not steer.startswith("Blocked")
    assert kind_for_schema_error("unexpected top-level keys: th2") == "invalid_decision"
    assert kind_for_schema_error(
        'unexpected top-level keys: th2',
        {"act": "reply", "intent": "status", "msg": "Continuing.", "th2": "x"},
    ) == "invalid_decision"
    valid = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Continuing.","th":"ok"}'
    )
    assert valid.get("decision") == "answer"
    assert "th2" not in ConversationDecision.model_fields


def test_parse_action_invented_th2_is_invalid_decision() -> None:
    parsed = parse_action(
        '{"act":"cli","data":{"cmd":"git push origin main"},"th2":"push"}'
    )
    assert parsed["action"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "invalid_decision"
    assert "th2" in str(parsed.get("_raw_snippet") or "")


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
    _limit_decision_repairs(0)
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
    _limit_decision_repairs(0)
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
    assert "approval_required" in detail
    assert "request id" in detail
    assert "Do not park @Operator" in detail
    assert "do not invent a desk" in detail.lower()
    assert "_needsApproval" in detail
    assert not detail.startswith("Blocked")
    assert outcome.result.get("parse_steer") is True


@pytest.mark.asyncio
async def test_decision_invented_th2_fail_closes_without_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen = _script_completions(
        monkeypatch,
        [
            '{"act":"reply","intent":"status","msg":"I will continue.",'
            '"th2":"waiting"}'
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
    assert "Do not park @Operator" in detail
    assert "th2" in detail
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


def test_work_commit_flag_is_intent_not_an_invented_key() -> None:
    parsed = parse_direct_turn_response(
        '{"say":"Committing now to land the notes","actions":[],"work_commit":true}'
    )
    assert parsed.get("decision") == "answer"
    assert parsed.get("workCommit") is True
    assert parsed.get("reply") == "Committing now to land the notes"
    assert parsed.get("commitmentKind") == "none"
    omitted = parse_direct_turn_response('{"say":"Still reading the brief.","actions":[]}')
    assert omitted.get("decision") == "answer"
    assert omitted.get("workCommit") is None
    compact = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Committing now to land the notes",'
        '"work_commit":false,"th":"status"}'
    )
    assert compact.get("decision") == "answer"
    assert compact.get("workCommit") is False
    rejected = parse_direct_turn_response(
        '{"say":"Committing now to land the notes","actions":[],"work_commit":"yes"}'
    )
    assert rejected["decision"] == "_parse_failed"
    assert rejected.get("_parse_kind") == "invalid_decision"
    peeled = peel_decision_envelope(
        {"say": "Committing now to land the notes", "actions": [], "work_commit": True}
    )
    assert "work_commit" not in peeled
    assert peeled["msg"] == "Committing now to land the notes"


def test_peel_say_alias_and_empty_actions() -> None:
    peeled = peel_decision_envelope(
        {"say": "Committed and pushed.\n\n- Next: pytest.", "actions": []}
    )
    assert peeled["msg"] == "Committed and pushed.\n\n- Next: pytest."
    assert "actions" not in peeled
    assert "say" not in peeled


def test_say_only_decision_is_a_status_reply() -> None:
    parsed = parse_direct_turn_response(
        '{"say":"Committed and pushed.\\n\\n- Next: pytest.","actions":[]}'
    )
    assert parsed.get("decision") == "answer"
    assert parsed.get("intentKind") == "status_request"
    assert parsed.get("reply") == "Committed and pushed.\n\n- Next: pytest."
    assert parsed.get("commitmentKind") == "none"
    also = parse_direct_turn_response('{"say":"Still on the clone. No Board change."}')
    assert also.get("decision") == "answer"
    assert also.get("reply") == "Still on the clone. No Board change."
    aliased = parse_direct_turn_response(
        '{"act":"reply","intent":"status","say":"Continuing.","msg":"Continuing.","th":"ok"}'
    )
    assert aliased.get("decision") == "answer"
    assert aliased.get("reply") == "Continuing."
    mismatched = parse_direct_turn_response(
        '{"say":"Committed.","msg":"Something else.","actions":[]}'
    )
    assert mismatched["decision"] == "_parse_failed"
    assert mismatched.get("_parse_kind") == "invalid_decision"


def test_say_only_does_not_parse_as_done() -> None:
    parsed = parse_action('{"say":"Done. Tests passed.","actions":[]}')
    assert parsed["action"] == "_parse_failed"
    assert parsed.get("_parse_kind") == "invalid_decision"
    nested = parse_action(
        '{"say":"Done. Tests passed.","actions":[{"act":"done","data":{"sum":"Looks good."}}]}'
    )
    assert nested["action"] == "complete"
    assert nested["summary"] == "Looks good."
    assert nested["followUpMessage"] == "Done. Tests passed."
    assert nested.get("doneClaim") in (None, "", {})


@pytest.mark.asyncio
async def test_one_to_one_say_only_posts_to_chat_without_board_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen = _script_completions(
        monkeypatch,
        ['{"say":"Committed and pushed.\\n\\n- Next: pytest on the clone.","actions":[]}'],
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
    assert len(seen) == 1
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("chat_message")
    thread = db.get_human_chat_thread(agent.id)
    assert any(
        "Committed and pushed" in (item.content or "") and "pytest" in (item.content or "")
        for item in thread
    )
    assert db.list_tasks(assigned_to=agent.id) == []


@pytest.mark.asyncio
async def test_say_only_does_not_complete_or_block_active_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    from core.agent_loop.activity_runtime import activate_work_activity

    activate_work_activity(agent.id, task)
    seen = _script_completions(
        monkeypatch,
        ['{"say":"Done. Tests passed. Waiting on review.","actions":[]}'],
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
    assert len(seen) == 1
    assert outcome.result.get("event") == "decision_applied"
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert refreshed.status != "blocked"
    thread = db.get_human_chat_thread(agent.id)
    assert any("Done. Tests passed." in (item.content or "") for item in thread)


@pytest.mark.asyncio
async def test_envelope_done_without_evidence_still_rejected() -> None:
    agent = db.create_agent("Jim", role="Engineer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    from core.agent_loop.activity_runtime import activate_work_activity

    activate_work_activity(agent.id, task)
    parsed = parse_action(
        '{"say":"Done. Tests passed.","actions":[{"act":"done","data":{"sum":"Looks good."}}]}'
    )
    result = await execute_action(parsed, agent, state)
    assert result["event"] == "world_feedback"
    assert "checkable claim" in result["detail"].lower() or "claim" in result["detail"].lower()
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
