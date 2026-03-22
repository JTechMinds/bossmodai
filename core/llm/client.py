"""BossMod AI — Unified LLM client via litellm.

Provides an async interface to any LLM provider (OpenAI, Anthropic,
Ollama, etc.) through litellm's unified API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm

from core import config

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

# Concurrency limiter for LLM calls — initialized lazily from settings
_llm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return the LLM concurrency semaphore, initializing from config on first use."""
    global _llm_semaphore
    if _llm_semaphore is None:
        limit = config.get_int("max_concurrent_llm_calls") or 5
        _llm_semaphore = asyncio.Semaphore(limit)
    return _llm_semaphore


def normalize_api_base(url: str) -> str:
    """Strip /chat/completions or /completions suffix from a base URL."""
    clean = url.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if clean.endswith(suffix):
            return clean[: -len(suffix)]
    return clean


@dataclass
class LLMResponse:
    """Structured result from an LLM call."""
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


async def completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    extra_body: str | None = None,
) -> LLMResponse:
    """Call an LLM via litellm and return a structured response.

    Parameters
    ----------
    model : str
        Model identifier passed directly to litellm.
    messages : list
        Chat messages in OpenAI format (role + content).
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
        If the LLM call fails.
    """
    if temperature is None:
        temperature = config.get_float("default_temperature") or 0.7
    if max_tokens is None:
        max_tokens = config.get_int("default_max_tokens") or 2048

    # When using a custom base URL, litellm needs a provider prefix.
    # Auto-add "openai/" for OpenAI-compatible endpoints if no prefix present.
    if api_base and "/" not in model:
        model = f"openai/{model}"

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if api_base:
        kwargs["api_base"] = normalize_api_base(api_base)
        # LiteLLM requires an api_key even for local servers that don't need one
        kwargs["api_key"] = api_key or "not-needed"
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

    logger.info("LLM request: model=%s, api_base=%s, extra_body=%s", kwargs.get("model"), kwargs.get("api_base"), kwargs.get("extra_body"))

    try:
        async with _get_semaphore():
            response = await litellm.acompletion(**kwargs)
    except Exception as exc:
        logger.error("LLM call failed (model=%s): %s", model, exc)
        raise LLMError(f"LLM call failed: {exc}") from exc

    choice = response.choices[0]
    usage = response.usage

    return LLMResponse(
        content=choice.message.content or "",
        model=response.model or model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
    )


def count_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count for a string using litellm's tokenizer.

    Falls back to the configured default work model, then ``gpt-4o``.
    """
    effective_model = model or config.get("default_model_work") or "gpt-4o"
    try:
        return litellm.token_counter(model=effective_model, text=text)
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("Token counting failed (model=%s), using estimate: %s", effective_model, exc)
        return len(text) // 4


class LLMError(Exception):
    """Raised when an LLM call fails."""
