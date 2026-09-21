"""Stall timeout cancels idle streams. Live streams keep the 720s backstop."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.decision_parse_fail import TIMEOUT_NOTE
from core.agent_loop.loop import run_turn
from core.bm_cli.policy_engine import policy_engine
from core.llm.client import LLMError, LLMResponse, LLMTimeoutError, completion
from core.models.message import HUMAN_SENDER_ID
from db.settings import get_seed_setting_default

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


def _set_timeouts(*, stall: str, backstop: str) -> None:
    db.set_setting("llm_stall_timeout_seconds", stall, "llm")
    db.set_setting("llm_request_timeout_seconds", backstop, "llm")
    config.reload()


def _chunk(text: str, *, role: str | None = None, finish: str | None = None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if role:
        delta["role"] = role
    if text:
        delta["content"] = text
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Usage:
    prompt_tokens = 3
    completion_tokens = 2
    total_tokens = 5


class _Finished:
    """A completion that arrived without a stream."""

    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.model = "test/mock"
        self.usage = _Usage()


class ScriptedStream:
    def __init__(self, items: list[tuple[float, Any]]) -> None:
        self._items = list(items)
        self.closed = False

    def __aiter__(self) -> ScriptedStream:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        delay, item = self._items.pop(0)
        if delay:
            await asyncio.sleep(delay)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed = True


def _note_count(task_id: str) -> int:
    return sum(
        1 for item in db.list_task_events(task_id) if (item.content or "").strip() == TIMEOUT_NOTE
    )


def test_stall_timeout_is_seeded_beside_the_request_timeout() -> None:
    seeded, category = get_seed_setting_default("llm_stall_timeout_seconds")
    assert seeded == "120"
    assert category == "llm"
    assert config.get("llm_stall_timeout_seconds") == "120"
    backstop, backstop_category = get_seed_setting_default("llm_request_timeout_seconds")
    assert backstop == "720"
    assert backstop_category == "llm"
    settings_js = Path("ui/static/js/settings/settings-system.js").read_text(encoding="utf-8")
    assert settings_js.index("llm_request_timeout_seconds") < settings_js.index("llm_stall_timeout_seconds")
    assert "LLM Stall Timeout" in settings_js
    assert "Default 120" in settings_js
    assert "LLM Request Timeout" in settings_js
    assert "720" in settings_js


def test_llm_timeout_error_kind_defaults_to_the_backstop() -> None:
    error = LLMTimeoutError(720)
    assert error.kind == "backstop"
    assert error.timeout_seconds == 720
    assert "720s" in str(error)
    stall = LLMTimeoutError(120, kind="stall")
    assert stall.kind == "stall"
    assert "no progress" in str(stall)


@pytest.mark.asyncio
async def test_streaming_progress_is_not_killed_before_absolute_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks keep arriving past the stall window, so only the backstop can cancel."""
    _set_timeouts(stall="0.25", backstop="5")
    pieces = ["Hel", "lo", " ", "wor", "ld"]
    stream = ScriptedStream(
        [
            (
                0.06,
                _chunk(
                    text,
                    role="assistant" if index == 0 else None,
                    finish="stop" if index == len(pieces) - 1 else None,
                ),
            )
            for index, text in enumerate(pieces)
        ]
    )
    seen: dict[str, Any] = {}

    async def _fake_acompletion(**kwargs: Any) -> ScriptedStream:
        seen["stream"] = kwargs.get("stream")
        seen["timeout"] = kwargs.get("timeout")
        return stream

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    started = time.monotonic()
    result = await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    elapsed = time.monotonic() - started

    assert seen["stream"] is True
    assert seen["timeout"] == 5.0
    assert result.content == "Hello world"
    assert isinstance(result, LLMResponse)
    assert elapsed > 0.25
    assert stream.closed is True
    assert result.prompt_tokens > 0


@pytest.mark.asyncio
async def test_idle_stream_raises_stall_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_timeouts(stall="0.15", backstop="5")
    stream = ScriptedStream([(1.0, _chunk("late", role="assistant"))])

    async def _fake_acompletion(**_kwargs: Any) -> ScriptedStream:
        return stream

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    with pytest.raises(LLMTimeoutError) as raised:
        await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert raised.value.kind == "stall"
    assert raised.value.timeout_seconds == 0.15
    assert isinstance(raised.value, LLMError)
    assert stream.closed is True


@pytest.mark.asyncio
async def test_streaming_progress_still_hits_the_absolute_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_timeouts(stall="5", backstop="0.3")
    stream = ScriptedStream([(0.05, _chunk("x", role="assistant")) for _ in range(20)])

    async def _fake_acompletion(**_kwargs: Any) -> ScriptedStream:
        return stream

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    with pytest.raises(LLMTimeoutError) as raised:
        await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert raised.value.kind == "backstop"
    assert raised.value.timeout_seconds == 0.3
    assert stream.closed is True


