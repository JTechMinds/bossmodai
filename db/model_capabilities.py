"""BossMod AI — Operator-set model capabilities, keyed by model name.

Agents keep no link to the AI Connection they were configured from, and
per-mode models are free strings, so the only identifier available when a
call is made is the model name itself. Keys are the RAW model string, as
typed on the connection or agent (before any ``openai/`` prefix is added for
OpenAI-compatible endpoints), and matching is exact: ``gpt-4o`` and
``openai/gpt-4o`` are different keys.
"""

from __future__ import annotations

from datetime import datetime, timezone

from db.crud import execute, query_one


def supports_images(model: str) -> bool:
    """Return whether the operator marked ``model`` as able to read images.

    Args:
        model: The raw model string.

    Returns:
        The stored flag. A model with no row is text-only (False): image
        support is never inferred, only set by the operator.
    """
    row = query_one(
        "SELECT supports_images FROM model_capabilities WHERE model = $1",
        [model],
    )
    if row is None:
        return False
    return bool(row["supports_images"])


def set_supports_images(model: str, value: bool) -> None:
    """Record whether ``model`` can read images (insert or update).

    Args:
        model: The raw model string. Must be non-blank.
        value: True when the model accepts image input.

    Raises:
        ValueError: ``model`` is blank; a blank key would match nothing.
    """
    if not model.strip():
        raise ValueError("A model name is required to set its capabilities")
    execute(
        """
        INSERT INTO model_capabilities (model, supports_images, updated_at)
        VALUES ($1, $2, $3)
        ON CONFLICT(model) DO UPDATE SET
            supports_images = excluded.supports_images,
            updated_at = excluded.updated_at
        """,
        [model, value, datetime.now(timezone.utc)],
    )
