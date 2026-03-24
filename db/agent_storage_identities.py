"""BossMod AI — Durable immutable per-agent storage identities."""

from __future__ import annotations

from db.crud import execute, query, query_one


def ensure_agent_storage_identity(agent_id: str) -> dict[str, object]:
    """Return one agent storage identity, creating it when missing."""
    existing = get_agent_storage_identity(agent_id)
    if existing is not None:
        return existing
    storage_index = _next_storage_index()
    storage_key = _storage_key_for_index(storage_index)
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
        ensure_agent_storage_identity(str(row["id"]))


def delete_agent_storage_identity(agent_id: str) -> None:
    """Delete one agent storage identity."""
    execute("DELETE FROM agent_storage_identities WHERE agent_id = $1", [agent_id])


def _next_storage_index() -> int:
    """Return the next immutable storage counter."""
    row = query_one(
        "SELECT COALESCE(MAX(storage_index), 0) + 1 AS next_index FROM agent_storage_identities"
    )
    value = row["next_index"] if row is not None else 1
    return int(value)


def _storage_key_for_index(storage_index: int) -> str:
    """Render the canonical human-readable storage key for one index."""
    return f"agent_{storage_index:04d}"
