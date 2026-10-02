"""Recurrence math (core/scheduling/recurrence.py): pure, on a pinned host zone.

Every test runs with ``TZ=America/New_York`` set through ``time.tzset()``, so
"06:00 local" is a known instant and the DST dates are real ones.
"""

from __future__ import annotations

import os
import time as time_module
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from core.models.schedule import RecurrenceRule
from core.scheduling.recurrence import describe, matches_day, next_occurrence, upcoming

NY = "America/New_York"


@pytest.fixture(autouse=True)
def new_york_zone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = NY
    time_module.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time_module.tzset()


def _rule(**fields) -> RecurrenceRule:
    base = {"frequency": "daily", "interval": 1, "times": ["06:00"], "start_date": "2026-09-01"}
    base.update(fields)
    return RecurrenceRule.model_validate(base)


def _local(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """An aware UTC instant for a New York wall-clock time."""
    return datetime(year, month, day, hour, minute).astimezone(timezone.utc)


def _as_local(instant: datetime) -> tuple[date, str]:
    local = instant.astimezone()
    return local.date(), f"{local:%H:%M}"


# ─── daily ───


def test_daily_every_day_runs_at_the_next_time() -> None:
    rule = _rule()
    assert next_occurrence(rule, after=_local(2026, 9, 10, 5, 0)) == _local(2026, 9, 10, 6, 0)
    assert next_occurrence(rule, after=_local(2026, 9, 10, 7, 0)) == _local(2026, 9, 11, 6, 0)


def test_daily_every_other_day_counts_from_the_start_date() -> None:
    rule = _rule(interval=2, start_date="2026-09-01")
    assert matches_day(rule, date(2026, 9, 1))
    assert not matches_day(rule, date(2026, 9, 2))
    assert matches_day(rule, date(2026, 9, 3))
    assert next_occurrence(rule, after=_local(2026, 9, 2, 12, 0)) == _local(2026, 9, 3, 6, 0)


def test_nothing_runs_before_the_start_date() -> None:
    rule = _rule(start_date="2026-10-05")
    assert not matches_day(rule, date(2026, 10, 4))
    assert next_occurrence(rule, after=_local(2026, 9, 10, 12, 0)) == _local(2026, 10, 5, 6, 0)


def test_several_times_a_day_pick_the_next_one_today_then_tomorrow() -> None:
    rule = _rule(times=["21:00", "06:00", "12:00"])
    assert rule.times == ["06:00", "12:00", "21:00"]
    assert next_occurrence(rule, after=_local(2026, 9, 10, 7, 0)) == _local(2026, 9, 10, 12, 0)
    assert next_occurrence(rule, after=_local(2026, 9, 10, 12, 30)) == _local(2026, 9, 10, 21, 0)
    assert next_occurrence(rule, after=_local(2026, 9, 10, 22, 0)) == _local(2026, 9, 11, 6, 0)


def test_an_occurrence_exactly_at_after_is_not_returned() -> None:
    rule = _rule(times=["06:00", "12:00"])
    at = _local(2026, 9, 10, 6, 0)
    assert next_occurrence(rule, after=at) == _local(2026, 9, 10, 12, 0)
    assert next_occurrence(rule, after=at - timedelta(microseconds=1)) == at


def test_a_naive_after_is_refused() -> None:
    with pytest.raises(ValueError):
        next_occurrence(_rule(), after=datetime(2026, 9, 10, 6, 0))


# ─── weekly ───


def test_weekly_runs_on_the_chosen_weekdays() -> None:
    # 2026-09-07 is a Monday.
    rule = _rule(frequency="weekly", weekdays=[4, 0, 2], start_date="2026-09-07")
    assert rule.weekdays == [0, 2, 4]
    got = []
    after = _local(2026, 9, 7, 0, 0)
    for _ in range(4):
        after = next_occurrence(rule, after=after)
        got.append(_as_local(after))
    assert got == [
        (date(2026, 9, 7), "06:00"), (date(2026, 9, 9), "06:00"),
        (date(2026, 9, 11), "06:00"), (date(2026, 9, 14), "06:00"),
    ]


def test_every_other_week_counts_whole_weeks_from_the_start_week() -> None:
    # Start on a Wednesday: its Monday-started week is week 0.
    rule = _rule(frequency="weekly", interval=2, weekdays=[0], start_date="2026-09-09")
    assert not matches_day(rule, date(2026, 9, 7))  # before the start date
    assert not matches_day(rule, date(2026, 9, 14))  # week 1
    assert matches_day(rule, date(2026, 9, 21))  # week 2
    assert next_occurrence(rule, after=_local(2026, 9, 9, 12, 0)) == _local(2026, 9, 21, 6, 0)


# ─── monthly ───


def test_monthly_day_31_uses_the_last_day_of_a_short_month() -> None:
    rule = _rule(frequency="monthly", month_day=31, start_date="2026-01-01")
    assert next_occurrence(rule, after=_local(2026, 2, 1)) == _local(2026, 2, 28, 6, 0)
    assert next_occurrence(rule, after=_local(2026, 4, 1)) == _local(2026, 4, 30, 6, 0)
    assert next_occurrence(rule, after=_local(2026, 5, 1)) == _local(2026, 5, 31, 6, 0)
    assert not matches_day(rule, date(2026, 4, 29))


def test_monthly_every_three_months_counts_from_the_start_month() -> None:
    rule = _rule(frequency="monthly", interval=3, month_day=15, start_date="2026-01-10")
    got = []
    after = _local(2026, 1, 1)
    for _ in range(3):
        after = next_occurrence(rule, after=after)
        got.append(_as_local(after)[0])
    assert got == [date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15)]


