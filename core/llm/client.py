"""BossMod AI — Unified LLM client via litellm.

Provides an async interface to any LLM provider (OpenAI, Anthropic,
Ollama, etc.) through litellm's unified API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import litellm

from core import config

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True


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
        kwargs["api_base"] = api_base
        # LiteLLM requires an api_key even for local servers that don't need one
        kwargs["api_key"] = api_key or "not-needed"
    elif api_key:
        kwargs["api_key"] = api_key

    try:
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


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Estimate token count for a string using litellm's tokenizer."""
    try:
        return litellm.token_counter(model=model, text=text)
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("Token counting failed (model=%s), using estimate: %s", model, exc)
        return len(text) // 4


class LLMError(Exception):
    """Raised when an LLM call fails."""
