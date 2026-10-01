"""BossMod AI — recurrence math for agent schedules (pure: no db, no config).

``next_occurrence`` is the one answer to "when does this rule run next",
used by the runtime worker's timetable and by the API's "Next run", so the
two always agree without sharing state. ``describe`` is the one human
wording of a rule.

The days a rule runs on (``matches_day``) and the slots within each day
(``_wall_times``: the "At" times, or every ``every_minutes`` from
``window_start`` to ``window_end`` inclusive, aligned to the wall clock) are
separate. Slots are wall-clock on the host and resolved per date through
``core.time.local_wall_clock_to_utc``, so a DST change between two runs
moves nothing on the operator's clock. On a spring-forward day the skipped
slots normalize onto real instants, which can land out of order or on
another slot's instant; on a fall-back day the repeated hour runs once
(fold 0). ``next_occurrence`` therefore takes the minimum later instant
across a day's slots, never the first in list order.
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
# A window this wide is the whole day, and describe() leaves it unsaid.
_WHOLE_DAY_START = "00:00"
_WHOLE_DAY_END = "23:59"


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


def _minutes_of(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def _wall_times(rule: RecurrenceRule) -> list[time]:
    """The wall-clock slots of one matching day.

    Returns:
        "At" mode: ``times``. "Every" mode: ``window_start``, then every
        ``every_minutes`` up to and including ``window_end``.
    """
    if rule.times:
        return [time(*divmod(_minutes_of(value), 60)) for value in rule.times]
    # Every-mode fields are all set when times is empty (RecurrenceRule).
    first, last = _minutes_of(str(rule.window_start)), _minutes_of(str(rule.window_end))
    return [time(*divmod(minute, 60)) for minute in range(first, last + 1, int(rule.every_minutes))]


def next_occurrence(rule: RecurrenceRule, *, after: datetime) -> datetime:
    """Return the first run of ``rule`` strictly after ``after``.

    Scans local days from ``after``'s local date (or the start date, if
    later) over a bounded horizon. On the first matching day with any slot
    later than ``after`` it returns the minimum such instant (DST
    normalization can put a day's slots out of order).

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
        later = [instant for instant in (local_wall_clock_to_utc(day, at) for at in times) if instant > after]
        if later:
            return min(later)
    raise ValueError(f"no occurrence of {rule.model_dump_json()} within {horizon} days of {after.isoformat()}")


def format_local_run(instant: datetime) -> str:
    """One run as the operator and the agent read it: ``Thu 2 Oct 2026, 06:00`` on the host's clock.

    Args:
        instant: An aware instant.

    Returns:
        The weekday, day, month, year and 24-hour time in host-local time
        (DST-correct for that instant).

    Raises:
        ValueError: ``instant`` is naive.
    """
    if instant.tzinfo is None:
        raise ValueError("format_local_run takes an aware datetime")
    local = instant.astimezone()
    return f"{local:%a} {local.day} {local:%b %Y, %H:%M}"


def upcoming(rule: RecurrenceRule, *, after: datetime, count: int) -> list[datetime]:
    """The next ``count`` runs of ``rule`` after ``after``, each strictly after the last.

    Chains ``next_occurrence``, so a preview and the real runs cannot disagree.

    Args:
        rule: A validated rule.
        after: An aware instant.
        count: How many runs; at least 1.

    Returns:
        ``count`` aware UTC instants, strictly increasing.

    Raises:
        ValueError: ``count`` is below 1, or ``next_occurrence`` refuses.
    """
    if count < 1:
        raise ValueError("upcoming needs a count of at least 1")
    runs: list[datetime] = []
    for _ in range(count):
        after = next_occurrence(rule, after=after)
        runs.append(after)
    return runs


def _day_cadence(rule: RecurrenceRule) -> str:
    """Which days, in words: ``Every weekday``, ``Every 2 weeks on Fri``, ``Every month on day 5``."""
    if rule.frequency == "daily":
        return "Every day" if rule.interval == 1 else f"Every {rule.interval} days"
    if rule.frequency == "weekly":
        if rule.interval == 1 and rule.weekdays == _WORKWEEK:
            return "Every weekday"
        if rule.interval == 1 and rule.weekdays == _ALL_WEEK:
            return "Every day"
        days = ", ".join(_WEEKDAY_NAMES[day] for day in rule.weekdays)
        cadence = "Every week" if rule.interval == 1 else f"Every {rule.interval} weeks"
        return f"{cadence} on {days}"
    cadence = "Every month" if rule.interval == 1 else f"Every {rule.interval} months"
    return f"{cadence} on day {rule.month_day}"


def _repeat_words(minutes: int) -> str:
    """``minute``, ``15 minutes``, ``hour`` or ``2 hours``."""
    if minutes % 60 == 0:
        hours = minutes // 60
        return "hour" if hours == 1 else f"{hours} hours"
    return "minute" if minutes == 1 else f"{minutes} minutes"


def describe(rule: RecurrenceRule) -> str:
    """The one human summary of a rule.

    "At" mode: ``Every weekday at 06:00, 12:00``. "Every" mode: ``Every
    weekday, every 15 minutes from 09:00 to 17:00``, with the window dropped
    when it is the whole day (00:00-23:59) and hours used when the step is a
    whole number of hours.

    Args:
        rule: A validated rule.

    Returns:
        One sentence without a trailing period.
    """
    cadence = _day_cadence(rule)
    if rule.times:
        return f"{cadence} at {', '.join(rule.times)}"
    repeat = f"{cadence}, every {_repeat_words(int(rule.every_minutes))}"
    if (rule.window_start, rule.window_end) == (_WHOLE_DAY_START, _WHOLE_DAY_END):
        return repeat
    return f"{repeat} from {rule.window_start} to {rule.window_end}"
