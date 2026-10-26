"""BossMod AI — extension-wide settings (``manifest.settings``).

One row per extension. The whole value is a JSON object of str → str wrapped
with the ``bm1:`` at-rest secret wrap, as in ``db.extension_agent_configs``,
since a settings form may hold a secret. Only the extension host reads and
writes it; an extension reads its own values through ``ExtensionContext``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from db.crud import execute, query, query_one
from db.secret_store import decrypt_secret, encrypt_secret


def get_extension_settings(extension_id: str) -> dict[str, str] | None:
    """Return one extension's stored settings, decrypted.

    Args:
        extension_id: The extension id.

    Returns:
        The stored object, or ``None`` when there is no row.

    Raises:
        ValueError: The stored value does not decrypt to a JSON object of
            str → str (a corrupt row is surfaced, never ignored).
    """
    row = query_one("SELECT config FROM extension_settings WHERE extension_id = $1", [extension_id])
    if row is None:
        return None
    plain = decrypt_secret(row["config"])
    try:
        value = json.loads(plain or "")
    except json.JSONDecodeError as exc:
        raise ValueError(f"extension {extension_id} settings are not JSON") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"extension {extension_id} settings are not an object of strings")
    return value


def set_extension_settings(extension_id: str, values: dict[str, str]) -> None:
    """Insert or replace one extension's settings.

    Args:
        extension_id: The extension id.
        values: The complete object to store; it replaces any earlier one.
    """
    execute(
        """
        INSERT INTO extension_settings (extension_id, config, updated_at)
        VALUES ($1, $2, $3)
        ON CONFLICT(extension_id) DO UPDATE SET
            config = excluded.config,
            updated_at = excluded.updated_at
        """,
        [extension_id, encrypt_secret(json.dumps(values)), datetime.now(timezone.utc)],
    )


def delete_extension_settings(extension_id: str) -> bool:
    """Delete one extension's settings.

    Returns:
        Whether a row was deleted.
    """
    deleted = query("DELETE FROM extension_settings WHERE extension_id = $1 RETURNING 1", [extension_id])
    return bool(deleted)