# ─── DST (America/New_York) ───


def test_spring_forward_keeps_the_wall_clock_time() -> None:
    # 2026-03-08: clocks go from 02:00 EST to 03:00 EDT.
    rule = _rule(start_date="2026-03-01")
    before = next_occurrence(rule, after=_local(2026, 3, 7, 0, 0))
    after = next_occurrence(rule, after=before)
    assert _as_local(before) == (date(2026, 3, 7), "06:00")
    assert _as_local(after) == (date(2026, 3, 8), "06:00")
    assert before == datetime(2026, 3, 7, 11, 0, tzinfo=timezone.utc)
    assert after == datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)


def test_a_time_skipped_by_spring_forward_resolves_to_the_os_normalized_instant() -> None:
    rule = _rule(times=["02:30"], start_date="2026-03-01")
    skipped = next_occurrence(rule, after=_local(2026, 3, 8, 0, 0))
    # 02:30 does not exist on 2026-03-08; the OS normalizes it to 03:30 EDT.
    assert skipped == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert _as_local(skipped) == (date(2026, 3, 8), "03:30")


def test_fall_back_keeps_the_wall_clock_time_and_runs_once() -> None:
    # 2026-11-01: clocks go from 02:00 EDT back to 01:00 EST.
    rule = _rule(times=["01:30", "06:00"], start_date="2026-10-01")
    first = next_occurrence(rule, after=_local(2026, 11, 1, 0, 0))
    second = next_occurrence(rule, after=first)
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # 01:30 EDT, the first one
    assert _as_local(second) == (date(2026, 11, 1), "06:00")
    assert second == datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)


# ─── validation ───


@pytest.mark.parametrize(
    "fields",
    [
        {"frequency": "weekly"},
        {"frequency": "weekly", "weekdays": [7]},
        {"frequency": "weekly", "weekdays": [1, 1]},
        {"weekdays": [1]},
        {"frequency": "monthly"},
        {"frequency": "monthly", "month_day": 32},
        {"month_day": 5},
        {"times": []},
        {"times": ["06:00", "06:00"]},
        {"times": ["6:00"]},
        {"times": ["24:00"]},
        {"times": [f"{hour:02d}:00" for hour in range(13)]},
        {"interval": 0},
        {"interval": 366},
        {"frequency": "weekly", "weekdays": [0], "interval": 53},
        {"frequency": "monthly", "month_day": 1, "interval": 13},
        {"hourly": True},
    ],
)
def test_the_validator_refuses_bad_rules(fields) -> None:
    with pytest.raises(ValidationError):
        _rule(**fields)


# ─── describe ───


