"""BossMod AI — Thinking-level request shaping.

Pure functions that turn a connection's ``extra_body`` plus its level map and
an agent's choice into the ``extra_body`` string the client sends. Providers
spell thinking differently, so the connection carries the fragment and this
module only merges it; nothing here names a provider.
"""

from __future__ import annotations

import json
from typing import Any

from core.models.thinking import ThinkingChoice, ThinkingLevels


class ThinkingConfigError(ValueError):
    """A thinking choice cannot be applied to a connection as configured."""


def deep_merge(base: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """Merge ``fragment`` over ``base`` recursively, without mutating either.

    Nested objects merge key by key; on any other conflict the fragment's
    value wins.

    Args:
        base: The connection's extra body.
        fragment: The level's fragment.

    Returns:
        A new dict.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in fragment.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def effective_extra_body(
    extra_body: str | None,
    levels: ThinkingLevels | None,
    choice: ThinkingChoice,
) -> str | None:
    """Return the extra body to send for one thinking choice.

    Args:
        extra_body: The connection's stored extra body (a JSON object string)
            or None.
        levels: The connection's level map, or None when it offers none.
        choice: The agent's choice for the activation.

    Returns:
        ``extra_body`` unchanged for ``default``; otherwise the JSON string of
        the level's fragment deep-merged over ``extra_body``.

    Raises:
        ThinkingConfigError: ``choice`` is a level the connection does not
            offer, or ``extra_body`` is not a JSON object, so the fragment has
            nothing valid to merge into.
    """
    if choice == "default":
        return extra_body
    if not levels or choice not in levels:
        raise ThinkingConfigError(f"thinking level {choice!r} is not offered by this connection")
    base: Any = {}
    if extra_body is not None and extra_body.strip():
        try:
            base = json.loads(extra_body)
        except json.JSONDecodeError as exc:
            raise ThinkingConfigError(f"the connection's extra body is not valid JSON: {exc}") from exc
        if not isinstance(base, dict):
            raise ThinkingConfigError("the connection's extra body is not a JSON object")
    return json.dumps(deep_merge(base, levels[choice]))


def unoffered(levels: ThinkingLevels | None, choices: dict[str, ThinkingChoice]) -> list[str]:
    """List the non-``default`` choices that ``levels`` does not offer.

    Args:
        levels: A connection's level map, or None.
        choices: Field name → chosen level, e.g. ``{"thinking_work": "high"}``.

    Returns:
        ``"<field>: <level>"`` for each unoffered choice, in ``choices`` order.
    """
    offered = levels or {}
    return [
        f"{field}: {choice}"
        for field, choice in choices.items()
        if choice != "default" and choice not in offered
    ]
