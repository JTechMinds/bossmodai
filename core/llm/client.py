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
import openai

from core import config
from core.llm import connection_health
from core.llm.attachment_parts import expand_attachment_messages
from core.llm.call_budget import budget, current_turn_lane
from core.models import ConnectionFailureKind

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
    """Build the litellm model string that sends ``model`` to ``api_base`` unchanged.

    The configured model is the endpoint's exact id, as ``GET {base}/models``
    lists it and as the connection test compares it. Ids may contain ``/``,
    for example ``openai/gpt-5.6-sol`` or ``z-ai/glm-5.2``.

    The ``openai/`` prefix only selects litellm's OpenAI-compatible transport;
    it is not part of the id. litellm strips exactly one leading segment as the
    provider name, so exactly one prefix is always added and the id is never
    inspected. Skipping the prefix for an id that already contains ``/`` would
    let litellm eat the id's first segment.

    Args:
        model: The endpoint's model id, exactly as configured.
        api_base: The connection's base URL. When it is set, the endpoint is
            OpenAI-compatible.

    Returns:
        ``openai/{model}`` when ``api_base`` is set; otherwise ``model``
        unchanged, so litellm picks the provider from the name itself.
    """
    if api_base:
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


def classify_connection_failure(exc: BaseException) -> ConnectionFailureKind | None:
    """Say what a failed model call's exception means for its AI connection.

    Classified by exception class, never by message text, reusing the same
    transient/permanent split as the in-call retry:

    - ``"transient"``: could not connect, 5xx, 429, or an extension
      provider's plain ``ProviderError``.
    - ``"rejected"``: the provider refused the request: any other
      ``openai.APIError`` (400, 401, 403, 404, 422, and so on) or
      ``ProviderUsageLimitError``.
    - ``None``: a timeout or stall, an empty reply,
      ``ContextWindowExceededError`` or ``ContentPolicyViolationError``.
      These are about one request, and existing repair paths own them.
    - ``None``: anything that is not a provider answer (a BossMod bug, a
      local config error).

    Both counted kinds count toward pausing the connection
    (``core.llm.connection_health.record_failure``); ``None`` is not counted.

    Args:
        exc: The provider's own exception (not the ``LLMError`` that wraps it).

    Returns:
        ``"transient"``, ``"rejected"``, or ``None`` when the failure says
        nothing about the connection.
    """
    from core.extensions.model_provider import ProviderError, ProviderTimeout, ProviderUsageLimitError

    if (
        _is_provider_timeout(exc)
        or isinstance(exc, (LLMTimeoutError, LLMEmptyResponseError, ProviderTimeout))
    ):
        return None
    # Both subclass BadRequestError (litellm 1.82.5), so they must be checked
    # before the openai.APIError rule below.
    if isinstance(
        exc,
        (litellm.exceptions.ContextWindowExceededError, litellm.exceptions.ContentPolicyViolationError),
    ):
        return None
    if isinstance(exc, ProviderUsageLimitError):
        return "rejected"
    if isinstance(exc, ProviderError):
        return "transient"
    if isinstance(exc, _TRANSIENT_PROVIDER_ERRORS):
        return "transient"
    # Every litellm provider exception subclasses openai.APIError.
    if isinstance(exc, openai.APIError):
        return "rejected"
    return None


