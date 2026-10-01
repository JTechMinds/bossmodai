"""BossMod AI — Time normalization helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timezone, tzinfo

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


def local_wall_clock_to_utc(day: date, at: time) -> datetime:
    """Return the UTC instant of wall-clock ``at`` on local calendar ``day``.

    DST-aware per date, unlike ``_LOCAL_TZ`` (a fixed offset captured at
    process start): a naive ``astimezone()`` asks the OS for the offset of
    that very instant. A time skipped by a spring-forward change (02:30 on
    the change date) resolves to whatever instant the OS normalizes it to;
    an ambiguous fall-back time resolves to its first occurrence (fold 0).

    Args:
        day: A date on the host's local calendar.
        at: A naive wall-clock time.

    Returns:
        An aware UTC datetime.

    Raises:
        ValueError: ``at`` carries a tzinfo; a wall-clock time has none.
    """
    if at.tzinfo is not None:
        raise ValueError("local_wall_clock_to_utc takes a naive wall-clock time")
    return datetime.combine(day, at).astimezone(timezone.utc)


def local_date_of(instant: datetime) -> date:
    """Return the host's local calendar date of an aware ``instant`` (DST-aware).

    Raises:
        ValueError: ``instant`` is naive; which date it falls on is then unknown.
    """
    if instant.tzinfo is None:
        raise ValueError("local_date_of takes an aware datetime")
    return instant.astimezone().date()
