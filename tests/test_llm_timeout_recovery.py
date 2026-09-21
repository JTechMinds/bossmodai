"""LLM timeouts on a decision turn repair, then note and re-queue once."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from db.settings import get_seed_setting_default
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_parse_fail import (
    TIMEOUT_NOTE,
    surface_llm_timeout_failure,
)
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.bm_cli.policy_engine import policy_engine
from core.llm.client import LLMError, LLMResponse, LLMTimeoutError, completion
from core.models.message import HUMAN_SENDER_ID

_ENVELOPE = '{"say":"string","actions":[]}'
_OK = '{"say":"Still on the clone.","actions":[]}'


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
    contents: list[Any],
) -> tuple[list[Any], list[list[dict[str, str]]]]:
    queue = list(contents)
    seen: list[Any] = []
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append(list(kwargs.get("messages") or []))
        item = queue.pop(0)
        seen.append(item)
        if isinstance(item, Exception):
            raise item
        return _llm(item)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen, prompts


def _chat_trigger() -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": "Status?",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


def _joined(prompt: list[dict[str, str]]) -> str:
    return "\n".join(item.get("content") or "" for item in prompt)


def _assert_timeout_steer(text: str) -> None:
    assert "timed out" in text
    assert "Retry this turn" in text
    assert "one JSON object" in text
    assert _ENVELOPE in text
    assert "not Done" in text
    assert "Do not invent Board status" in text


def _note_count(task_id: str) -> int:
    return sum(
        1 for item in db.list_task_events(task_id) if (item.content or "").strip() == TIMEOUT_NOTE
    )


def _claimed_payload(
    agent_id: str,
    *,
    trigger_type: str,
    task_id: str,
) -> dict[str, Any]:
    row = db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type=trigger_type,
        source_channel="chat" if trigger_type == "human_chat" else "work",
        payload={"content": "Status?", "from_name": "Human", "from_id": HUMAN_SENDER_ID},
        task_id=task_id,
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update(
        {
            "type": claimed.trigger_type,
            "trigger_id": claimed.id,
            "task_id": claimed.task_id,
            "source_channel": claimed.source_channel,
            "claim_generation": claimed.claim_generation,
        }
    )
    return payload


@pytest.mark.asyncio
async def test_llm_request_timeout_seconds_still_bounds_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded, category = get_seed_setting_default("llm_request_timeout_seconds")
    assert seeded == "720"
    assert category == "llm"
    assert config.get("llm_request_timeout_seconds") == "720"
    settings_js = Path("ui/static/js/settings/settings-system.js").read_text(encoding="utf-8")
    assert "llm_request_timeout_seconds" in settings_js
    assert "LLM Request Timeout" in settings_js
    assert "720" in settings_js
    seen: list[float] = []

    async def _fake_acompletion(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("model call should not run when the wait is cancelled")

    async def _fake_wait_for(awaitable: Any, timeout: float) -> Any:
        seen.append(timeout)
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    monkeypatch.setattr("core.llm.client.asyncio.wait_for", _fake_wait_for)

    async def _call() -> None:
        with pytest.raises(LLMTimeoutError) as raised:
            await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
        assert isinstance(raised.value, LLMError)
        assert raised.value.timeout_seconds == seen[-1]
        assert f"{seen[-1]:g}s" in str(raised.value)

    await _call()
    assert seen == [720.0]

    db.set_setting("llm_request_timeout_seconds", "15", "llm")
    config.reload()
    await _call()
    assert seen == [720.0, 15.0]


@pytest.mark.asyncio
async def test_timeout_repairs_up_to_the_decision_budget_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, prompts = _script_completions(
        monkeypatch,
        [LLMTimeoutError(120)] * 6 + [_OK],
    )
    outcome = await run_turn(agent, state, _chat_trigger())
    assert len(seen) == 7
    for prompt in prompts[1:]:
        _assert_timeout_steer(_joined(prompt))
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("llm_timeout") is not True
    assert _note_count(task.id) == 0
    assert db.get_task(task.id).status == "active"
    assert db.get_agent(agent.id) is not None


@pytest.mark.asyncio
async def test_timeout_fail_close_notes_thread_and_requeues_without_approve_or_wipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(1)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, prompts = _script_completions(
        monkeypatch,
        [LLMTimeoutError(120), LLMTimeoutError(120)],
    )
    outcome = await run_turn(agent, state, _chat_trigger())
    assert len(seen) == 2
    _assert_timeout_steer(_joined(prompts[1]))
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("llm_timeout") is True
    assert outcome.result.get("parse_steer") is not True
    assert "one JSON object" in str(outcome.result.get("detail") or "")
    assert "Done" in str(outcome.result.get("detail") or "")
    assert _note_count(task.id) == 1
    wakes = [
        item
        for item in outcome.result.get("trigger_requests") or []
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
    ]
    assert len(wakes) == 1
    assert (wakes[0].get("payload") or {}).get("repair_wake") is True
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.get_agent_state(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
    assert db.list_consent_requests(status="pending") == []

    persist_result_triggers(outcome.result)
    again = surface_llm_timeout_failure(
        agent=agent,
        trigger={"type": "human_chat", "task_id": task.id},
    )
    assert again.get("trigger_requests") == []
    assert _note_count(task.id) == 1


@pytest.mark.asyncio
async def test_timeout_shares_the_decision_repair_budget_with_parse_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(2)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, prompts = _script_completions(
        monkeypatch,
        [LLMTimeoutError(45), '{"act":', "still prose"],
    )
    outcome = await run_turn(agent, state, _chat_trigger())
    assert len(seen) == 3
    _assert_timeout_steer(_joined(prompts[1]))
    assert "one JSON object" in _joined(prompts[2])
    assert outcome.result.get("parse_steer") is True
    assert outcome.result.get("llm_timeout") is not True
    assert outcome.trigger_status == "completed"
    assert _note_count(task.id) == 0
    events = db.list_task_events(task.id)
    assert any("Decision parse failed" in (item.content or "") for item in events)


@pytest.mark.asyncio
async def test_soft_blocked_commitment_is_requeued_after_timeout(
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
    seen, _prompts = _script_completions(monkeypatch, [LLMTimeoutError(120)])
    outcome = await run_turn(agent, state, _chat_trigger())
    assert len(seen) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert any(
        item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
        for item in outcome.result.get("trigger_requests") or []
    )
    assert _note_count(task.id) == 1
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_other_llm_errors_still_fail_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit_decision_repairs(6)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, _prompts = _script_completions(monkeypatch, [LLMError("connection reset")])
    outcome = await run_turn(agent, state, _chat_trigger())
    assert len(seen) == 1
    assert outcome.trigger_status == "failed"
    assert outcome.result.get("llm_timeout") is not True
    assert _note_count(task.id) == 0
    assert outcome.result.get("trigger_requests") in (None, [])
    assert db.get_task(task.id).status == "active"
    assert db.get_agent(agent.id) is not None


@pytest.mark.asyncio
async def test_dispatcher_timeout_completes_and_requeues_instead_of_stalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    _script_completions(monkeypatch, [LLMTimeoutError(120)])
    payload = _claimed_payload(agent.id, trigger_type="human_chat", task_id=task.id)
    await TurnDispatcher()._run_trigger(agent, state, payload)

    refreshed = db.get_agent_trigger(payload["trigger_id"])
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.retry_count == 0
    assert db.get_task(task.id).status == "active"
    assert _note_count(task.id) == 1
    queued = [
        item
        for item in db.list_queued_triggers(limit=20)
        if item.agent_id == agent.id and item.trigger_type == "activity_resumed" and item.task_id == task.id
    ]
    assert len(queued) == 1
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_escaped_decision_timeout_does_not_enter_supervise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise LLMTimeoutError(120)

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    payload = _claimed_payload(agent.id, trigger_type="human_chat", task_id=task.id)
    await TurnDispatcher()._run_trigger(agent, state, payload)

    refreshed = db.get_agent_trigger(payload["trigger_id"])
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.retry_count == 0
    assert db.get_task(task.id).status == "active"
    assert _note_count(task.id) == 1
    assert any(
        item.agent_id == agent.id and item.trigger_type == "activity_resumed" and item.task_id == task.id
        for item in db.list_queued_triggers(limit=20)
    )


@pytest.mark.asyncio
async def test_execution_timeout_still_uses_trigger_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise LLMTimeoutError(120)

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    payload = _claimed_payload(agent.id, trigger_type="activity_resumed", task_id=task.id)
    await TurnDispatcher()._run_trigger(agent, state, payload)

    refreshed = db.get_agent_trigger(payload["trigger_id"])
    assert refreshed is not None
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert db.get_task(task.id).status != "stalled"
    assert _note_count(task.id) == 0
