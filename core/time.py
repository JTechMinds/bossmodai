"""BossMod AI — Time normalization helpers.

Every local-time question here asks the OS for the offset of that very
instant (a no-argument ``astimezone()``), so a long-running process stays
right across a DST change. Nothing caches a fixed offset at start-up.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime.

    A naive value is read as host-local wall-clock time, resolved with the OS
    rule for that instant (DST-correct on either side of a change). An aware
    value is converted to UTC unchanged in meaning.
    """
    return value.astimezone(timezone.utc)


def now_local() -> datetime:
    """Return the current host-local time, aware, with the offset in force now."""
    return datetime.now().astimezone()


def local_wall_clock_to_utc(day: date, at: time) -> datetime:
    """Return the UTC instant of wall-clock ``at`` on local calendar ``day``.

    DST-aware per date: a naive ``astimezone()`` asks the OS for the offset
    of that very instant. A time skipped by a spring-forward change (02:30 on
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
