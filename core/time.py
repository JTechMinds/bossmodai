"""BossMod AI — Time normalization helpers."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def ensure_utc(value: datetime) -> datetime:
    """Normalize naive DB timestamps to UTC using the local runtime timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=_LOCAL_TZ).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def local_timezone() -> tzinfo:
    """Return the runtime's local timezone."""
    return _LOCAL_TZ


def now_local() -> datetime:
    """Return the current local time as a timezone-aware datetime."""
    return datetime.now(_LOCAL_TZ)
