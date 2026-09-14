"""BossMod AI — AI Connections CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.models import AIConnection
from db.crud import build_update, execute, fetch_all, fetch_one, insert_returning
from db.secret_store import decrypt_secret, encrypt_secret

_COLUMNS = "id, name, api_base_url, api_key, model, extra_body, created_at"

# The same names as a sequence. `restore_connections` writes a full row, so it
# needs the column list and its values to stay in step; reading both off this
# makes reordering _COLUMNS reorder them together instead of silently pairing
# each value with its neighbour's column.
_COLUMN_NAMES = tuple(column.strip() for column in _COLUMNS.split(","))

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


def restore_connections(connections: Sequence[AIConnection]) -> int:
    """Re-insert connections captured before the database was recreated.

    The application reseed deletes the database file and rebuilds it from the
    schema; a provider's base URL and API key are the one thing an operator
    cannot get back from inside the app afterwards, so the reseed carries them
    across. ``id`` and ``created_at`` are written as given rather than
    regenerated: nothing references connection ids (agents name models, not
    connections), so this buys honesty — the same connections, not copies of
    them — not referential integrity.

    Args:
        connections: Rows as ``list_connections`` returned them, so each
            ``api_key`` is plaintext. Keys are re-wrapped through the same
            ``encrypt_secret`` path ``create_connection`` uses; there is no
            second encryption scheme and the data key survives the reset.

    Returns:
        The number of rows inserted. An empty sequence inserts nothing and
        returns ``0``.

    Failure modes:
        Raises ``sqlite3.Error`` if the schema is not there yet or an ``id``
        is already taken — so this must run after ``init_db()`` has rebuilt
        the schema and against a table that does not already hold these rows.
        It is deliberately not defensive: a reseed that silently dropped a key
        it could not write back would be worse than one that fails.
    """
    placeholders = ", ".join(f"${index + 1}" for index in range(len(_COLUMN_NAMES)))
    restored = 0
    for connection in connections:
        # Keyed by column name, then read out in _COLUMN_NAMES order: a column
        # this misses raises KeyError rather than shifting every later value one
        # place to the left, which SQLite would accept without complaint.
        values = {
            "id": connection.id,
            "name": connection.name,
            "api_base_url": connection.api_base_url,
            "api_key": encrypt_secret(connection.api_key),
            "model": connection.model,
            "extra_body": connection.extra_body,
            "created_at": connection.created_at,
        }
        execute(
            f"INSERT INTO ai_connections ({_COLUMNS}) VALUES ({placeholders})",
            [values[column] for column in _COLUMN_NAMES],
        )
        restored += 1
    return restored


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