def _settle_failed_call(
    connection_id: str,
    admission: connection_health.Admission,
    failure: BaseException | None,
    kind: ConnectionFailureKind | None,
) -> None:
    """Record a failed call's verdict on its connection, or end an undecided test call.

    Args:
        connection_id: The connection the call was for.
        admission: The admission the call was given.
        failure: The provider's own exception when the call ended on one;
            ``None`` when it ended on a timeout or a cancellation.
        kind: ``classify_connection_failure(failure)``, computed once by the
            caller, which also sets it as the raised
            ``LLMError.connection_failure``: what is recorded here and what
            the dispatcher parks on cannot disagree. ``None`` when the
            failure says nothing about the connection; a test call that
            ends that way re-arms the wait (``end_undecided_test``).
    """
    if kind is not None:
        connection_health.record_failure(connection_id, kind=kind, error=str(failure))
    elif admission.probe:
        connection_health.end_undecided_test(connection_id, lease_until=admission.lease_until)


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
    *,
    connection_id: str,
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

    A paused AI connection is never contacted. Immediately before the
    provider call, ``connection_health.admit`` decides whether this call may
    go ahead; afterwards the outcome is recorded once, after the transient
    retries, so a short blip a retry absorbed never counts: a success
    resets the connection's failure count, and a failure the provider owns
    (``classify_connection_failure``) counts toward pausing it. That same
    kind is set as the raised ``LLMError.connection_failure``.

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
    connection_id : str
        The AI connection this call belongs to: the key of its pause state.
        Required, because a call no pause can stop is the bug this guards.

    Raises
    ------
    LLMConnectionSuspendedError
        If ``connection_id`` is paused after repeated provider errors and
        this call is not its test call. Raised before any provider contact.
    LLMError
        If the LLM call fails with a permanent error, or every transient retry
        failed. The message names the number of attempts.
    LLMUsageLimitError
        If an extension provider reports that the connection's account has no
        usage left (``ProviderUsageLimitError``). It is never retried.
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
    from db.managed_connections import routed_images
    messages = expand_attachment_messages(messages, model=model, image_capable=routed_images(model, api_base))

    if temperature is None:
        temperature = config.get_float("default_temperature") or 0.7
    if max_tokens is None:
        max_tokens = config.get_int("default_max_tokens") or 16384

    from dataclasses import asdict
    from core.extensions.model_provider import (
        ProviderError, ProviderRequest, ProviderTimeout, ProviderUsageLimitError,
        complete as complete_provider, provider_id, resolve_provider,
    )
    # The kind of the provider's failure, computed once: recorded on the
    # connection and carried on the LLMError converted below. It stays None
    # for a local error raised before the provider was contacted (an invalid
    # endpoint, a missing or disabled extension), which records nothing.
    failure_kind: ConnectionFailureKind | None = None
    try:
        ext_id = provider_id(api_base)
        if ext_id is not None:
            # Resolved before admission: an unknown or disabled extension is a
            # local error that contacted no provider, so it is not recorded.
            provider = resolve_provider(ext_id)
            request = ProviderRequest(model, messages, extra_body,
                                      config.require_float("llm_request_timeout_seconds"),
                                      config.require_float("llm_stall_timeout_seconds"),
                                      temperature, max_tokens)
            admission = connection_health.admit(connection_id, lease_seconds=request.timeout_seconds)
            if not admission.allowed:
                raise LLMConnectionSuspendedError(connection_id, last_error=admission.health.last_error)
            try:
                result = await complete_provider(provider, request)
            except BaseException as exc:
                failure_kind = classify_connection_failure(exc)
                _settle_failed_call(connection_id, admission, exc, failure_kind)
                raise
            connection_health.record_success(connection_id)
            return LLMResponse(**asdict(result))
    except ProviderTimeout as exc:
        raise LLMTimeoutError(exc.seconds, kind=exc.kind) from None
    except ProviderUsageLimitError as exc:
        usage_error = LLMUsageLimitError(str(exc))
        usage_error.connection_failure = failure_kind
        raise usage_error from None
    except ProviderError as exc:
        provider_error = LLMError(str(exc))
        provider_error.connection_failure = failure_kind
        raise provider_error from None

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
        # The in-call transient retries, and the backoff between them, are
        # part of the same test call: a lease of one backstop would expire
        # while this call is still retrying, and a second caller could then
        # test the paused connection in parallel. So the lease covers every
        # attempt plus every wait.
        lease_seconds = max_attempts * backstop_seconds + retry_backoff_seconds * (2 ** transient_retries - 1)
        # Checked with the lane held, immediately before the first attempt;
        # the finally below still releases the lane when it is refused.
        admission = connection_health.admit(connection_id, lease_seconds=lease_seconds)
        if not admission.allowed:
            raise LLMConnectionSuspendedError(connection_id, last_error=admission.health.last_error)
        # The provider's own exception when the attempts end on one; it stays
        # None for a timeout or a cancellation, which say nothing about the
        # connection. Its kind is computed once, for both the recording and
        # the raised LLMError's connection_failure.
        provider_failure: BaseException | None = None
        failure_kind = None
        try:
            # The backstop bounds each model attempt, not the wait for a free slot
            # or the backoff between attempts.
            attempt = 1
            while True:
                try:
                    response = await _attempt_completion(
                        kwargs,
                        messages=messages,
                        model=model,
                        backstop_seconds=backstop_seconds,
                        stall_seconds=stall_seconds,
                        stream_for_progress=stream_for_progress,
                    )
                    break
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
                    provider_failure = exc
                    failure_kind = classify_connection_failure(exc)
                    # Keep the empty-completion type so callers can tell it apart;
                    # it is still an LLMError, never a reply.
                    error_type = LLMEmptyResponseError if isinstance(exc, LLMEmptyResponseError) else LLMError
                    error = error_type(f"LLM call failed after {attempt} attempt(s): {exc}")
                    error.connection_failure = failure_kind
                    raise error from exc
        except BaseException:
            _settle_failed_call(connection_id, admission, provider_failure, failure_kind)
            raise
        connection_health.record_success(connection_id)
        return response
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


