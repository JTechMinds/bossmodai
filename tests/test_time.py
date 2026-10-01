"""core/time.py: local time is resolved per instant, so a DST change never shifts it."""

from __future__ import annotations

import os
import time as time_module
from datetime import datetime, timedelta, timezone

import pytest

from core.time import ensure_utc, now_local


@pytest.fixture(autouse=True)
def new_york_zone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time_module.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time_module.tzset()


def test_now_local_is_aware_with_the_offset_in_force_now() -> None:
    local = now_local()
    assert local.tzinfo is not None
    assert local.utcoffset() == datetime.now().astimezone().utcoffset()
    assert abs(local - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_a_naive_value_uses_the_offset_of_its_own_instant_across_dst() -> None:
    # 2026-03-08 02:00 EST -> 03:00 EDT; 2026-11-01 02:00 EDT -> 01:00 EST.
    assert ensure_utc(datetime(2026, 3, 7, 12, 0)) == datetime(2026, 3, 7, 17, 0, tzinfo=timezone.utc)
    assert ensure_utc(datetime(2026, 3, 9, 12, 0)) == datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc)
    assert ensure_utc(datetime(2026, 10, 31, 12, 0)) == datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
    assert ensure_utc(datetime(2026, 11, 2, 12, 0)) == datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)


def test_an_aware_value_passes_through_as_the_same_instant() -> None:
    aware = datetime(2026, 6, 1, 9, 30, tzinfo=timezone(timedelta(hours=2)))
    result = ensure_utc(aware)
    assert result == aware
    assert result.tzinfo == timezone.utc
    utc = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert ensure_utc(utc) == utc
