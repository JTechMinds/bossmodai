"""The worker's in-memory timetable (core/scheduling/timetable.py): pure, no clock, no DB."""

from __future__ import annotations

import os
import time as time_module
from datetime import datetime, timezone

import pytest

from core.models.schedule import RecurrenceRule
from core.scheduling.timetable import Timetable


@pytest.fixture(autouse=True)
def utc_zone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time_module.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time_module.tzset()


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _daily(*times: str) -> RecurrenceRule:
    return RecurrenceRule.model_validate(
        {"frequency": "daily", "interval": 1, "times": list(times), "start_date": "2026-09-01"},
    )


def test_load_computes_from_now_so_past_due_times_never_replay() -> None:
    table = Timetable()
    # 06:00 already passed today (offline/paused): it is not in the table.
    table.load([("a", _daily("06:00")), ("b", _daily("12:00"))], now=_at(10, 9))
    assert len(table) == 2
    assert table.next_due() == _at(10, 12)
    assert table.pop_due(_at(10, 9)) == []
    assert table.pop_due(_at(10, 12)) == [("b", _at(10, 12))]
    assert table.next_due() == _at(11, 6)


def test_pop_due_returns_every_entry_due_at_the_same_instant() -> None:
    table = Timetable()
    table.load([("a", _daily("06:00")), ("b", _daily("06:00")), ("c", _daily("07:00"))], now=_at(10, 5))
    assert sorted(table.pop_due(_at(10, 6))) == [("a", _at(10, 6)), ("b", _at(10, 6))]
    assert len(table) == 1


def test_schedule_replaces_an_existing_entry() -> None:
    table = Timetable()
    table.load([("a", _daily("06:00"))], now=_at(10, 5))
    table.schedule("a", _daily("08:00"), after=_at(10, 5))
    assert len(table) == 1
    assert table.next_due() == _at(10, 8)
    table.schedule("b", _daily("07:00"), after=_at(10, 5))
    assert table.pop_due(_at(10, 7)) == [("b", _at(10, 7))]


def test_drop_removes_an_entry_and_ignores_an_unknown_id() -> None:
    table = Timetable()
    table.load([("a", _daily("06:00")), ("b", _daily("07:00"))], now=_at(10, 5))
    table.drop("a")
    table.drop("zzz")
    assert len(table) == 1
    assert table.next_due() == _at(10, 7)


def test_an_empty_table_has_nothing_due() -> None:
    table = Timetable()
    assert table.next_due() is None
    assert table.pop_due(_at(10, 23)) == []
    assert len(table) == 0


def test_load_refuses_a_duplicate_id() -> None:
    with pytest.raises(ValueError):
        Timetable().load([("a", _daily("06:00")), ("a", _daily("07:00"))], now=_at(10, 5))
