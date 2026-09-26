"""BossMod AI — Durable immutable per-agent storage identities.

Keys are allocated from the ``agent_storage_keys`` ledger, which keeps every
key ever issued, so a key is never reissued, not even after its agent is
deleted.
"""

from __future__ import annotations

from db.connection import transaction
from db.crud import execute, query, query_one


def ensure_agent_storage_identity(agent_id: str) -> dict[str, object]:
    """Return one agent's storage identity, allocating a never-issued key when missing.

    The ledger row is written first and the identity row copies its index and
    key. Both writes must commit or roll back together, so this runs inside
    the caller's ``transaction()`` (``create_agent`` already holds one, and
    transactions do not nest here).

    Args:
        agent_id: The agent that owns the key.

    Returns:
        The identity row: ``agent_id``, ``storage_index``, ``storage_key``,
        ``created_at``.

    Raises:
        RuntimeError: The identity row cannot be read back after the insert.
        sqlite3.IntegrityError: The next index or key is already taken, which
            only a hand-edited ledger or ``sqlite_sequence`` can cause.
    """
    existing = get_agent_storage_identity(agent_id)
    if existing is not None:
        return existing
    storage_index = _next_ledger_index()
    storage_key = _storage_key_for_index(storage_index)
    execute(
        """
        INSERT INTO agent_storage_keys (storage_index, storage_key, agent_id)
        VALUES ($1, $2, $3)
        """,
        [storage_index, storage_key, agent_id],
    )
    execute(
        """
        INSERT INTO agent_storage_identities (agent_id, storage_index, storage_key)
        VALUES ($1, $2, $3)
        """,
        [agent_id, storage_index, storage_key],
    )
    created = get_agent_storage_identity(agent_id)
    if created is None:
        raise RuntimeError(f"Failed to create storage identity for agent {agent_id}")
    return created


def get_agent_storage_identity(agent_id: str) -> dict[str, object] | None:
    """Return one agent storage identity by agent id."""
    return query_one(
        """
        SELECT agent_id, storage_index, storage_key, created_at
        FROM agent_storage_identities
        WHERE agent_id = $1
        """,
        [agent_id],
    )


def ensure_all_agent_storage_identities() -> None:
    """Backfill immutable storage identities for every existing agent."""
    rows = query(
        """
        SELECT agents.id
        FROM agents
        LEFT JOIN agent_storage_identities ON agent_storage_identities.agent_id = agents.id
        WHERE agent_storage_identities.agent_id IS NULL
        ORDER BY agents.created_at, agents.id
        """
    )
    for row in rows:
        with transaction():
            ensure_agent_storage_identity(str(row["id"]))


def get_agent_names_by_storage_keys(storage_keys: list[str]) -> dict[str, str]:
    """Map storage keys to agent display names in a single round-trip."""
    if not storage_keys:
        return {}
    placeholders = ", ".join(f"${i + 1}" for i in range(len(storage_keys)))
    rows = query(
        f"""
        SELECT asi.storage_key, a.name
        FROM agent_storage_identities asi
        JOIN agents a ON a.id = asi.agent_id
        WHERE asi.storage_key IN ({placeholders})
        """,
        storage_keys,
    )
    return {str(row["storage_key"]): str(row["name"]) for row in rows}


def delete_agent_storage_identity(agent_id: str) -> None:
    """Delete one agent storage identity.

    Called only from ``db.agents.delete_agent_rows``, right after
    ``retire_agent_storage_key``: the ledger keeps the key, so it is never
    issued again.
    """
    execute("DELETE FROM agent_storage_identities WHERE agent_id = $1", [agent_id])


def retire_agent_storage_key(agent_id: str) -> None:
    """Stamp the agent's ledger row retired. The row stays, so the key stays used.

    Raises:
        RuntimeError: The agent has no live ledger row. Every agent gets one
            at create (or from the startup seeding), so this is a broken
            invariant, not a case to skip.
    """
    retired = query(
        """
        UPDATE agent_storage_keys
        SET retired_at = current_timestamp
        WHERE agent_id = $1 AND retired_at IS NULL
        RETURNING storage_key
        """,
        [agent_id],
    )
    if not retired:
        raise RuntimeError(f"Agent {agent_id} has no live storage key in the ledger to retire")


def _next_ledger_index() -> int:
    """Return one past the highest index the ledger has ever issued.

    ``sqlite_sequence`` is AUTOINCREMENT's high-water mark for
    ``agent_storage_keys``: SQLite only ever raises it, deletes never lower it,
    and the startup seeding raises it past keys found on disk. No row yet
    means nothing was issued. Called inside the write transaction that
    inserts the index, so no other writer can take it in between.
    """
    row = query_one(
        "SELECT seq FROM sqlite_sequence WHERE name = 'agent_storage_keys'"
    )
    return (int(row["seq"]) if row is not None else 0) + 1


def _storage_key_for_index(storage_index: int) -> str:
    """Render the canonical human-readable storage key for one index."""
    return f"agent_{storage_index:04d}"
