"""BossMod AI — Centralized configuration reader.

ALL runtime configuration comes from the ``settings`` table.
No hardcoded defaults anywhere in the codebase. Modules call
``config.get()`` or ``config.require()`` to read values.

Settings are lazy-loaded on first access and cached. Call
``config.reload()`` after changing settings via the API.
"""

from __future__ import annotations

import logging
import threading

import db

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_loaded = False
_lock = threading.RLock()


def _ensure_loaded() -> None:
    with _lock:
        loaded = _loaded
    if not loaded:
        reload()


def reload() -> None:
    """Reload all settings from the database into the cache."""
    global _loaded

    rows = db.get_settings()
    with _lock:
        _cache.clear()
        for s in rows:
            _cache[s.key] = s.value
        _loaded = True
    logger.debug("Config loaded: %d settings", len(_cache))


def get(key: str) -> str | None:
    """Get a setting value, or ``None`` if not set or empty."""
    _ensure_loaded()
    with _lock:
        val = _cache.get(key)
    if not val or not val.strip():
        return None
    return val.strip()


def require(key: str) -> str:
    """Get a required setting value, raising if not configured.

    Raises
    ------
    ConfigError
        If the setting is missing or empty.
    """
    val = get(key)
    if val is None:
        raise ConfigError(
            f"Required setting '{key}' is not configured. "
            f"Set it via the Settings page or PUT /api/settings/{key}"
        )
    return val


def get_int(key: str) -> int | None:
    """Get a setting as an integer, or ``None`` if missing or not an int."""
    val = get(key)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        logger.warning("Setting %s=%r is not an integer; ignoring", key, val)
        return None


def require_int(key: str) -> int:
    """Get a required setting as an integer."""
    val = require(key)
    try:
        return int(val)
    except ValueError as exc:
        raise ConfigError(f"Required setting '{key}' is not an integer: {val!r}") from exc


def get_float(key: str) -> float | None:
    """Get a setting as a float, or ``None`` if missing or not a float."""
    val = get(key)
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        logger.warning("Setting %s=%r is not a float; ignoring", key, val)
        return None


def require_float(key: str) -> float:
    """Get a required setting as a float."""
    val = require(key)
    try:
        return float(val)
    except ValueError as exc:
        raise ConfigError(f"Required setting '{key}' is not a float: {val!r}") from exc


class ConfigError(Exception):
    """Raised when a required setting is missing or empty."""