def test_describe_wording() -> None:
    assert describe(_rule(times=["12:00", "06:00"]), clock="24h") == "Every day at 06:00, 12:00"
    assert describe(_rule(interval=2), clock="24h") == "Every 2 days at 06:00"
    assert describe(_rule(frequency="weekly", weekdays=[0, 1, 2, 3, 4]), clock="24h") == "Every weekday at 06:00"
    assert describe(_rule(frequency="weekly", weekdays=[0, 1, 2, 3, 4, 5, 6]), clock="24h") == "Every day at 06:00"
    assert describe(_rule(frequency="weekly", weekdays=[0, 2]), clock="24h") == "Every week on Mon, Wed at 06:00"
    assert describe(_rule(frequency="weekly", interval=2, weekdays=[4]), clock="24h") == "Every 2 weeks on Fri at 06:00"
    assert describe(_rule(frequency="monthly", month_day=15), clock="24h") == "Every month on day 15 at 06:00"
    assert describe(_rule(frequency="monthly", interval=3, month_day=31), clock="24h") == "Every 3 months on day 31 at 06:00"


# ─── "Every" mode: repeats within a window ───


def _every(minutes: int, start: str, end: str, **fields) -> RecurrenceRule:
    return _rule(times=[], every_minutes=minutes, window_start=start, window_end=end, **fields)


def _sequence(rule: RecurrenceRule, after: datetime, count: int) -> list[datetime]:
    out = []
    for _ in range(count):
        after = next_occurrence(rule, after=after)
        out.append(after)
    return out


def test_every_15_minutes_runs_inside_the_window_including_its_end_then_rolls_over() -> None:
    rule = _every(15, "09:00", "10:00")
    got = [_as_local(instant) for instant in _sequence(rule, _local(2026, 9, 10, 8, 0), 6)]
    assert got == [
        (date(2026, 9, 10), "09:00"), (date(2026, 9, 10), "09:15"), (date(2026, 9, 10), "09:30"),
        (date(2026, 9, 10), "09:45"), (date(2026, 9, 10), "10:00"), (date(2026, 9, 11), "09:00"),
    ]


def test_a_step_that_does_not_land_on_the_window_end_stops_before_it() -> None:
    rule = _every(25, "09:00", "10:00")
    got = [_as_local(instant)[1] for instant in _sequence(rule, _local(2026, 9, 10, 8, 0), 4)]
    assert got == ["09:00", "09:25", "09:50", "09:00"]


def test_every_hour_at_quarter_past() -> None:
    rule = _every(60, "00:15", "23:59")
    assert next_occurrence(rule, after=_local(2026, 9, 10, 13, 20)) == _local(2026, 9, 10, 14, 15)
    assert next_occurrence(rule, after=_local(2026, 9, 10, 23, 20)) == _local(2026, 9, 11, 0, 15)


def test_weekly_with_a_window_skips_the_days_it_does_not_choose() -> None:
    # 2026-09-11 is a Friday; the next weekday is Monday the 14th.
    rule = _every(30, "09:00", "17:00", frequency="weekly", weekdays=[0, 1, 2, 3, 4])
    assert next_occurrence(rule, after=_local(2026, 9, 11, 17, 0)) == _local(2026, 9, 14, 9, 0)


@pytest.mark.parametrize(
    ("day", "start"),
    [((2026, 3, 8), (2026, 3, 8, 0, 0)), ((2026, 11, 1), (2026, 11, 1, 0, 0))],
    ids=["spring-forward", "fall-back"],
)
def test_every_30_minutes_across_a_dst_change_strictly_increases(day, start) -> None:
    rule = _every(30, "00:00", "23:59", start_date="2026-01-01")
    after = datetime(*start).astimezone(timezone.utc)
    got = _sequence(rule, after, 52)
    assert all(later > earlier for earlier, later in zip(got, got[1:]))
    assert len(set(got)) == len(got)


