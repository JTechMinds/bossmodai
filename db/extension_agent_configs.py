"""BossMod AI — per-agent extension settings (``manifest.agent_config``).

One row per (extension, agent). The whole value is a JSON object of
str → str wrapped with the ``bm1:`` at-rest secret wrap, since it holds
credentials (e.g. a client secret). Only the extension host reads and writes
it; an extension reads its own values through ``ExtensionContext``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from db.crud import execute, query, query_one
from db.secret_store import decrypt_secret, encrypt_secret


def get_extension_agent_config(extension_id: str, agent_id: str) -> dict[str, str] | None:
    """Return one agent's stored values for one extension, decrypted.

    Args:
        extension_id: The extension id.
        agent_id: The agent id.

    Returns:
        The stored object, or ``None`` when there is no row.

    Raises:
        ValueError: The stored value does not decrypt to a JSON object of
            str → str (a corrupt row is surfaced, never ignored).
    """
    row = query_one(
        "SELECT config FROM extension_agent_configs WHERE extension_id = $1 AND agent_id = $2",
        [extension_id, agent_id],
    )
    if row is None:
        return None
    plain = decrypt_secret(row["config"])
    try:
        value = json.loads(plain or "")
    except json.JSONDecodeError as exc:
        raise ValueError(f"extension {extension_id} config for agent {agent_id} is not JSON") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"extension {extension_id} config for agent {agent_id} is not an object of strings")
    return value


def set_extension_agent_config(extension_id: str, agent_id: str, values: dict[str, str]) -> None:
    """Insert or replace one agent's values for one extension.

    Args:
        extension_id: The extension id.
        agent_id: An existing agent's id (FK).
        values: The complete object to store; it replaces any earlier one.

    Raises:
        sqlite3.IntegrityError: ``agent_id`` names no agent.
    """
    execute(
        """
        INSERT INTO extension_agent_configs (extension_id, agent_id, config, updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT(extension_id, agent_id) DO UPDATE SET
            config = excluded.config,
            updated_at = excluded.updated_at
        """,
        [extension_id, agent_id, encrypt_secret(json.dumps(values)), datetime.now(timezone.utc)],
    )


def delete_extension_agent_config(extension_id: str, agent_id: str) -> bool:
    """Delete one agent's values for one extension.

    Returns:
        Whether a row was deleted.
    """
    deleted = query(
        "DELETE FROM extension_agent_configs WHERE extension_id = $1 AND agent_id = $2 RETURNING 1",
        [extension_id, agent_id],
    )
    return bool(deleted)


def extension_agent_config_updated_at(extension_id: str, agent_id: str) -> datetime | None:
    """Return when one agent's values were last saved, or ``None`` when there are none."""
    row = query_one(
        "SELECT updated_at FROM extension_agent_configs WHERE extension_id = $1 AND agent_id = $2",
        [extension_id, agent_id],
    )
    return None if row is None else row["updated_at"]


def configured_agent_ids(extension_id: str) -> frozenset[str]:
    """Return the ids of every agent with stored values for this extension."""
    rows = query(
        "SELECT agent_id FROM extension_agent_configs WHERE extension_id = $1",
        [extension_id],
    )
    return frozenset(row["agent_id"] for row in rows)
