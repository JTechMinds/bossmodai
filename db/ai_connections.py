"""BossMod AI — AI Connections CRUD."""

from __future__ import annotations

from collections.abc import Collection, Sequence
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


def _copy_name(source_name: str, taken: Collection[str]) -> str:
    """Derive a free name for a copy: "X (copy)", then "X (copy 2)", ...

    ``taken`` is a parameter rather than a ``list_connections()`` call inside,
    which makes this a pure function of its two inputs: the numbering rule is
    pinned by a test with no database, and the caller keeps the one read it
    already needs. The first free suffix wins, so a deleted "(copy 2)" is
    reused instead of being climbed past.

    ``ai_connections.name`` has no ``UNIQUE`` constraint — two rows may share a
    name and nothing breaks. This is for the operator's eyes, not for
    correctness, which is why a collision picks the next number rather than
    raising.

    Args:
        source_name: The name being copied from, verbatim.
        taken: The names already in use. Membership is all that is read, so a
            set is the cheap thing to pass.

    Returns:
        A name not in ``taken``.
    """
    candidate = f"{source_name} (copy)"
    suffix = 2
    while candidate in taken:
        candidate = f"{source_name} (copy {suffix})"
        suffix += 1
    return candidate


def duplicate_connection(connection_id: str) -> AIConnection | None:
    """Copy a connection — base URL, API key, model and extra_body — under a new name.

    The operator duplicates to change one field (usually ``extra_body``: the
    same provider and key, a different thinking mode), so every field is
    carried verbatim and the edit form is where the difference is made.

    This is glue on purpose. ``get_connection_by_id`` already decrypts and
    ``create_connection`` already encrypts, so going through both means there is
    exactly one place a key is unwrapped and one place it is wrapped. Its own
    INSERT would be a second encryption path that looks right until the day the
    wrapping changes under it — the bug ``restore_connections`` documents at
    length. The new row therefore also takes its ``id`` and ``created_at`` from
    the INSERT defaults: a copy is a new connection, where a restore is the
    same one coming back.

    Args:
        connection_id: The connection to copy from.

    Returns:
        The newly created copy, or ``None`` when ``connection_id`` matches no
        row — the same shape ``get_connection_by_id`` and ``update_connection``
        return, so a route can map it to a 404 the same way.

    Failure modes:
        Propagates ``sqlite3.Error`` from the read or the insert, and the
        ``RuntimeError`` ``create_connection`` raises if the new row cannot be
        read back. Nothing is written when the source is missing.
    """
    source = get_connection_by_id(connection_id)
    if source is None:
        return None
    return create_connection(
        name=_copy_name(source.name, {conn.name for conn in list_connections()}),
        api_base_url=source.api_base_url,
        api_key=source.api_key,
        model=source.model,
        extra_body=source.extra_body,
    )
