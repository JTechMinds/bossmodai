"""BossMod AI — Unified LLM client via litellm.

Provides an async interface to any LLM provider (OpenAI, Anthropic,
Ollama, etc.) through litellm's unified API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import litellm

from core import config
from core.llm.attachment_parts import expand_attachment_messages
from core.llm.call_budget import budget, current_turn_lane

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging.
# litellm's built-in retry stays off: it retries immediately, and it also
# retries permanent 4xx errors, which starves the serve loop. Transient
# provider failures retry in ``completion()`` below, with backoff. Content
# problems (prose, broken JSON) are not retried here; turn repair owns them.
litellm.suppress_debug_info = True
litellm.num_retries = 0


def validate_api_base(url: str) -> str:
    """Validate an explicit API base URL for runtime requests."""
    clean = url.rstrip("/")
    if clean.endswith("/chat/completions") or clean.endswith("/completions"):
        raise LLMError("api_base must be the provider base URL, not a completions endpoint")
    return clean


def canonicalize_openai_compatible_model(model: str, api_base: str | None = None) -> str:
    """Normalize model names for OpenAI-compatible custom endpoints."""
    if api_base and "/" not in model:
        return f"openai/{model}"
    return model


@dataclass
class LLMResponse:
    """Structured result from an LLM call."""
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # The provider's stop reason for the last choice, when it reported one.
    finish_reason: str | None = None


_STREAM_END = object()

# Failure classes that say nothing about the request itself: a dropped or
# refused connection, a 5xx, or a rate limit. Matched by class, never by
# message text, so any engine behind litellm is covered. BadRequestError
# (context-window errors included), AuthenticationError, NotFoundError and
# PermissionDeniedError are permanent and stay out of this tuple.
_TRANSIENT_PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.BadGatewayError,
    litellm.exceptions.RateLimitError,
)


def _stall_timeout_seconds() -> float | None:
    """Return the idle-silence limit, or ``None`` when stall detection is off.

    A non-positive value disables the stall timer. The absolute backstop
    still applies. Missing or non-numeric values are a config error.
    """
    value = config.require_float("llm_stall_timeout_seconds")
    if value <= 0:
        return None
    return value


def _streaming_disabled(extra_body: Any) -> bool:
    """Return whether this provider path explicitly refuses a stream.

    Without chunks there is no progress signal. Those calls use only the
    absolute backstop and must not be cut off by the stall window.
    """
    if not isinstance(extra_body, dict):
        return False
    flag = extra_body.get("stream")
    if isinstance(flag, str):
        return flag.strip().lower() in {"0", "false", "no"}
    return flag is False


def _is_async_stream(value: Any) -> bool:
    return hasattr(value, "__aiter__")


def _is_provider_timeout(exc: BaseException) -> bool:
    if isinstance(exc, litellm.Timeout):
        return True
    return type(exc).__name__ in {
        "APITimeoutError",
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }


def _is_transient_provider_error(exc: BaseException) -> bool:
    """Return whether ``exc`` is a provider failure worth another attempt.

    A provider timeout is excluded first: ``litellm.Timeout`` subclasses
    ``APIConnectionError``, and timeouts keep their own repair path. An empty
    completion counts as transient (a dropped stream looks like one).
    """
    if _is_provider_timeout(exc):
        return False
    if isinstance(exc, LLMEmptyResponseError):
        return True
    return isinstance(exc, _TRANSIENT_PROVIDER_ERRORS)


def _transient_retry_policy() -> tuple[int, float]:
    """Return ``(retries, first_backoff_seconds)`` for transient failures.

    Raises
    ------
    ConfigError
        If either setting is missing, not numeric, or negative.
    """
    retries = config.require_int("llm_transient_retries")
    if retries < 0:
        raise config.ConfigError(
            f"Required setting 'llm_transient_retries' must not be negative: {retries!r}"
        )
    backoff = config.require_float("llm_transient_retry_backoff_seconds")
    if backoff < 0:
        raise config.ConfigError(
            "Required setting 'llm_transient_retry_backoff_seconds' must not be negative: "
            f"{backoff!r}"
        )
    return retries, backoff


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _usage_counts(usage: Any) -> tuple[int, int, int] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion_tokens))
        return prompt, completion_tokens, total
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or (prompt + completion_tokens))
    return prompt, completion_tokens, total


def _message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        return _as_text(message.get("content"))
    return _as_text(getattr(message, "content", None))


def _choice_finish_reason(choice: Any) -> str | None:
    value = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
    return value if isinstance(value, str) and value else None


def _response_from_model(response: Any, model: str) -> LLMResponse:
    if isinstance(response, dict):
        choice = response["choices"][0]
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
        usage = _usage_counts(response.get("usage"))
        response_model = response.get("model")
    else:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        usage = _usage_counts(getattr(response, "usage", None))
        response_model = getattr(response, "model", None)
    prompt_tokens, completion_tokens, total_tokens = usage or (0, 0, 0)
    return LLMResponse(
        content=_message_content(message),
        model=_as_text(response_model) or model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=_choice_finish_reason(choice),
    )


def _delta_content(chunk: Any) -> str:
    choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
    if delta is None:
        return ""
    if isinstance(delta, dict):
        return _as_text(delta.get("content"))
    return _as_text(getattr(delta, "content", None))


def _chunk_finish_reason(chunk: Any) -> str | None:
    choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
    if not choices:
        return None
    return _choice_finish_reason(choices[0])


def _chunk_model(chunk: Any) -> str:
    if isinstance(chunk, dict):
        return _as_text(chunk.get("model"))
    return _as_text(getattr(chunk, "model", None))


def _chunk_usage(chunk: Any) -> tuple[int, int, int] | None:
    usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
    return _usage_counts(usage)


def _response_from_chunks(chunks: list[Any], messages: list[dict[str, Any]], model: str) -> LLMResponse:
    """Rebuild one completion from streamed chunks.

    litellm's builder keeps usage accounting. A chunk shape it cannot rebuild
    still returns the text that arrived, which is what the turn parses.
    """
    if chunks:
        try:
            built = litellm.stream_chunk_builder(chunks, messages=messages)
        except Exception:
            logger.debug("stream_chunk_builder failed; joining chunk text", exc_info=True)
            built = None
        if built is not None:
            response = _response_from_model(built, model)
            # Keep the builder's usage when it has the text. If it drops the
            # text the chunks actually carried, join those chunks instead.
            if response.content or not any(_delta_content(chunk) for chunk in chunks):
                return response

    parts: list[str] = []
    response_model = model
    usage: tuple[int, int, int] | None = None
    finish_reason: str | None = None
    for chunk in chunks:
        text = _delta_content(chunk)
        if text:
            parts.append(text)
        chunk_model = _chunk_model(chunk)
        if chunk_model:
            response_model = chunk_model
        chunk_usage = _chunk_usage(chunk)
        if chunk_usage is not None:
            usage = chunk_usage
        chunk_finish = _chunk_finish_reason(chunk)
        if chunk_finish is not None:
            finish_reason = chunk_finish
    prompt_tokens, completion_tokens, total_tokens = usage or (0, 0, 0)
    return LLMResponse(
        content="".join(parts),
        model=response_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
    )


async def _next_chunk(iterator: Any) -> Any:
    """Read one chunk, converting stream end into a sentinel.

    ``asyncio.wait_for`` runs the read as a task. ``StopAsyncIteration``
    raised directly from that task is not a reliable end-of-stream signal.
    """
    try:
        return await iterator.__anext__()
    except StopAsyncIteration:
        return _STREAM_END


async def _close_stream(stream: Any) -> None:
    close = getattr(stream, "aclose", None)
    if close is None:
        close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=5)
    except Exception:
        logger.debug("Failed to close LLM stream", exc_info=True)


async def _read_stream(
    stream: Any,
    *,
    messages: list[dict[str, Any]],
    model: str,
    started: float,
    backstop_seconds: float,
    stall_seconds: float | None,
) -> LLMResponse:
    """Collect chunks until the stream ends, goes idle, or hits the backstop.

    Any chunk is progress, including an empty keepalive. The stall timer
    resets on each one. Wall-clock time alone does not cancel the read
    before ``backstop_seconds``.
    """
    iterator = stream.__aiter__()
    chunks: list[Any] = []
    while True:
        remaining = backstop_seconds - (time.monotonic() - started)
        if remaining <= 0:
            logger.error("LLM call timed out (model=%s) after %ss", model, backstop_seconds)
            raise LLMTimeoutError(backstop_seconds, kind="backstop")
        if stall_seconds is None:
            chunk_timeout = remaining
            stall_applies = False
        else:
            stall_applies = remaining > stall_seconds
            chunk_timeout = stall_seconds if stall_applies else remaining
        try:
            chunk = await asyncio.wait_for(_next_chunk(iterator), timeout=chunk_timeout)
        except asyncio.TimeoutError as exc:
            if stall_applies:
                logger.error(
                    "LLM call stalled (model=%s) after %ss with no progress",
                    model,
                    stall_seconds,
                )
                raise LLMTimeoutError(stall_seconds, kind="stall") from exc
            logger.error("LLM call timed out (model=%s) after %ss", model, backstop_seconds)
            raise LLMTimeoutError(backstop_seconds, kind="backstop") from exc
        if chunk is _STREAM_END:
            break
        chunks.append(chunk)
    return _response_from_chunks(chunks, messages, model)


async def _attempt_completion(
    kwargs: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    model: str,
    backstop_seconds: float,
    stall_seconds: float | None,
    stream_for_progress: bool,
) -> LLMResponse:
    """Make one provider call and return its completion.

    Each attempt gets its own ``backstop_seconds`` wall clock and stall window.

    Raises
    ------
    LLMTimeoutError
        If opening the call or reading the stream hits the stall or backstop limit.
    LLMEmptyResponseError
        If the provider returned a completion with no content.
    Exception
        Any provider exception from litellm, unchanged; ``completion()``
        classifies it.
    """
    started = time.monotonic()
    try:
        opened = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=backstop_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.error("LLM call timed out (model=%s) after %ss", model, backstop_seconds)
        raise LLMTimeoutError(backstop_seconds, kind="backstop") from exc
    if stream_for_progress and _is_async_stream(opened):
        try:
            response = await _read_stream(
                opened,
                messages=messages,
                model=model,
                started=started,
                backstop_seconds=backstop_seconds,
                stall_seconds=stall_seconds,
            )
        finally:
            await _close_stream(opened)
    else:
        response = _response_from_model(opened, model)
    # An empty reply is never a model answer: it is a dropped stream or a
    # reasoning model that spent its whole budget thinking. Returning it
    # would let a turn read it as prose.
    if response.content.strip() == "":
        raise LLMEmptyResponseError(
            f"provider returned an empty completion (finish_reason={response.finish_reason})"
        )
    return response


async def completion(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    extra_body: str | None = None,
) -> LLMResponse:
    """Call an LLM via litellm and return a structured response.

    Streaming is turned on so stall detection can see chunks. Each chunk
    resets ``llm_stall_timeout_seconds`` (default 120). The call is cancelled
    for wall-clock time only at ``llm_request_timeout_seconds`` (default 720).
    A provider path that sets ``extra_body`` ``{"stream": false}``, or that
    returns a finished completion instead of a stream, has no progress signal
    and uses only that absolute backstop.

    A transient provider failure (connection error, 5xx, rate limit, or an
    empty completion) is retried up to ``llm_transient_retries`` times. The
    first wait is ``llm_transient_retry_backoff_seconds`` and doubles on each
    retry. The call-budget slot is held across retries: a retry is the same
    logical call. Timeouts and permanent errors are not retried here.

    Parameters
    ----------
    model : str
        Model identifier passed directly to litellm.
    messages : list
        Chat messages in OpenAI format (role + content). A message may name
        attachments under ``attachment_parts.ATTACHMENT_IDS_KEY``; they are
        expanded into content parts for this ``model`` before the call.
    temperature : float | None
        Sampling temperature. Read from settings if not provided.
    max_tokens : int | None
        Maximum response tokens. Read from settings if not provided.
    api_base : str | None
        Override API base URL (for self-hosted models).
    api_key : str | None
        Override API key (per-agent keys).
    extra_body : str | None
        JSON string of extra fields to merge into the request body.
        Used for provider-specific params like ``{"stream": false}``.

    Raises
    ------
    LLMError
        If the LLM call fails with a permanent error, or every transient retry
        failed. The message names the number of attempts.
    LLMEmptyResponseError
        If every attempt returned an empty completion.
    LLMTimeoutError
        If the stream is idle for the stall window, or the absolute backstop expires.
    AttachmentUnavailableError
        If a named attachment's row or file is missing.
    ConfigError
        If a timeout or transient-retry setting is missing, invalid, or negative.
    """
    # The raw model name is the image-capability key, so expand before the
    # provider prefix is added; both the call and the stream rebuild below
    # see the expanded list.
    messages = expand_attachment_messages(messages, model=model)

    if temperature is None:
        temperature = config.get_float("default_temperature") or 0.7
    if max_tokens is None:
        max_tokens = config.get_int("default_max_tokens") or 16384

    kwargs: dict[str, Any] = {
        "model": canonicalize_openai_compatible_model(model, api_base=api_base),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if api_base:
        kwargs["api_base"] = validate_api_base(api_base)
        kwargs["api_key"] = api_key or "local-openai-compatible"
    elif api_key:
        kwargs["api_key"] = api_key

    # Merge provider-specific body params (e.g. {"stream": false, "thinking": ...})
    if extra_body:
        try:
            import json
            parsed_extra = json.loads(extra_body)
            if isinstance(parsed_extra, dict):
                kwargs["extra_body"] = parsed_extra
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid extra_body JSON, ignoring: %s", extra_body[:200])

    backstop_seconds = config.require_float("llm_request_timeout_seconds")
    stall_seconds = _stall_timeout_seconds()
    transient_retries, retry_backoff_seconds = _transient_retry_policy()
    max_attempts = 1 + transient_retries
    # litellm defaults its own client timeout to 600s. Pin it to the absolute
    # backstop so a live call is not cut off short of that limit. Idle silence
    # on a stream is enforced separately by the stall timer.
    kwargs["timeout"] = backstop_seconds
    kwargs["num_retries"] = 0
    kwargs["max_retries"] = 0
    stream_for_progress = not _streaming_disabled(kwargs.get("extra_body"))
    if stream_for_progress:
        kwargs["stream"] = True

    logger.info(
        "LLM request: model=%s, api_base=%s, extra_body=%s, stream=%s",
        kwargs.get("model"),
        kwargs.get("api_base"),
        kwargs.get("extra_body"),
        bool(kwargs.get("stream")),
    )

    # A turn that already claimed a lane — including its repair calls — reuses
    # that lane. Any other caller waits for the same budget.
    held = current_turn_lane()
    owned = None
    if held is None:
        owned = await budget.acquire(kind="call", owner=str(model or "llm-call"))
    try:
        # The backstop bounds each model attempt, not the wait for a free slot
        # or the backoff between attempts.
        attempt = 1
        while True:
            try:
                return await _attempt_completion(
                    kwargs,
                    messages=messages,
                    model=model,
                    backstop_seconds=backstop_seconds,
                    stall_seconds=stall_seconds,
                    stream_for_progress=stream_for_progress,
                )
            except LLMTimeoutError:
                raise
            except Exception as exc:
                if _is_provider_timeout(exc):
                    logger.error("LLM call timed out (model=%s) after %ss", model, backstop_seconds)
                    raise LLMTimeoutError(backstop_seconds, kind="backstop") from exc
                if attempt < max_attempts and _is_transient_provider_error(exc):
                    delay = retry_backoff_seconds * 2 ** (attempt - 1)
                    logger.warning(
                        "LLM call failed transiently (model=%s, attempt %d/%d); retrying in %.1fs: %s",
                        model,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    # Drop the broken connection so the retry opens a fresh one.
                    await close_provider_sessions(allow_inflight=1)
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                logger.error("LLM call failed (model=%s): %s", model, exc)
                # Keep the empty-completion type so callers can tell it apart;
                # it is still an LLMError, never a reply.
                error_type = LLMEmptyResponseError if isinstance(exc, LLMEmptyResponseError) else LLMError
                raise error_type(f"LLM call failed after {attempt} attempt(s): {exc}") from exc
    finally:
        if owned is not None:
            budget.release(owned)
        await close_provider_sessions(allow_inflight=1)


async def close_provider_sessions(*, allow_inflight: int = 0) -> None:
    """Close cached aiohttp and httpx clients left by litellm.

    Turn end, cancel, and timeout all come through here. A sibling model
    call keeps its session until it finishes; the last caller closes the cache.
    """
    try:
        inflight = budget.inflight()
    except Exception:
        inflight = 0
    if inflight > allow_inflight:
        return
    closer = getattr(litellm, "close_litellm_async_clients", None)
    if closer is None:
        return
    try:
        result = closer()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("provider session close failed", exc_info=True)


def count_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count for a string using litellm's tokenizer.

    Returns ``0`` when no explicit tokenizer model is configured or supported.
    """
    effective_model = model
    if not effective_model:
        logger.warning("Token counting skipped because no tokenizer model is configured")
        return 0
    try:
        return litellm.token_counter(model=effective_model, text=text)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("Token counting skipped for unsupported model %s: %s", effective_model, exc)
        return 0


class LLMError(Exception):
    """Raised when an LLM call fails."""


class LLMEmptyResponseError(LLMError):
    """Raised when the provider returned a completion with no content.

    Either the stream dropped before any text arrived, or a reasoning model
    spent its whole budget on thinking. It is a provider failure, never a
    prose reply, and it is retried like other transient provider errors. It
    deliberately does not subclass :class:`LLMTimeoutError`: decision-turn
    timeout repair stays timeout-only.
    """


class LLMTimeoutError(LLMError):
    """Raised when one model call stalls or hits the absolute backstop.

    ``kind`` is ``"stall"`` when an open stream produced no chunk for
    ``llm_stall_timeout_seconds``, and ``"backstop"`` when the call reached
    ``llm_request_timeout_seconds``.
    """

    def __init__(self, timeout_seconds: float, *, kind: str = "backstop") -> None:
        self.timeout_seconds = timeout_seconds
        self.kind = kind if kind in {"stall", "backstop"} else "backstop"
        if self.kind == "stall":
            message = f"LLM call stalled after {timeout_seconds:g}s with no progress"
        else:
            message = f"LLM call timed out after {timeout_seconds:g}s"
        super().__init__(message)
