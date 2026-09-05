"""BossMod AI — Secret redaction for API responses.

List/get endpoints never return full secret values. Callers see
``has_*`` plus last-4 only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import AIConnection, Setting
from db.secret_store import SECRET_SETTING_KEYS

# Never listed on GET /api/settings — injected into the desktop page instead.
HIDDEN_SETTING_KEYS = frozenset({
    "local_api_token",
})


def secret_last4(value: str | None) -> str | None:
    """Return the last four characters of a secret, or None if empty."""
    if not value:
        return None
    text = str(value)
    if not text:
        return None
    return text[-4:]


def serialize_setting(setting: Setting) -> dict[str, Any]:
    """Serialize a setting, redacting known secret keys."""
    payload: dict[str, Any] = {
        "key": setting.key,
        "value": setting.value,
        "category": setting.category,
        "updated_at": setting.updated_at,
    }
    if setting.key in SECRET_SETTING_KEYS:
        payload["value"] = ""
        payload["has_value"] = bool(setting.value)
        payload["value_last4"] = secret_last4(setting.value)
    return payload


def serialize_settings(settings: list[Setting]) -> list[dict[str, Any]]:
    """Serialize settings for GET responses, omitting hidden keys."""
    return [
        serialize_setting(setting)
        for setting in settings
        if setting.key not in HIDDEN_SETTING_KEYS
    ]


def serialize_connection(connection: AIConnection) -> dict[str, Any]:
    """Serialize an AI connection without the raw API key."""
    created_at = connection.created_at
    if isinstance(created_at, datetime):
        created_at_out: datetime | str = created_at
    else:
        created_at_out = created_at
    return {
        "id": connection.id,
        "name": connection.name,
        "api_base_url": connection.api_base_url,
        "model": connection.model,
        "extra_body": connection.extra_body,
        "created_at": created_at_out,
        "has_api_key": bool(connection.api_key),
        "api_key_last4": secret_last4(connection.api_key) if connection.api_key else None,
    }


def serialize_secret_field(field: str, value: str | None) -> dict[str, Any]:
    """Redact a single named secret field (e.g. agent api_key)."""
    return {
        f"has_{field}": bool(value),
        f"{field}_last4": secret_last4(value) if value else None,
    }
