"""The runtime worker's restart policy: the sliding-window crash-loop guard and the outage's words."""

from __future__ import annotations

from datetime import datetime, timezone

from core.runtime.supervisor import RestartHistory, WorkerOutage, restart_allowed

_SINCE = datetime(2026, 11, 26, 13, 5, tzinfo=timezone.utc)


def test_attempts_older_than_the_window_stop_counting() -> None:
    history = RestartHistory()

    assert history.record(100.0, window_seconds=600) == 1
    assert history.record(400.0, window_seconds=600) == 2
    # Exactly a window after the first: it still counts.
    assert history.record(700.0, window_seconds=600) == 3
    # The first is now older than the window; the second is exactly a window old.
    assert history.record(1000.0, window_seconds=600) == 3
    # Long after: only this attempt is left.
    assert history.record(5000.0, window_seconds=600) == 1


def test_a_shorter_window_forgets_more() -> None:
    history = RestartHistory()
    history.record(0.0, window_seconds=600)
    history.record(30.0, window_seconds=600)

    assert history.record(61.0, window_seconds=60) == 2


def test_clear_forgets_every_attempt() -> None:
    history = RestartHistory()
    for moment in (1.0, 2.0, 3.0):
        history.record(moment, window_seconds=600)

    history.clear()

    assert history.record(4.0, window_seconds=600) == 1


def test_restarts_are_allowed_up_to_the_limit_and_not_past_it() -> None:
    assert restart_allowed(1, limit=3) is True
    assert restart_allowed(3, limit=3) is True
    assert restart_allowed(4, limit=3) is False
    assert restart_allowed(1, limit=1) is True
    assert restart_allowed(2, limit=1) is False


def test_the_outage_says_what_it_means_and_what_was_tried() -> None:
    assert WorkerOutage(since=_SINCE, reason="it stopped responding", attempts=3).describe() == (
        "Your agents can't work until it runs again. "
        "BossMod tried to restart it 3 times; the last time, it stopped responding."
    )
    assert WorkerOutage(since=_SINCE, reason="it could not start", attempts=1).describe() == (
        "Your agents can't work until it runs again. "
        "BossMod tried to restart it 1 time; the last time, it could not start."
    )
    # No attempt could be made (a restart setting is unusable).
    assert WorkerOutage(since=_SINCE, reason="it stopped unexpectedly", attempts=0).describe() == (
        "Your agents can't work until it runs again. "
        "BossMod could not restart it on its own after it stopped unexpectedly."
    )
