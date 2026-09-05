"""BossMod AI — AI Connections CRUD."""

from __future__ import annotations

from typing import Any

from core.models import AIConnection
from db.crud import build_update, execute, fetch_all, fetch_one, insert_returning
from db.secret_store import decrypt_secret, encrypt_secret

_COLUMNS = "id, name, api_base_url, api_key, model, extra_body, created_at"

_VALID_COLUMNS = {"name", "api_base_url", "api_key", "model", "extra_body"}


def _decrypt_connection(connection: AIConnection | None) -> AIConnection | None:
    if connection is None or not connection.api_key:
        return connection
    plain = decrypt_secret(connection.api_key)
    if plain == connection.api_key:
        return connection
    return connection.model_copy(update={"api_key": plain})


def create_connection(
    name: str,
    api_base_url: str,
    api_key: str | None = None,
    model: str | None = None,
    extra_body: str | None = None,
) -> AIConnection:
    """Insert a new AI connection."""
    created = insert_returning(
        f"""
        INSERT INTO ai_connections (name, api_base_url, api_key, model, extra_body)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_COLUMNS}
        """,
        [name, api_base_url, encrypt_secret(api_key), model, extra_body],
        AIConnection,
    )
    decrypted = _decrypt_connection(created)
    if decrypted is None:
        raise RuntimeError("Failed to reload created AI connection")
    return decrypted


def get_connection_by_id(connection_id: str) -> AIConnection | None:
    """Fetch a single AI connection by ID."""
    return _decrypt_connection(
        fetch_one(
            f"SELECT {_COLUMNS} FROM ai_connections WHERE id = $1",
            [connection_id],
            AIConnection,
        )
    )


def list_connections() -> list[AIConnection]:
    """Return all AI connections ordered by name."""
    return [
        decrypted
        for connection in fetch_all(
            f"SELECT {_COLUMNS} FROM ai_connections ORDER BY name",
            model_cls=AIConnection,
        )
        if (decrypted := _decrypt_connection(connection)) is not None
    ]


def update_connection(connection_id: str, **fields: Any) -> AIConnection | None:
    """Update an AI connection's fields."""
    if "api_key" in fields:
        fields = {**fields, "api_key": encrypt_secret(fields["api_key"])}
    build_update("ai_connections", "id", connection_id, fields, _VALID_COLUMNS)
    return get_connection_by_id(connection_id)


def delete_connection(connection_id: str) -> bool:
    """Delete an AI connection."""
    existing = get_connection_by_id(connection_id)
    if not existing:
        return False
    execute("DELETE FROM ai_connections WHERE id = $1", [connection_id])
    return True