def count_history_tokens(text: str) -> int:
    """Count tokens of conversation history with litellm's default tokenizer.

    Unlike :func:`count_tokens` this takes no model: conversation history is
    shared per thread, so every reader (each agent in a thread, and the
    transcript API) must get the same count whatever connection it uses, and
    the count must never depend on a configured model.

    Args:
        text: The rendered history entry.

    Returns:
        The token count.

    Raises:
        Exception: Whatever litellm raises. Deliberately not mapped to 0 like
            :func:`count_tokens`: a 0 count would silently disable both the
            history budget and conversation compaction.
    """
    return litellm.token_counter(text=text)


class LLMError(Exception):
    """Raised when an LLM call fails.

    Attributes:
        connection_failure: What the failure said about the call's AI
            connection. ``"transient"`` or ``"rejected"`` when
            ``completion()`` raised this for a provider exception that counts
            toward pausing the connection (``classify_connection_failure``),
            the same kind it recorded. ``None`` for every other failure: a
            timeout, an empty reply, a refusal of that one request (a
            context-window or content-policy error), a local error before
            the provider was contacted, or a paused connection
            (``LLMConnectionSuspendedError``, which the dispatcher recognises
            by its type). The dispatcher parks a failed turn on a paused
            connection only when the connection caused the failure.
    """

    connection_failure: ConnectionFailureKind | None = None


class LLMUsageLimitError(LLMError):
    """Raised when the connection's account has no usage left.

    ``completion()`` raises it for an extension provider's
    ``ProviderUsageLimitError``; the message is the provider's operator-facing
    text. No retry can succeed until the operator's allowance resets, so it is
    never retried: the dispatcher stops the work and tells the operator. It is
    an ``LLMError``, so every turn that catches model failures still catches it.
    """


class LLMConnectionSuspendedError(LLMError):
    """Raised when the call's AI connection is paused after repeated provider errors.

    ``completion()`` raises it before any provider contact: a paused
    connection is never called, except for its one test call. The dispatcher
    holds new work for the connection and parks a turn that failed with it,
    without using up a retry, until the connection resumes. It is an
    ``LLMError``, so every turn handler that catches model failures catches
    it unchanged.

    Attributes:
        connection_id: The paused connection.
    """

    def __init__(self, connection_id: str, *, last_error: str) -> None:
        """Name the paused connection and what the provider last said.

        Args:
            connection_id: The paused connection.
            last_error: The provider's last error, from its health row.
        """
        self.connection_id = connection_id
        super().__init__(
            f"AI connection is paused after repeated provider errors. Last error: {last_error}"
        )


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
