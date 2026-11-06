"""Transient provider failures retry in the client, with backoff.

Timeouts and permanent errors are not retried here. An empty completion is
a provider failure, never a reply. A request whose images the provider
refuses is re-sent once without them, which is not a retry.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import litellm
import pytest

import db
from core import config
from core.bm_cli.policy_engine import policy_engine
from core.llm import client
from core.llm.attachment_parts import IMAGE_NOT_SUPPORTED_TEXT
from core.llm.call_budget import budget
from core.llm.client import (
    LLMEmptyResponseError,
    LLMError,
    LLMResponse,
    LLMTimeoutError,
    completion,
)
from core.runtime.events import NullRuntimeEventSink, runtime_events
from db.settings import get_seed_setting_default

_MESSAGES = [{"role": "user", "content": "hi"}]
# Already expanded: no private keys, so expansion passes it through unchanged.
_IMAGE_MESSAGES = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    },
]
_TEXT_ONLY = "messages.content.type is invalid, allowed values: ['text']"


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
    result = await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
    assert "after 1 attempt(s)" in str(raised.value)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_provider_timeout_is_not_retried_here(monkeypatch: pytest.MonkeyPatch) -> None:
    error = litellm.Timeout(message="provider timed out", model="test/mock", llm_provider="openai")
    calls = _script(monkeypatch, [error])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMTimeoutError):
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_zero_retries_makes_a_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    _set("llm_transient_retries", "0")
    calls = _script(monkeypatch, [_mid_stream()])
    sleeps = _record_sleeps(monkeypatch)
    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
    result = await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
    assert result.content == "ok"
    assert len(acquired) == 1
    assert released == acquired


@pytest.mark.asyncio
async def test_empty_stream_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _script(monkeypatch, [_empty_stream(), _ok_stream()])
    sleeps = _record_sleeps(monkeypatch)
    result = await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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
        await completion(model="test/mock", messages=_MESSAGES, connection_id="test-connection")
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


# ─── A refused image: one re-send without it ───


class _ActivitySink(NullRuntimeEventSink):
    """Records activity broadcasts; every other runtime event is dropped."""

    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.activities.append({"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})


@pytest.fixture
def activities() -> Iterator[list[dict[str, Any]]]:
    sink = _ActivitySink()
    runtime_events.set_sink(sink)
    try:
        yield sink.activities
    finally:
        runtime_events.set_sink(NullRuntimeEventSink())


def _connection() -> str:
    """A real connection row: a failure is recorded only for a connection that exists."""
    return db.create_connection(
        name="Vision", api_base_url="http://127.0.0.1:9/v1", api_key="sk-test", model="test/mock",
    ).id


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        litellm.exceptions.BadRequestError(message=_TEXT_ONLY, model="test/mock", llm_provider="openai"),
        litellm.exceptions.UnprocessableEntityError(
            message=_TEXT_ONLY, model="test/mock", llm_provider="openai", response=_response(422),
        ),
    ],
    ids=["400", "422"],
)
async def test_a_refused_image_is_replaced_by_a_notice_and_sent_once_more(
    monkeypatch: pytest.MonkeyPatch, activities: list[dict[str, Any]], refusal: Exception,
) -> None:
    connection_id = _connection()
    calls = _script(monkeypatch, [refusal, _ok_stream()])
    sleeps = _record_sleeps(monkeypatch)

    result = await completion(model="test/mock", messages=_IMAGE_MESSAGES, connection_id=connection_id)

    assert result.content == "ok"
    assert len(calls) == 2
    assert calls[0]["messages"] == _IMAGE_MESSAGES
    assert calls[1]["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "look"}, {"type": "text", "text": IMAGE_NOT_SUPPORTED_TEXT}],
        },
    ]
    # A different request, not a transient retry: no backoff.
    assert sleeps == []
    # The call succeeded, so nothing counts against the connection.
    assert db.get_connection_health(connection_id) is None
    assert activities == [
        {
            "event": "model_images_refused",
            "detail": (
                "“test/mock” didn't accept an image, so it was sent without it. If this model "
                "can't read images, turn off “Supports images” for it in Settings → AI Connections."
            ),
            "agent_name": None,
            "extra": {"model": "test/mock", "connection_id": connection_id},
        },
    ]
    assert _IMAGE_MESSAGES[0]["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_a_resend_refused_too_raises_and_records_its_own_error(
    monkeypatch: pytest.MonkeyPatch, activities: list[dict[str, Any]],
) -> None:
    connection_id = _connection()
    second = litellm.exceptions.BadRequestError(
        message="refused without images too", model="test/mock", llm_provider="openai",
    )
    calls = _script(
        monkeypatch,
        [litellm.exceptions.BadRequestError(message=_TEXT_ONLY, model="test/mock", llm_provider="openai"), second],
    )
    sleeps = _record_sleeps(monkeypatch)

    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_IMAGE_MESSAGES, connection_id=connection_id)

    assert raised.value.__cause__ is second
    assert raised.value.connection_failure == "rejected"
    assert len(calls) == 2
    assert IMAGE_NOT_SUPPORTED_TEXT in str(calls[1]["messages"])
    assert sleeps == []
    assert activities == []
    # The images were not the cause: the re-send's failure is what counts.
    health = db.get_connection_health(connection_id)
    assert health.failure_count == 1
    assert "refused without images too" in health.last_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        litellm.exceptions.ContextWindowExceededError(message="too long", model="test/mock", llm_provider="openai"),
        litellm.exceptions.ContentPolicyViolationError(message="policy", model="test/mock", llm_provider="openai"),
    ],
    ids=["context_window", "content_policy"],
)
async def test_a_context_window_or_content_policy_error_with_an_image_is_not_resent(
    monkeypatch: pytest.MonkeyPatch, activities: list[dict[str, Any]], error: Exception,
) -> None:
    calls = _script(monkeypatch, [error])
    sleeps = _record_sleeps(monkeypatch)

    with pytest.raises(LLMError) as raised:
        await completion(model="test/mock", messages=_IMAGE_MESSAGES, connection_id="test-connection")

    assert raised.value.__cause__ is error
    assert len(calls) == 1
    assert sleeps == []
    assert activities == []
