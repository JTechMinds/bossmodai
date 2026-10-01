"""BossMod AI — recurrence math for agent schedules (pure: no db, no config).

``next_occurrence`` is the one answer to "when does this rule run next",
used by the runtime worker's timetable and by the API's "Next run", so the
two always agree without sharing state. ``describe`` is the one human
wording of a rule.

Times are wall-clock on the host and resolved per date through
``core.time.local_wall_clock_to_utc``, so a DST change between two runs
moves nothing on the operator's clock.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from core.models.schedule import RecurrenceRule
from core.time import local_date_of, local_wall_clock_to_utc

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WORKWEEK = [0, 1, 2, 3, 4]
_ALL_WEEK = [0, 1, 2, 3, 4, 5, 6]
# Days one interval spans at most, per frequency; the scan horizon is
# interval x this + 32, which always reaches the next matching day.
_DAYS_PER_INTERVAL = {"daily": 1, "weekly": 7, "monthly": 31}
_HORIZON_SLACK_DAYS = 32


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _months_between(start: date, day: date) -> int:
    return (day.year - start.year) * 12 + (day.month - start.month)


def _last_day(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def matches_day(rule: RecurrenceRule, day: date) -> bool:
    """Whether ``rule`` runs on local calendar ``day`` (at any of its times).

    Private to this module in spirit; exported so the tests can pin the day
    rules without going through ``next_occurrence``.

    Args:
        rule: A validated rule.
        day: A local calendar date.

    Returns:
        False before ``rule.start_date``; otherwise the frequency's day rule
        (see ``RecurrenceRule``).
    """
    if day < rule.start_date:
        return False
    if rule.frequency == "daily":
        return (day - rule.start_date).days % rule.interval == 0
    if rule.frequency == "weekly":
        if day.weekday() not in rule.weekdays:
            return False
        weeks = (_monday_of(day) - _monday_of(rule.start_date)).days // 7
        return weeks % rule.interval == 0
    if _months_between(rule.start_date, day) % rule.interval != 0:
        return False
    # month_day is set for every valid monthly rule (RecurrenceRule).
    return day.day == min(int(rule.month_day), _last_day(day))


def _wall_times(rule: RecurrenceRule) -> list[time]:
    return [time(int(value[:2]), int(value[3:])) for value in rule.times]


def next_occurrence(rule: RecurrenceRule, *, after: datetime) -> datetime:
    """Return the first run of ``rule`` strictly after ``after``.

    Scans local days from ``after``'s local date (or the start date, if
    later) over a bounded horizon and returns the first (day, time) whose
    UTC instant is later than ``after``.

    Args:
        rule: A validated rule.
        after: An aware instant; an occurrence exactly at it is not returned.

    Returns:
        An aware UTC datetime.

    Raises:
        ValueError: ``after`` is naive, or no occurrence lies within the
            horizon. The latter is unreachable for a valid rule; it is an
            invariant check, never a fallback.
    """
    if after.tzinfo is None:
        raise ValueError("next_occurrence takes an aware datetime")
    first = max(local_date_of(after), rule.start_date)
    horizon = rule.interval * _DAYS_PER_INTERVAL[rule.frequency] + _HORIZON_SLACK_DAYS
    times = _wall_times(rule)
    for offset in range(horizon + 1):
        day = first + timedelta(days=offset)
        if not matches_day(rule, day):
            continue
        for at in times:
            instant = local_wall_clock_to_utc(day, at)
            if instant > after:
                return instant
    raise ValueError(f"no occurrence of {rule.model_dump_json()} within {horizon} days of {after.isoformat()}")


def describe(rule: RecurrenceRule) -> str:
    """The one human summary of a rule, e.g. ``Every weekday at 06:00, 12:00``.

    Args:
        rule: A validated rule.

    Returns:
        One sentence without a trailing period: the cadence, then
        ``at`` and the times in order.
    """
    at = f"at {', '.join(rule.times)}"
    if rule.frequency == "daily":
        cadence = "Every day" if rule.interval == 1 else f"Every {rule.interval} days"
        return f"{cadence} {at}"
    if rule.frequency == "weekly":
        if rule.interval == 1 and rule.weekdays == _WORKWEEK:
            return f"Every weekday {at}"
        if rule.interval == 1 and rule.weekdays == _ALL_WEEK:
            return f"Every day {at}"
        days = ", ".join(_WEEKDAY_NAMES[day] for day in rule.weekdays)
        cadence = "Every week" if rule.interval == 1 else f"Every {rule.interval} weeks"
        return f"{cadence} on {days} {at}"
    cadence = "Every month" if rule.interval == 1 else f"Every {rule.interval} months"
    return f"{cadence} on day {rule.month_day} {at}"
