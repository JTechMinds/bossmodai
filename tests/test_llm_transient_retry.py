"""Transient provider failures retry in the client, with backoff.

Timeouts and permanent errors are not retried here. An empty completion is
a provider failure, never a reply.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import litellm
import pytest

import db
from core import config
from core.bm_cli.policy_engine import policy_engine
from core.llm import client
from core.llm.call_budget import budget
from core.llm.client import (
    LLMEmptyResponseError,
    LLMError,
    LLMResponse,
    LLMTimeoutError,
    completion,
)
from db.settings import get_seed_setting_default

_MESSAGES = [{"role": "user", "content": "hi"}]


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


def _set(key: str, value: str) -> None:
    db.set_setting(key, value, "llm")
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


class _Stream:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def aclose(self) -> None:
        return None


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)
        self.finish_reason = "stop"


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


def _ok_stream() -> _Stream:
    return _Stream([_chunk("ok", role="assistant"), _chunk("", finish="stop")])


def _empty_stream(finish: str = "length") -> _Stream:
    return _Stream([_chunk("", role="assistant"), _chunk("", finish=finish)])


def _mid_stream() -> Exception:
    return litellm.exceptions.MidStreamFallbackError(
        message="malformed tool call", model="test/mock", llm_provider="openai"
    )


def _script(monkeypatch: pytest.MonkeyPatch, items: list[Any]) -> list[dict[str, Any]]:
    """Each provider call takes the next item: raise it if it is an exception."""
    queue = list(items)
    calls: list[dict[str, Any]] = []

    async def _fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if not queue:
            raise AssertionError("unexpected extra provider call")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    return calls


def _record_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def _fake_sleep(delay: float, *_args: Any, **_kwargs: Any) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("core.llm.client.asyncio.sleep", _fake_sleep)
    return sleeps


def test_transient_retry_settings_are_seeded() -> None:
    assert get_seed_setting_default("llm_transient_retries") == ("2", "llm")
    assert get_seed_setting_default("llm_transient_retry_backoff_seconds") == ("2", "llm")
    assert config.get("llm_transient_retries") == "2"
    assert config.get("llm_transient_retry_backoff_seconds") == "2"


@pytest.mark.asyncio
async def test_mid_stream_failure_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _script(monkeypatch, [_mid_stream(), _ok_stream()])
    sleeps = _record_sleeps(monkeypatch)
    closes: list[int] = []

    async def _fake_close(*, allow_inflight: int = 0) -> None:
        closes.append(allow_inflight)

    monkeypatch.setattr("core.llm.client.close_provider_sessions", _fake_close)
    result = await completion(model="test/mock", messages=_MESSAGES)
    assert isinstance(result, LLMResponse)
    assert result.content == "ok"
    assert len(calls) == 2
    assert sleeps == [2.0]
    # One close drops the broken connection before the retry; one is the call's own cleanup.
    assert closes == [1, 1]


@pytest.mark.asyncio
async def test_transient_failure_on_every_attempt_raises_after_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = [
        litellm.exceptions.InternalServerError(message="boom", llm_provider="openai", model="test/mock")
        for _ in range(3)
    ]
    calls = _script(monkeypatch, errors)
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_MESSAGES)
    assert "after 3 attempt(s)" in str(raised.value)
    assert not isinstance(raised.value, LLMTimeoutError)
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        litellm.exceptions.BadRequestError(message="bad", model="test/mock", llm_provider="openai"),
        litellm.exceptions.AuthenticationError(message="no key", llm_provider="openai", model="test/mock"),
    ],
    ids=["bad_request", "authentication"],
)
async def test_permanent_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    calls = _script(monkeypatch, [error])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_MESSAGES)
    assert "after 1 attempt(s)" in str(raised.value)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_provider_timeout_is_not_retried_here(monkeypatch: pytest.MonkeyPatch) -> None:
    error = litellm.Timeout(message="provider timed out", model="test/mock", llm_provider="openai")
    calls = _script(monkeypatch, [error])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMTimeoutError):
        await completion(model="test/mock", messages=_MESSAGES)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_zero_retries_makes_a_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    _set("llm_transient_retries", "0")
    calls = _script(monkeypatch, [_mid_stream()])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_MESSAGES)
    assert "after 1 attempt(s)" in str(raised.value)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["llm_transient_retries", "llm_transient_retry_backoff_seconds"])
async def test_negative_retry_settings_are_a_config_error(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    _set(key, "-1")
    calls = _script(monkeypatch, [_ok_stream()])
    with pytest.raises(config.ConfigError):
        await completion(model="test/mock", messages=_MESSAGES)
    assert calls == []


@pytest.mark.asyncio
async def test_call_budget_is_held_once_across_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(monkeypatch, [_mid_stream(), _mid_stream(), _ok_stream()])
    _record_sleeps(monkeypatch)
    acquired: list[Any] = []
    released: list[Any] = []
    real_acquire = budget.acquire
    real_release = budget.release

    async def _acquire(**kwargs: Any) -> Any:
        lane = await real_acquire(**kwargs)
        acquired.append(lane)
        return lane

    def _release(lane: Any) -> None:
        released.append(lane)
        real_release(lane)

    monkeypatch.setattr(budget, "acquire", _acquire)
    monkeypatch.setattr(budget, "release", _release)
    result = await completion(model="test/mock", messages=_MESSAGES)
    assert result.content == "ok"
    assert len(acquired) == 1
    assert released == acquired


@pytest.mark.asyncio
async def test_empty_stream_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _script(monkeypatch, [_empty_stream(), _ok_stream()])
    sleeps = _record_sleeps(monkeypatch)
    result = await completion(model="test/mock", messages=_MESSAGES)
    assert result.content == "ok"
    assert len(calls) == 2
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_empty_on_every_attempt_raises_empty_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _script(monkeypatch, [_empty_stream(), _empty_stream(), _empty_stream()])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMEmptyResponseError) as raised:
        await completion(model="test/mock", messages=_MESSAGES)
    message = str(raised.value)
    assert "after 3 attempt(s)" in message
    assert "finish_reason=length" in message
    # A provider failure, not a timeout: decision-turn timeout repair must not see it.
    assert isinstance(raised.value, LLMError)
    assert not isinstance(raised.value, LLMTimeoutError)
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.asyncio
async def test_whitespace_only_content_counts_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set("llm_transient_retries", "0")
    calls = _script(monkeypatch, [_Finished("  \n\t ")])
    with pytest.raises(LLMEmptyResponseError) as raised:
        await completion(model="test/mock", messages=_MESSAGES)
    assert "finish_reason=stop" in str(raised.value)
    assert len(calls) == 1


def test_transient_classification_excludes_timeouts_and_permanent_errors() -> None:
    assert client._is_transient_provider_error(_mid_stream())
    assert client._is_transient_provider_error(LLMEmptyResponseError("empty"))
    assert not client._is_transient_provider_error(
        litellm.Timeout(message="t", model="test/mock", llm_provider="openai")
    )
    assert not client._is_transient_provider_error(
        litellm.exceptions.ContextWindowExceededError(message="too long", model="test/mock", llm_provider="openai")
    )
