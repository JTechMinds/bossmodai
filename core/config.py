"""BossMod AI — Centralized configuration reader.

ALL runtime configuration comes from the ``settings`` table.
No hardcoded defaults anywhere in the codebase. Modules call
``config.get()`` or ``config.require()`` to read values.

Settings are lazy-loaded on first access and cached per process. Call
``config.reload()`` after changing settings via the API. The app and the
runtime worker are separate processes, so each also calls
``refresh_if_changed()`` at the start of each request, turn or loop tick:
it reloads only when the database's ``settings_revision`` counter moved.
"""

from __future__ import annotations

import logging
import threading

import db

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_loaded = False
# The settings_revision.rev the cache was loaded at; None before the first load.
_loaded_rev: int | None = None
_lock = threading.RLock()


def _ensure_loaded() -> None:
    with _lock:
        loaded = _loaded
    if not loaded:
        reload()


def reload() -> None:
    """Reload all settings from the database into the cache.

    Records the ``settings_revision`` counter first, then loads the rows, all
    under the lock. A write that lands between the two leaves the recorded
    revision behind the rows, so the next ``refresh_if_changed`` reloads
    again instead of missing it.

    Raises:
        ConfigError: The ``settings_revision`` row is missing.
    """
    global _loaded, _loaded_rev

    with _lock:
        rev = _read_settings_revision()
        rows = db.get_settings()
        _cache.clear()
        for s in rows:
            _cache[s.key] = s.value
        _loaded_rev = rev
        _loaded = True
    logger.debug("Config loaded: %d settings at revision %d", len(_cache), rev)


def refresh_if_changed() -> bool:
    """Reload the cache if any process changed settings since the last load.

    The database bumps ``settings_revision.rev`` on every insert, update and
    delete on ``settings`` (triggers in ``db/schema.sql``), whatever process or
    code path wrote it. This reads that one integer and reloads only when it
    differs from the revision the cache was loaded at. Call it at the start
    of each HTTP request, agent turn or worker loop tick, not in hot inner
    paths.

    Returns:
        True when the cache was reloaded, False when it was already current.

    Raises:
        ConfigError: The ``settings_revision`` row is missing.
    """
    with _lock:
        if _read_settings_revision() == _loaded_rev:
            return False
        reload()
        return True


def _read_settings_revision() -> int:
    row = db.query_one("SELECT rev FROM settings_revision WHERE id = 1")
    if row is None:
        # The schema seeds this row on every init; without it a change by the
        # other process would go unseen, so fail instead of serving stale values.
        raise ConfigError(
            "settings_revision row is missing; db/schema.sql seeds it on init_db"
        )
    return int(row["rev"])


def get(key: str) -> str | None:
    """Get a setting value, or ``None`` if not set or empty."""
    _ensure_loaded()
    with _lock:
        val = _cache.get(key)
    if not val or not val.strip():
        return None
    return val.strip()


def get_live(key: str) -> str | None:
    """Read one setting from the database, then the process cache.

    A per-key read that predates :func:`refresh_if_changed`, which is now
    the general mechanism: each process refreshes its whole cache at the
    start of a request, turn or loop tick. Kept for its existing callers
    (Shell Executor Enable and the other CLI gates). A live hit warms the
    cache so later ``get`` calls in the same worker turn see the same value.
    """
    global _loaded
    token = (key or "").strip()
    if not token:
        return None
    try:
        from db.crud import query_one

        row = query_one("SELECT value FROM settings WHERE key = $1", [token])
    except Exception:
        row = None
    if row and row.get("value") not in (None, ""):
        val = str(row["value"])
        with _lock:
            _cache[token] = val
            _loaded = True
        stripped = val.strip()
        return stripped or None
    return get(token)


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
