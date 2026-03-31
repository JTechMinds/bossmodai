"""BossMod AI — AI Personalities CRUD."""

from __future__ import annotations

import logging
from typing import Any

from core.default_prompts import iter_default_personality_prompts
from core.models import AIPersonality
from db.crud import build_update, execute, fetch_all, fetch_one, insert_returning, query, query_one

logger = logging.getLogger(__name__)

_COLUMNS = "id, name, prompt_template, created_at"

_VALID_COLUMNS = {"name", "prompt_template"}


def create_personality(
    name: str,
    prompt_template: str,
) -> AIPersonality:
    """Insert a new AI personality."""
    return insert_returning(
        f"""
        INSERT INTO ai_personalities (name, prompt_template)
        VALUES ($1, $2)
        RETURNING {_COLUMNS}
        """,
        [name, prompt_template],
        AIPersonality,
    )


def get_personality(personality_id: str) -> AIPersonality | None:
    """Fetch a single AI personality by ID."""
    return fetch_one(
        f"SELECT {_COLUMNS} FROM ai_personalities WHERE id = $1",
        [personality_id],
        AIPersonality,
    )


def list_personalities() -> list[AIPersonality]:
    """Return all AI personalities ordered by name."""
    return fetch_all(
        f"SELECT {_COLUMNS} FROM ai_personalities ORDER BY name",
        model_cls=AIPersonality,
    )


def update_personality(personality_id: str, **fields: Any) -> AIPersonality | None:
    """Update an AI personality's fields."""
    build_update("ai_personalities", "id", personality_id, fields, _VALID_COLUMNS)
    return get_personality(personality_id)


def delete_personality(personality_id: str) -> bool:
    """Delete an AI personality."""
    existing = get_personality(personality_id)
    if not existing:
        return False
    execute("DELETE FROM ai_personalities WHERE id = $1", [personality_id])
    return True


# Names are stable identifiers used by seed/force-reseed logic.
# Renaming a personality prompt file entry in core.default_prompts will cause it
# to be inserted as a new personality rather than updating the old one.
def _default_personalities() -> tuple[tuple[str, str], ...]:
    return iter_default_personality_prompts()


def seed_default_personalities() -> None:
    """Insert any default personalities whose name is not already present.

    Never overwrites user-modified personalities.
    """
    # Replace the old generic "Research Assistant" with the new "Research Analyst".
    execute("DELETE FROM ai_personalities WHERE name = $1", ["Research Assistant"])

    seeded = 0
    defaults = _default_personalities()
    for name, prompt in defaults:
        existing = query_one(
            "SELECT id FROM ai_personalities WHERE name = $1", [name]
        )
        if existing is None:
            create_personality(name=name, prompt_template=prompt)
            seeded += 1
    logger.info("Personality seed check complete (%d defaults, %d new)", len(defaults), seeded)


def force_reseed_personalities() -> None:
    """Delete all default-named personalities and re-insert canonical versions.

    User-created personalities with non-default names are preserved.
    Available for programmatic/admin use; the full reseed path
    (``reseed_application_data``) resets the entire database.
    """
    from db.connection import transaction

    with transaction():
        defaults = _default_personalities()
        for name, prompt in defaults:
            execute("DELETE FROM ai_personalities WHERE name = $1", [name])
            create_personality(name=name, prompt_template=prompt)
    logger.info("Personalities force-reseeded (%d defaults)", len(defaults))