@pytest.mark.asyncio
async def test_non_streaming_path_uses_the_backstop_not_the_stall_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_timeouts(stall="0.12", backstop="2")
    seen: dict[str, Any] = {}

    async def _fake_acompletion(**kwargs: Any) -> _Finished:
        seen["stream"] = kwargs.get("stream")
        seen["timeout"] = kwargs.get("timeout")
        await asyncio.sleep(0.35)
        return _Finished("finished without chunks")

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    result = await completion(
        model="test/mock",
        messages=[{"role": "user", "content": "hi"}],
        extra_body='{"stream": false}',
    )
    assert seen["stream"] is not True
    assert seen["timeout"] == 2.0
    assert result.content == "finished without chunks"
    assert result.total_tokens == 5


@pytest.mark.asyncio
async def test_opening_a_stream_uses_the_backstop_not_the_stall_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No chunk channel yet: a slow open is not a 120s-style wall clock."""
    _set_timeouts(stall="0.12", backstop="2")

    async def _fake_acompletion(**kwargs: Any) -> Any:
        assert kwargs.get("stream") is True
        await asyncio.sleep(0.35)
        return ScriptedStream([(0.0, _chunk("ok", role="assistant", finish="stop"))])

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    result = await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_finished_completion_without_a_stream_keeps_the_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_timeouts(stall="0.12", backstop="2")

    async def _fake_acompletion(**_kwargs: Any) -> _Finished:
        await asyncio.sleep(0.35)
        return _Finished("buffered")

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    result = await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "buffered"


@pytest.mark.asyncio
async def test_non_streaming_backstop_still_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_timeouts(stall="5", backstop="0.15")

    async def _fake_acompletion(**_kwargs: Any) -> _Finished:
        await asyncio.sleep(2)
        return _Finished("too late")

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    with pytest.raises(LLMTimeoutError) as raised:
        await completion(
            model="test/mock",
            messages=[{"role": "user", "content": "hi"}],
            extra_body='{"stream": false}',
        )
    assert raised.value.kind == "backstop"
    assert raised.value.timeout_seconds == 0.15


@pytest.mark.asyncio
async def test_provider_timeout_is_a_backstop_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_timeouts(stall="5", backstop="9")

    async def _fake_acompletion(**_kwargs: Any) -> _Finished:
        import litellm

        raise litellm.Timeout(message="provider timed out", model="test/mock", llm_provider="openai")

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    with pytest.raises(LLMTimeoutError) as raised:
        await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert raised.value.kind == "backstop"
    assert raised.value.timeout_seconds == 9


@pytest.mark.asyncio
async def test_non_positive_stall_does_not_cancel_an_idle_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_timeouts(stall="0", backstop="2")
    stream = ScriptedStream([(0.2, _chunk("later", role="assistant", finish="stop"))])

    async def _fake_acompletion(**_kwargs: Any) -> ScriptedStream:
        return stream

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    result = await completion(model="test/mock", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "later"


@pytest.mark.asyncio
async def test_stall_timeout_repairs_then_can_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_timeouts(stall="0.15", backstop="5")
    db.set_setting("decision_repair_attempts", "1", "llm")
    config.reload()
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    calls = {"n": 0}
    sent: list[list[dict[str, str]]] = []

    async def _fake_acompletion(**kwargs: Any) -> ScriptedStream:
        calls["n"] += 1
        sent.append(list(kwargs.get("messages") or []))
        if calls["n"] == 1:
            return ScriptedStream([(1.0, _chunk("late", role="assistant"))])
        return ScriptedStream([(0.0, _chunk(_OK, role="assistant", finish="stop"))])

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
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
    assert calls["n"] == 2
    repair = "\n".join(item.get("content") or "" for item in sent[1])
    assert "timed out" in repair
    assert "with no progress" in repair
    assert "one JSON object" in repair
    assert outcome.result.get("event") == "decision_applied"
    assert outcome.result.get("llm_timeout") is not True
    assert _note_count(task.id) == 0
    assert db.get_task(task.id).status == "active"


@pytest.mark.asyncio
async def test_stall_timeout_fail_close_notes_and_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_timeouts(stall="0.15", backstop="5")
    db.set_setting("decision_repair_attempts", "0", "llm")
    config.reload()
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)

    async def _fake_acompletion(**_kwargs: Any) -> ScriptedStream:
        return ScriptedStream([(1.0, _chunk("late", role="assistant"))])

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
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
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("llm_timeout") is True
    detail = str(outcome.result.get("detail") or "")
    assert "no progress" in detail
    assert "one JSON object" in detail
    assert "Done" in detail
    assert _note_count(task.id) == 1
    assert any(
        item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
        for item in outcome.result.get("trigger_requests") or []
    )
    assert db.get_task(task.id).status == "active"
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
