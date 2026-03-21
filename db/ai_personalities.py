"""BossMod AI — AI Personalities CRUD."""

from __future__ import annotations

from typing import Any

from core.models import AIPersonality
from db.crud import build_update, execute, fetch_all, fetch_one, insert_returning

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