def test_the_earliest_later_slot_wins_even_when_dst_puts_slots_out_of_order() -> None:
    # On the spring-forward day 02:30 normalizes to 03:30 EDT (07:30 UTC),
    # after the 03:00 slot (07:00 UTC) in list order: 03:00 must come first.
    rule = _rule(times=["02:30", "03:00"], start_date="2026-03-01")
    first = next_occurrence(rule, after=_local(2026, 3, 8, 0, 0))
    assert first == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)
    assert next_occurrence(rule, after=first) == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "fields",
    [
        {"times": ["06:00"], "every_minutes": 15, "window_start": "09:00", "window_end": "17:00"},
        {"times": []},
        {"times": [], "every_minutes": 15, "window_start": "17:00", "window_end": "09:00"},
        {"times": [], "every_minutes": 0, "window_start": "09:00", "window_end": "17:00"},
        {"times": [], "every_minutes": 721, "window_start": "09:00", "window_end": "17:00"},
        {"times": [], "every_minutes": 15, "window_start": "09:00"},
        {"times": [], "every_minutes": 15, "window_start": "9:00", "window_end": "17:00"},
    ],
    ids=["both", "neither", "overnight", "zero", "over-720", "no-end", "bad-time"],
)
def test_the_validator_refuses_bad_repeats(fields) -> None:
    with pytest.raises(ValidationError):
        _rule(**fields)


def test_a_stored_rule_without_repeat_keys_parses_as_at_mode() -> None:
    stored = '{"frequency": "daily", "interval": 1, "times": ["06:00"], "weekdays": [], ' \
        '"month_day": null, "start_date": "2026-09-01"}'
    rule = RecurrenceRule.model_validate_json(stored)
    assert (rule.times, rule.every_minutes, rule.window_start, rule.window_end) == (["06:00"], None, None, None)


def test_describe_every_forms() -> None:
    assert describe(_every(15, "09:00", "17:00", frequency="weekly", weekdays=[0, 1, 2, 3, 4]), clock="24h") \
        == "Every weekday, every 15 minutes from 09:00 to 17:00"
    assert describe(_every(60, "00:00", "23:59"), clock="24h") == "Every day, every hour"
    assert describe(_every(120, "08:00", "20:00", interval=2), clock="24h") == "Every 2 days, every 2 hours from 08:00 to 20:00"
    assert describe(_every(1, "00:00", "23:59", frequency="monthly", month_day=5), clock="24h") == "Every month on day 5, every minute"
    assert describe(_every(90, "00:15", "23:59", frequency="weekly", weekdays=[0, 2]), clock="24h") \
        == "Every week on Mon, Wed, every 90 minutes from 00:15 to 23:59"


def test_describe_writes_twelve_hour_time_for_the_operator() -> None:
    assert describe(_rule(times=["12:00", "06:00"]), clock="12h") == "Every day at 6:00 AM, 12:00 PM"
    assert describe(_rule(times=["07:30", "19:05"]), clock="12h") == "Every day at 7:30 AM, 7:05 PM"
    assert describe(_rule(times=["00:00", "12:00", "23:59"]), clock="12h") \
        == "Every day at 12:00 AM, 12:00 PM, 11:59 PM"
    assert describe(_every(15, "09:00", "17:00", frequency="weekly", weekdays=[0, 1, 2, 3, 4]), clock="12h") \
        == "Every weekday, every 15 minutes from 9:00 AM to 5:00 PM"
    assert describe(_every(30, "00:00", "12:00"), clock="12h") \
        == "Every day, every 30 minutes from 12:00 AM to 12:00 PM"
    # The whole day stays unsaid in either clock.
    assert describe(_every(60, "00:00", "23:59"), clock="12h") == "Every day, every hour"


def test_describe_refuses_an_unknown_clock() -> None:
    # Typed as Any on purpose: this is the value a caller outside the type
    # checker could still pass.
    unknown: Any = "am/pm"
    with pytest.raises(ValueError, match="clock"):
        describe(_rule(), clock=unknown)


# ─── upcoming (the editor's preview) ───


@pytest.mark.parametrize(
    "rule",
    [_rule(times=["06:00", "18:00"]), _every(45, "09:00", "11:00", frequency="weekly", weekdays=[1, 3])],
    ids=["at", "every"],
)
def test_upcoming_returns_count_strictly_increasing_runs(rule) -> None:
    after = _local(2026, 9, 10, 12, 0)
    runs = upcoming(rule, after=after, count=7)
    assert len(runs) == 7
    assert runs[0] == next_occurrence(rule, after=after)
    assert all(later > earlier for earlier, later in zip(runs, runs[1:]))


def test_upcoming_refuses_a_count_below_one() -> None:
    with pytest.raises(ValueError):
        upcoming(_rule(), after=_local(2026, 9, 10), count=0)
