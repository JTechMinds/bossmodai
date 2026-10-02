"""BossMod AI — Thinking-level vocabulary.

An agent picks a thinking level per routed activation; a connection says what
each level means on the wire. This module owns only the words and the shape of
a connection's level map. It does no I/O and knows no provider dialects.

``default`` is a choice, not a level: it sends no fragment, so the provider's
own default applies. It is stored as a value rather than NULL so a partial
update can write it back.
"""

from __future__ import annotations

import json
from typing import Any, Literal, get_args

ThinkingLevel = Literal["off", "low", "medium", "high", "xhigh"]
ThinkingChoice = Literal["default", "off", "low", "medium", "high", "xhigh"]
ThinkingLevels = dict[ThinkingLevel, dict[str, Any]]

THINKING_LEVELS: tuple[str, ...] = get_args(ThinkingLevel)
THINKING_CHOICES: tuple[str, ...] = get_args(ThinkingChoice)

# The two Literals are written out because a starred Literal does not type
# check; this keeps them from drifting apart.
assert THINKING_CHOICES == ("default", *THINKING_LEVELS)


def parse_thinking_levels(value: Any) -> ThinkingLevels | None:
    """Read a connection's level map from a mapping, a JSON string or None.

    Args:
        value: A dict of level → JSON-object fragment, the same as a JSON
            string (the stored TEXT column), or None.

    Returns:
        The map with every fragment intact, or None when ``value`` is None or
        an empty map. An empty map and no map both mean "no levels offered".

    Raises:
        ValueError: The string is not valid JSON, the value is not an object,
            a key is not a known level, or a fragment is not a non-empty JSON
            object. Pydantic reports this as a 422.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"thinking_levels is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"thinking_levels must be an object of level → JSON object, got {type(value).__name__}"
        )
    if not value:
        return None
    levels: ThinkingLevels = {}
    for key, fragment in value.items():
        if key not in THINKING_LEVELS:
            raise ValueError(
                f"thinking_levels has unknown level {key!r}; expected one of {', '.join(THINKING_LEVELS)}"
            )
        if not isinstance(fragment, dict) or not fragment:
            raise ValueError(f"thinking_levels[{key!r}] must be a non-empty JSON object")
        levels[key] = fragment
    return levels
