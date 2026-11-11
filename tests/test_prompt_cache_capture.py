"""Capture: every model call says how many prompt tokens its provider served from cache.

Through the real installed litellm, with no mocks between ``client.completion``
and the wire: a local OpenAI-compatible server answers streamed and
non-streamed calls, and the client must read the provider's own counts. A
streamed call to a custom endpoint must ask for usage
(``stream_options.include_usage``); without it litellm drops the provider's
usage chunk and estimates the tokens itself.

"Not reported" is not "0% cached": an absent or null figure is ``None``, a 0 is
0, and a figure over the call's prompt tokens is dropped with a warning.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.extensions.model_provider import ProviderCompletion
from core.llm.client import LLMResponse, completion
from core.llm.prompt_cache_usage import PromptCacheUsage

_MESSAGES = [{"role": "user", "content": "hi"}]
_REPLY = "Hello there"
_USAGE = {"prompt_tokens": 1000, "completion_tokens": 7, "total_tokens": 1007}


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    # One attempt per call, so each call is exactly one recorded request.
    db.set_setting("llm_transient_retries", "0", "llm")
    config.reload()


def teardown_function() -> None:
    db.close_connection()


class _ChatCompletions(BaseHTTPRequestHandler):
    """``POST /v1/chat/completions``: SSE when the body asks to stream, JSON otherwise.

    The server's ``usage`` attribute is the usage block every answer carries;
    each request body is recorded on ``requests``.
    """

    server: _Endpoint

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if body.get("stream"):
            self._stream()
        else:
            self._finished()

    def _stream(self) -> None:
        def chunk(choices: list[dict[str, Any]], **extra: Any) -> bytes:
            payload = {
                "id": "chatcmpl-cache", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model", "choices": choices, **extra,
            }
            return f"data: {json.dumps(payload)}\n\n".encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(chunk([{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]))
        self.wfile.write(chunk([{"index": 0, "delta": {"content": " there"}, "finish_reason": None}]))
        self.wfile.write(chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}]))
        # The usage chunk OpenAI's stream_options.include_usage asks for: no choices.
        self.wfile.write(chunk([], usage=self.server.usage))
        self.wfile.write(b"data: [DONE]\n\n")

    def _finished(self) -> None:
        payload = json.dumps({
            "id": "chatcmpl-cache", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": _REPLY}, "finish_reason": "stop"}],
            "usage": self.server.usage,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        """Keep the test output clean."""


class _Endpoint(ThreadingHTTPServer):
    usage: dict[str, Any]
    requests: list[dict[str, Any]]


@pytest.fixture
def endpoint() -> Iterator[_Endpoint]:
    server = _Endpoint(("127.0.0.1", 0), _ChatCompletions)
    server.usage = dict(_USAGE)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


async def _call(server: _Endpoint, *, streamed: bool) -> LLMResponse:
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    connection = db.create_connection(name="Local cache", api_base_url=base, model="test-model")
    return await completion(
        model="test-model",
        messages=_MESSAGES,
        api_base=base,
        extra_body=None if streamed else '{"stream": false}',
        connection_id=connection.id,
    )


@pytest.mark.asyncio
async def test_a_streamed_call_asks_for_usage_and_reads_the_providers_own_counts(endpoint: _Endpoint) -> None:
    endpoint.usage = {**_USAGE, "prompt_tokens_details": {"cached_tokens": 640}}

    result = await _call(endpoint, streamed=True)

    (request,) = endpoint.requests
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert result.content == _REPLY
    # The provider's 1,000, not litellm's estimate of a two-word prompt.
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (1000, 7, 1007)
    assert result.cached_prompt_tokens == 640


@pytest.mark.asyncio
async def test_a_non_streamed_call_reads_the_same_figures_from_the_body(endpoint: _Endpoint) -> None:
    endpoint.usage = {**_USAGE, "prompt_tokens_details": {"cached_tokens": 640}}

    result = await _call(endpoint, streamed=False)

    (request,) = endpoint.requests
    assert request.get("stream") is False
    assert "stream_options" not in request
    assert (result.content, result.prompt_tokens, result.cached_prompt_tokens) == (_REPLY, 1000, 640)


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [True, False], ids=["streamed", "non-streamed"])
@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (None, None),
        ({"cached_tokens": None}, None),
        ({"cached_tokens": 0}, 0),
    ],
    ids=["details-absent", "cached-null", "cached-zero"],
)
async def test_absent_or_null_is_not_reported_and_zero_is_zero(
    endpoint: _Endpoint, streamed: bool, details: dict[str, Any] | None, expected: int | None,
) -> None:
    endpoint.usage = dict(_USAGE) if details is None else {**_USAGE, "prompt_tokens_details": details}

    result = await _call(endpoint, streamed=streamed)

    assert result.prompt_tokens == 1000
    assert result.cached_prompt_tokens == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [True, False], ids=["streamed", "non-streamed"])
async def test_a_figure_over_the_prompt_is_dropped_with_a_warning(
    endpoint: _Endpoint, streamed: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    endpoint.usage = {**_USAGE, "prompt_tokens_details": {"cached_tokens": 1200}}

    with caplog.at_level(logging.WARNING, logger="core.llm.client"):
        result = await _call(endpoint, streamed=streamed)

    assert (result.prompt_tokens, result.cached_prompt_tokens) == (1000, None)
    warnings = [record.getMessage() for record in caplog.records if record.name == "core.llm.client"]
    assert (
        "Provider reported 1200 cached prompt tokens for 1000 prompt tokens (model=test-model); "
        "recorded as not reported"
    ) in warnings


# ─── The extension provider path ───


class _StubProvider:
    """A model-provider extension that answers every request with one completion."""

    def __init__(self, answer: ProviderCompletion) -> None:
        self.answer = answer

    def provider_status(self) -> Any:
        raise AssertionError("not used")

    def provider_action(self, action: str, values: Any) -> None:
        raise AssertionError("not used")

    async def provider_complete(self, request: Any) -> ProviderCompletion:
        return self.answer

    def provider_complete_sync(self, request: Any) -> ProviderCompletion:
        raise AssertionError("not used")


async def _extension_call(monkeypatch: pytest.MonkeyPatch, answer: ProviderCompletion) -> LLMResponse:
    stub = _StubProvider(answer)
    monkeypatch.setattr("core.extensions.model_provider.resolve_provider", lambda ext_id: stub)
    return await completion(
        model="stub-model", messages=_MESSAGES, api_base="extension://stub-provider", connection_id="test-connection",
    )


@pytest.mark.asyncio
async def test_an_extension_providers_cached_figure_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _extension_call(
        monkeypatch, ProviderCompletion("ok", "stub-model", 50, 4, 54, cached_prompt_tokens=30),
    )

    assert (result.content, result.prompt_tokens, result.total_tokens) == ("ok", 50, 54)
    assert result.finish_reason == "stop"
    assert result.cached_prompt_tokens == 30


@pytest.mark.asyncio
async def test_an_extension_that_reports_nothing_is_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _extension_call(monkeypatch, ProviderCompletion("ok", "stub-model", 50, 4, 54))

    assert result.cached_prompt_tokens is None


@pytest.mark.asyncio
async def test_an_extension_figure_over_the_prompt_is_dropped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.llm.client"):
        result = await _extension_call(
            monkeypatch, ProviderCompletion("ok", "stub-model", 50, 4, 54, cached_prompt_tokens=51),
        )

    assert result.cached_prompt_tokens is None
    assert any(
        record.name == "core.llm.client" and "Provider reported 51 cached prompt tokens for 50" in record.getMessage()
        for record in caplog.records
    )


# ─── PromptCacheUsage ───


def _response(prompt: int, cached: int | None) -> LLMResponse:
    return LLMResponse(
        content="x", model="m", prompt_tokens=prompt, completion_tokens=1, total_tokens=prompt + 1,
        cached_prompt_tokens=cached,
    )


def test_of_a_call_that_reported_nothing_is_zero() -> None:
    assert PromptCacheUsage.of(_response(900, None)) == PromptCacheUsage.ZERO
    assert PromptCacheUsage.ZERO == PromptCacheUsage(cached_tokens=0, reported_prompt_tokens=0)


def test_of_a_call_that_reported_counts_its_prompt_as_reported() -> None:
    assert PromptCacheUsage.of(_response(900, 400)) == PromptCacheUsage(400, 900)
    # A reported 0 is reported: it adds its prompt tokens, so it reads 0%, not "not reported".
    assert PromptCacheUsage.of(_response(900, 0)) == PromptCacheUsage(0, 900)


def test_figures_add_up_call_by_call() -> None:
    total = PromptCacheUsage(400, 1000) + PromptCacheUsage.ZERO + PromptCacheUsage(300, 500)
    assert total == PromptCacheUsage(700, 1500)


@pytest.mark.parametrize(("cached", "reported"), [(-1, 10), (11, 10), (1, 0)])
def test_impossible_figures_are_refused(cached: int, reported: int) -> None:
    with pytest.raises(ValueError):
        PromptCacheUsage(cached, reported)
    with pytest.raises(ValueError):
        PromptCacheUsage.of(_response(reported, cached))
