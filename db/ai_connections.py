"""BossMod AI — AI Connections CRUD."""

from __future__ import annotations

from typing import Any

from core.models import AIConnection
from db.crud import build_update, execute, fetch_all, fetch_one, insert_returning

_COLUMNS = "id, name, api_base_url, api_key, model, created_at"

_VALID_COLUMNS = {"name", "api_base_url", "api_key", "model"}


def create_connection(
    name: str,
    api_base_url: str,
    api_key: str | None = None,
    model: str | None = None,
) -> AIConnection:
    """Insert a new AI connection."""
    return insert_returning(
        f"""
        INSERT INTO ai_connections (name, api_base_url, api_key, model)
        VALUES ($1, $2, $3, $4)
        RETURNING {_COLUMNS}
        """,
        [name, api_base_url, api_key, model],
        AIConnection,
    )


def get_connection_by_id(connection_id: str) -> AIConnection | None:
    """Fetch a single AI connection by ID."""
    return fetch_one(
        f"SELECT {_COLUMNS} FROM ai_connections WHERE id = $1",
        [connection_id],
        AIConnection,
    )


def list_connections() -> list[AIConnection]:
    """Return all AI connections ordered by name."""
    return fetch_all(
        f"SELECT {_COLUMNS} FROM ai_connections ORDER BY name",
        model_cls=AIConnection,
    )


def update_connection(connection_id: str, **fields: Any) -> AIConnection | None:
    """Update an AI connection's fields."""
    build_update("ai_connections", "id", connection_id, fields, _VALID_COLUMNS)
    return get_connection_by_id(connection_id)


def delete_connection(connection_id: str) -> bool:
    """Delete an AI connection."""
    existing = get_connection_by_id(connection_id)
    if not existing:
        return False
    execute("DELETE FROM ai_connections WHERE id = $1", [connection_id])
    return True
