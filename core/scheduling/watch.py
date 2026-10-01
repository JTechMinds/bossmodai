"""BossMod AI — the runtime worker's schedule clock.

A singleton shaped like ``ExtensionWakeWatch``: started and stopped with the
other runtime services (core/runtime/worker.py), so Pause stops it and an
occurrence due while paused is simply missed.

It owns the one piece of timing state, an in-memory ``Timetable`` of each
enabled schedule's next run, kept in step with the database by
``Timetable.sync`` on every start and reload: an unchanged schedule keeps
its ``next_at`` (a run already due stays due and the next pass fires it),
a new, re-enabled or edited one is computed from the clock (nothing behind
the clock fires because of an edit), and a deleted or disabled one is
dropped. A start begins from an empty timetable, so its first sync computes
everything from the clock and never replays what came due while the app was
closed or the runtime paused. It sleeps until the soonest run or a reload,
whichever comes first, capped at ``schedule_max_sleep_seconds`` because the loop's
sleep runs on the monotonic clock, which stops while the machine is
suspended; the cap makes it re-read the wall clock. A run handled more than
``schedule_fire_grace_seconds`` late (a suspend) is recorded as missed.

A ``schedule_ran`` activity (persisted in the activity log) is broadcast
only for news: a fired run, or an outcome different from the last one, so
a streak of identical skips writes one row. A fired run's "Created" line in
the operator's DM is pushed live through ``broadcast_origin_line``.

The app signals edits through the durable ``reload_schedules`` runtime
command; the worker's command handler calls ``reload()``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Callable

import db
from core import config
from core.agent_loop.dispatcher import dispatcher
from core.runtime.events import runtime_events
from core.scheduling import runner
from core.scheduling.announce import announce_run
from core.scheduling.runner import ScheduleRun, ScheduleSettingError, ScheduleTiming
from core.scheduling.timetable import Timetable

logger = logging.getLogger(__name__)

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleWatch:
    """Fires each enabled schedule's occurrences as tasks, on the wall clock.

    Args:
        clock: Aware wall-clock UTC now; injectable for tests.
    """

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._reload_requested = False
        self._timetable = Timetable()
        self._timing: ScheduleTiming | None = None
        # Logged once per distinct message; cleared (at info) on recovery.
        self._setting_error: str | None = None
        self._loop_error: str | None = None

    def start(self) -> None:
        """Start the clock loop. A second call while running does nothing.

        The timing settings are read first; when they are unusable the watch
        does not start and says why at error level (no invented default). The
        timetable is loaded from the database as the loop's first step, so a
        corrupt row is logged by the loop rather than failing the worker's
        boot. The timetable starts empty, so that first sync computes every
        entry from the clock and runs missed while stopped are never replayed.
        """
        if self._running:
            return
        try:
            timing = runner.read_timing()
        except ScheduleSettingError as exc:
            logger.error("Schedule watch not started: %s", exc)
            return
        self._timing = timing
        self._setting_error = None
        self._timetable = Timetable()
        self._wake = asyncio.Event()
        self._reload_requested = True
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Schedule watch started")

    async def stop(self) -> None:
        """Stop the clock loop and wait for it to exit."""
        self._running = False
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        logger.info("Schedule watch stopped")

    def reload(self) -> None:
        """Sync the timetable with the database now, without waiting out the sleep.

        A no-op while not running: a start (boot or Resume) loads fresh anyway.
        """
        if not self._running or self._wake is None:
            return
        self._reload_requested = True
        self._wake.set()

    async def _loop(self) -> None:
        while self._running:
            try:
                config.refresh_if_changed()
                self._refresh_timing()
                if self._reload_requested:
                    self._sync()
                    self._reload_requested = False
                await self._run_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A database error or a corrupt row must not end the clock;
                # it retries after the next sleep, and says so once.
                detail = f"{type(exc).__name__}: {exc}"
                if detail != self._loop_error:
                    logger.exception("Schedule watch loop error")
                    self._loop_error = detail
            else:
                if self._loop_error is not None:
                    logger.info("Schedule watch recovered")
                    self._loop_error = None
            await self._sleep()

    def _refresh_timing(self) -> None:
        """Re-read the timing; an unusable value keeps the last valid one, logged once."""
        try:
            timing = runner.read_timing()
        except ScheduleSettingError as exc:
            if str(exc) != self._setting_error:
                logger.error("Schedule timing settings are unusable, keeping the last valid ones: %s", exc)
                self._setting_error = str(exc)
            return
        if self._setting_error is not None:
            logger.info("Schedule timing settings are usable again")
            self._setting_error = None
        self._timing = timing

    def _sync(self) -> None:
        """Bring the timetable in step with the enabled schedules (``Timetable.sync``).

        Unchanged schedules keep their ``next_at``; new or edited ones are
        computed from the clock; deleted or disabled ones are dropped.
        """
        schedules = db.list_enabled_schedules()
        self._timetable.sync(((item.id, item.recurrence) for item in schedules), now=self._clock())

    async def _sleep(self) -> None:
        """Wait until the soonest run, a reload, or the sleep cap, whichever is first."""
        if self._timing is None or self._wake is None:
            raise RuntimeError("the schedule watch loop is running without timing")
        cap = float(self._timing.max_sleep_seconds)
        next_due = self._timetable.next_due()
        wait = cap if next_due is None else min(max((next_due - self._clock()).total_seconds(), 0.0), cap)
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=wait)
        except TimeoutError:
            pass  # The normal way a sleep ends; the wall clock is read next.
        self._wake.clear()

    async def _run_due(self) -> None:
        """Handle every entry due now, advance each, and deliver what it produced."""
        if self._timing is None:
            raise RuntimeError("the schedule watch loop is running without timing")
        now = self._clock()
        fired = False
        for schedule_id, due_at in self._timetable.pop_due(now):
            try:
                run = await asyncio.to_thread(
                    runner.run_occurrence, schedule_id, due_at=due_at, now=now, timing=self._timing,
                )
            except Exception:
                # The entry is already out of the timetable, so the sync this
                # asks for recomputes it from the clock (the failed occurrence
                # is not replayed) while every other entry keeps its place.
                logger.exception("Schedule %s: the run due %s could not be handled", schedule_id, due_at)
                self.reload()
                continue
            if run is None:
                continue
            self._timetable.schedule(schedule_id, run.rule, after=now)
            try:
                await self._deliver(run)
            except Exception:
                # The outcome is already recorded; a created task left
                # unannounced still waits on the board for the watchdog.
                logger.exception("Schedule %s: delivering the %s run failed", schedule_id, run.outcome)
            fired = fired or run.outcome == "fired"
        if fired:
            await runtime_events.broadcast_world_state()

    async def _deliver(self, run: ScheduleRun) -> None:
        """Wake the agent for a fired run and tell the UI what happened.

        The announcement is ``announce.announce_run``'s: a fired run's DM
        line live, then ``task_created``; the persisted ``schedule_ran`` only
        when ``run.changed``.
        """
        if run.trigger is not None and not dispatcher.enqueue_trigger(**run.trigger):
            logger.info(
                "Schedule %s: task %s was created but its agent was not woken (vacation began, "
                "or the target is archived); it stays pending",
                run.schedule_id, run.task.id if run.task is not None else None,
            )
        agent = db.get_agent(run.agent_id)
        await announce_run(runtime_events, run, agent_name=agent.name if agent is not None else None)


schedule_watch = ScheduleWatch()
