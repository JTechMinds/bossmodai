"""Short completion on the System AI connection.

This is not an agent turn. It does not select an identity model, build a
turn context, or inject standing-pref warm text into a member prompt.
Channel routing uses it for one small JSON completion. A missing model
or a failed call returns ``None`` so the caller can fall back. An unset
id, or a saved id that no longer names a connection, uses the first
connection. This module does not write the setting, so an operator's
saved pick stays put on upgrade.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

import db
from core import config
from core.llm.client import canonicalize_openai_compatible_model, validate_api_base
from core.models import AIConnection

logger = logging.getLogger(__name__)

# Bound for this helper only. It does not change llm_stall_timeout_seconds
# or llm_request_timeout_seconds.
SYSTEM_COMPLETION_TIMEOUT_SECONDS = 20
SYSTEM_COMPLETION_MAX_TOKENS = 256


def _usable(connection: AIConnection | None) -> AIConnection | None:
    if connection is None:
        return None
    if not str(connection.model or "").strip():
        return None
    return connection


def resolve_system_connection() -> AIConnection | None:
    """Return the System AI connection, or None when it cannot be used.

    A saved connection id that still exists is used as stored. This read
    does not write the setting. When the id is unset, or that connection
    is gone, the first connection in list order (name) is used instead.
    A saved connection that exists but has no model is not replaced.
    """
    connection_id = config.get("system_ai_connection")
    if connection_id:
        connection = db.get_connection_by_id(connection_id)
        if connection is not None:
            return _usable(connection)
    connections = db.list_connections()
    if not connections:
        return None
    return _usable(connections[0])


def system_ai_is_configured() -> bool:
    """Return whether a System AI connection with a model can be used."""
    return resolve_system_connection() is not None


def complete_text(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = SYSTEM_COMPLETION_MAX_TOKENS,
) -> str | None:
    """Run one short non-streaming completion. None means no usable connection or a failed call."""
    connection = resolve_system_connection()
    if connection is None:
        logger.debug("system completion skipped: system AI unavailable")
        return None
    model = str(connection.model or "").strip()
    api_base = str(connection.api_base_url or "").strip() or None
    kwargs: dict[str, Any] = {
        "model": canonicalize_openai_compatible_model(model, api_base=api_base),
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "timeout": SYSTEM_COMPLETION_TIMEOUT_SECONDS,
        "stream": False,
    }
    if api_base:
        try:
            kwargs["api_base"] = validate_api_base(api_base)
        except Exception:
            logger.warning("system completion skipped: system AI base URL was rejected")
            return None
        kwargs["api_key"] = connection.api_key or "local-openai-compatible"
    elif connection.api_key:
        kwargs["api_key"] = connection.api_key
    extra = _extra_body(connection.extra_body)
    if extra:
        kwargs["extra_body"] = extra
    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        logger.warning("system completion failed: %s", type(exc).__name__)
        return None
    text = _message_text(response)
    if not text.strip():
        logger.warning("system completion returned empty text")
        return None
    return text


def _extra_body(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("system completion ignored an unreadable extra_body")
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _message_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
    else:
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None) if choice is not None else None
        content = getattr(message, "content", None) if message is not None else None
    return content if isinstance(content, str) else ""
