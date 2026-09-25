"""Fix B — ``work_commit`` is a required, structural field on every reply.

A reply cannot promise work it does not schedule: without live work, a
committed reply is repaired in-turn (nothing is posted) unless it is on the
agent's own open task, which is then requeued exactly. No prose is read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.decision_contract import (
    REPLY_WORK_COMMIT_REQUIRED,
    WORK_COMMIT_CANNOT_START,
    WORK_COMMIT_STARTS_NOTHING,
    ConversationDecision,
    parse_direct_turn_response,
    validate_decision_for_trigger,
)
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.loop import run_turn
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task
from core.default_prompts import load_default_prompt
from core.tasking.transitions import transition_task
from db.settings import reconcile_work_commit_prompt_contract, seed_defaults


def setup_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
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


def _task(agent_id: str, *, title: str, requester_id: str = HUMAN_SENDER_ID):
    return create_or_bind_task(
        title=title,
        description="Do the work.",
        project=None,
        assigned_to=agent_id,
        requester_id=requester_id,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str], *, on_call=None) -> list[list[dict[str, str]]]:
    queue = list(contents)
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append(list(kwargs.get("messages") or []))
        if on_call is not None:
            on_call(len(prompts))
        return LLMResponse(
            content=queue.pop(0), model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return prompts


def _human_chat(content: str = "Can you start the migration?") -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": content,
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


def _agent_messages(agent_id: str) -> list[Any]:
    return [
        message
        for message in db.get_agent_direct_thread(agent_id, HUMAN_SENDER_ID, limit=50)
        if message.from_agent == agent_id
    ]


def test_reply_without_work_commit_fails_the_shape() -> None:
    parsed = parse_direct_turn_response('{"act":"reply","intent":"status","msg":"Status is green.","th":"s"}')
    assert parsed["decision"] == "_parse_failed"
    assert REPLY_WORK_COMMIT_REQUIRED in parsed["_raw_snippet"]
    say_only = parse_direct_turn_response('{"say":"Status is green.","actions":[]}')
    assert say_only["decision"] == "_parse_failed"
    with pytest.raises(ValueError, match="work_commit"):
        ConversationDecision.model_validate(
            {"decision": "answer", "intentKind": "status_request", "reply": "Green."}
        )
    # Non-reply acts do not carry it.
    clarify = parse_direct_turn_response('{"act":"clarify","intent":"work","msg":"Which repo?","th":"q"}')
    assert clarify["decision"] == "clarify"


@pytest.mark.asyncio
async def test_reply_without_work_commit_is_repaired_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Debra", role="Analyst", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    prompts = _script(
        monkeypatch,
        [
            '{"say":"All green.","actions":[]}',
            '{"say":"All green.","actions":[],"work_commit":false}',
        ],
    )
    outcome = await run_turn(agent, state, _human_chat("Status?"))
    assert len(prompts) == 2
    repair = "\n".join(item["content"] for item in prompts[1] if item["role"] == "system")
    assert REPLY_WORK_COMMIT_REQUIRED in repair
    assert outcome.trigger_status == "completed"
    assert [message.content for message in _agent_messages(agent.id)] == ["All green."]


@pytest.mark.asyncio
async def test_committed_reply_with_no_live_work_is_repaired_and_nothing_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Charles", role="Build Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    posted_before_repair: list[int] = []

    def _count_posts(call: int) -> None:
        if call == 2:
            posted_before_repair.append(len(_agent_messages(agent.id)))

    prompts = _script(
        monkeypatch,
        [
            '{"act":"reply","intent":"work","msg":"Starting the migration now.","work_commit":true,"th":"go"}',
            '{"act":"accept","intent":"work","msg":"On it.","commit":"work",'
            '"data":{"task":{"title":"Run the migration","desc":"Run it."}},"th":"accept"}',
        ],
        on_call=_count_posts,
    )
    outcome = await run_turn(agent, state, _human_chat())
    assert len(prompts) == 2
    assert posted_before_repair == [0], "the invalid reply must not post before it is repaired"
    repair = "\n".join(item["content"] for item in prompts[1] if item["role"] == "system")
    assert WORK_COMMIT_STARTS_NOTHING in repair
    assert outcome.trigger_status == "completed"
    assert any(
        item.get("trigger_type") == "activity_resumed" for item in outcome.result["trigger_requests"]
    ), "accept + commit=work schedules the first execution turn"


def test_committed_reply_is_valid_while_work_is_live() -> None:
    agent = db.create_agent("Charles", role="Build Engineer")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "Continuing.", "workCommit": True}
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="human_chat",
            active_task_id="t1",
            has_live_work=True,
            agent_id=agent.id,
            trigger=_human_chat("Status?"),
        )
        is None
    )


def test_committed_reply_on_own_waiting_task_requeues_exactly_that_task() -> None:
    agent = db.create_agent("Jim", role="Engineer")
    peer = db.create_agent("Laura", role="Reviewer")
    waiting = _task(agent.id, title="Ship the notes")
    other = _task(agent.id, title="Unrelated waiting work")
    activity_runtime.activate_work_activity(agent.id, other, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on design.", task_status="waiting")
    activity_runtime.activate_work_activity(agent.id, waiting, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on Laura.", task_status="waiting")
    assert activity_runtime.get_active_work_activity(agent.id) is None
    trigger = {
        "type": "task_follow_up",
        "task_id": waiting.id,
        "task_status": "waiting",
        "task_party": "assignee",
        "attention_kind": "note",
        "from_agent": peer.id,
        "from_name": peer.name,
        "content": "Infra is back, go ahead.",
        "source_channel": "work",
    }
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "Thanks, continuing now.", "workCommit": True}
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="task_follow_up",
            active_task_id=None,
            has_live_work=False,
            agent_id=agent.id,
            trigger=trigger,
        )
        is None
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(decision.model_dump(), agent, state, trigger)
    resumes = [item for item in result["trigger_requests"] if item.get("trigger_type") == "activity_resumed"]
    assert [item["task_id"] for item in resumes] == [waiting.id]
    assert db.get_task(other.id).status == "waiting"


def test_committed_reply_on_someone_elses_task_cannot_start_work() -> None:
    agent = db.create_agent("Laura", role="Reviewer")
    owner = db.create_agent("Jim", role="Engineer")
    task = _task(owner.id, title="Ship the notes", requester_id=agent.id)
    transition_task(task.id, "accepted", reason="test", actor="test")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "I'll handle it.", "workCommit": True}
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_follow_up",
        active_task_id=None,
        has_live_work=False,
        agent_id=agent.id,
        trigger={"type": "task_follow_up", "task_id": task.id, "task_status": "accepted", "task_party": "stakeholder"},
    )
    assert error == WORK_COMMIT_CANNOT_START


def test_uncommitted_reply_never_requeues_work() -> None:
    agent = db.create_agent("Jim", role="Engineer")
    blocked = _task(agent.id, title="Blocked work")
    activity_runtime.activate_work_activity(agent.id, blocked, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Blocked on infra.", task_status="waiting")
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(
        {"decision": "answer", "intentKind": "social_request", "reply": "Morning!", "workCommit": False},
        agent,
        state,
        _human_chat("Morning"),
    )
    assert result["trigger_requests"] == []


@pytest.mark.asyncio
async def test_validation_repair_respects_the_shared_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    db.set_setting("decision_repair_attempts", "1", "llm")
    config.reload()
    agent = db.create_agent("Debra", role="Analyst", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    committed = '{"act":"reply","intent":"work","msg":"Starting now.","work_commit":true,"th":"go"}'
    prompts = _script(monkeypatch, [committed, committed])
    outcome = await run_turn(agent, state, _human_chat())
    assert len(prompts) == 2, "one repair, then the budget is spent"
    assert outcome.trigger_status == "failed"
    assert outcome.diagnostic_error == WORK_COMMIT_STARTS_NOTHING
    assert _agent_messages(agent.id) == []


_PROMPT_KEYS = ("system_prompt_template", "runtime_contract_decision")
_MARKER = "work_commit_prompt_contract_reconciled"


def _stored(key: str) -> str:
    row = db.query_one("SELECT value FROM settings WHERE key = $1", [key])
    assert row is not None
    return str(row["value"])


def test_fresh_db_seeds_the_current_contract_and_marks_it() -> None:
    for key in _PROMPT_KEYS:
        assert _stored(key) == load_default_prompt(key)
    assert _stored(_MARKER) == "true"


def test_old_prompt_rows_are_overwritten_once() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    for key in _PROMPT_KEYS:
        db.execute("UPDATE settings SET value = $1 WHERE key = $2", ["old contract text", key])
    seed_defaults()
    for key in _PROMPT_KEYS:
        assert _stored(key) == load_default_prompt(key)
    assert "TURN MODEL" in _stored("runtime_contract_decision")


def test_a_later_operator_edit_survives_the_next_run() -> None:
    db.set_setting("runtime_contract_decision", "operator's own contract", "advanced")
    seed_defaults()
    reconcile_work_commit_prompt_contract()
    assert _stored("runtime_contract_decision") == "operator's own contract"
