"""BossMod AI — one schedule occurrence, synchronously, against the database.

Runs inside the runtime worker (core/scheduling/watch.py calls it through
``asyncio.to_thread``); kept apart from the async loop so every outcome is
testable without asyncio timing.

For one due entry it re-reads the row (a deleted or disabled schedule is
dropped, so stale memory in the worker is always harmless), classifies the
occurrence, records exactly one outcome on the row, and hands back what the
loop must deliver. Delivery is at most once per occurrence: a missed,
skipped or failed run is recorded and never retried.

``run_now`` is the operator's "Run now" (api/routes/schedules.py, in the app
process): one real occurrence of the saved schedule, enabled or not, created
through the same ``_fire`` as a scheduled run. It refuses for the same two
reasons a scheduled run skips (``_refusal``, shared so the paths cannot
drift), but says so instead of recording a skip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import db
from core import config
from core.agent_loop.activity_scheduler import assignment_wake_trigger
from core.floors import is_on_vacation
from core.models import Task
from core.models.message import HUMAN_SENDER_ID
from core.models.schedule import AgentSchedule, RecurrenceRule, ScheduleOutcome
from core.scheduling.recurrence import describe
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import TERMINAL_TASK_STATUSES

logger = logging.getLogger(__name__)

MAX_SLEEP_KEY = "schedule_max_sleep_seconds"
GRACE_KEY = "schedule_fire_grace_seconds"


class ScheduleSettingError(Exception):
    """The schedule timing settings are missing, not integers, or inconsistent."""


class ScheduleRunRefused(Exception):
    """A run would not start now, for the reason a scheduled run would skip.

    Attributes:
        reason: ``vacation`` (the agent is on vacation) or ``open`` (the last
            run's task is still open).
        message: The operator-facing sentence.
        task_id: The open task for ``open``; ``None`` otherwise.
    """

    def __init__(self, reason: Literal["vacation", "open"], message: str, task_id: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.task_id = task_id


class ScheduleRunFailed(Exception):
    """Creating a Run-now task raised; ``detail`` (``"<Type>: <message>"``) was recorded as ``failed``."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ScheduleTiming:
    """The schedule clock's two tunables, in whole seconds.

    Attributes:
        max_sleep_seconds: Longest the watch sleeps before re-reading the
            wall clock (the loop's sleep stops during machine suspend).
        grace_seconds: How late an occurrence may be handled and still
            fire; later is ``missed``. Always greater than
            ``max_sleep_seconds``.
    """

    max_sleep_seconds: int
    grace_seconds: int


@dataclass(frozen=True, slots=True)
class ScheduleRun:
    """What one handled occurrence produced, for the watch loop to deliver.

    Attributes:
        schedule_id: The schedule that came due.
        agent_id: Its agent.
        title: Its title, for the activity lines.
        outcome: The outcome recorded on the row.
        task: The created task (``fired`` only).
        trigger: The ``task_assigned`` spec to enqueue (``fired`` only, and
            only when ``assignment_wake_trigger`` returns one).
        rule: The rule as re-read now, which the loop advances from.
        changed: Whether this run is news: the outcome differs from the
            previous ``last_outcome``, or it fired. The loop persists a
            ``schedule_ran`` activity only then, so a streak of identical
            skips writes one row.
        origin_line: The "Created" line the fired task's creation persisted
            in the operator's DM (``broadcast_origin_line`` shape); empty
            unless fired, or when the task has no origin thread.
    """

    schedule_id: str
    agent_id: str
    title: str
    outcome: ScheduleOutcome
    task: Task | None
    trigger: dict[str, Any] | None
    rule: RecurrenceRule
    changed: bool
    origin_line: dict[str, Any]


def read_timing() -> ScheduleTiming:
    """Read and check the two timing settings.

    Returns:
        The timing.

    Raises:
        ScheduleSettingError: Either setting is missing or not an integer,
            either is below 1, or the grace window is not longer than one
            capped sleep; a healthy loop would then record its own due times
            as missed.
    """
    try:
        max_sleep = config.require_int(MAX_SLEEP_KEY)
        grace = config.require_int(GRACE_KEY)
    except config.ConfigError as exc:
        raise ScheduleSettingError(str(exc)) from exc
    if max_sleep < 1 or grace < 1:
        raise ScheduleSettingError(f"{MAX_SLEEP_KEY} and {GRACE_KEY} must both be at least 1")
    if grace <= max_sleep:
        raise ScheduleSettingError(
            f"{GRACE_KEY} ({grace}) must be greater than {MAX_SLEEP_KEY} ({max_sleep})"
        )
    return ScheduleTiming(max_sleep_seconds=max_sleep, grace_seconds=grace)


def run_occurrence(
    schedule_id: str,
    *,
    due_at: datetime,
    now: datetime,
    timing: ScheduleTiming,
) -> ScheduleRun | None:
    """Handle one due occurrence and record its outcome.

    In order: handled later than ``grace_seconds`` after ``due_at`` is
    ``missed`` (the machine slept); the agent on vacation is
    ``skipped_vacation``; the last fired run's task still open is
    ``skipped_open``; otherwise a task is created (``fired``) or creating it
    raised (``failed``, with ``"<Type>: <message>"`` as the detail, logged at
    warning when the detail changed).

    Args:
        schedule_id: The schedule the timetable says is due.
        due_at: The instant it was due.
        now: The wall-clock instant it is being handled.
        timing: The current timing settings.

    Returns:
        The run, or ``None`` when the schedule was deleted or disabled
        (the caller drops its entry).

    Raises:
        pydantic.ValidationError: The stored rule is corrupt.
        LookupError: The schedule was deleted between the read and the
            record (``db.record_outcome``).
    """
    schedule = db.get_schedule(schedule_id)
    if schedule is None or not schedule.enabled:
        return None

    def done(outcome: ScheduleOutcome, *, task: Task | None = None, trigger: dict[str, Any] | None = None,
             detail: str | None = None, origin_line: dict[str, Any] | None = None) -> ScheduleRun:
        db.record_outcome(
            schedule.id, occurrence=due_at, outcome=outcome, detail=detail,
            task_id=task.id if task is not None else None,
        )
        return ScheduleRun(
            schedule_id=schedule.id, agent_id=schedule.agent_id, title=schedule.title,
            outcome=outcome, task=task, trigger=trigger, rule=schedule.recurrence,
            changed=outcome == "fired" or outcome != schedule.last_outcome,
            origin_line=origin_line or {},
        )

    if (now - due_at).total_seconds() > timing.grace_seconds:
        return done("missed")
    refusal = _refusal(schedule)
    if refusal is not None:
        return done("skipped_vacation" if refusal.reason == "vacation" else "skipped_open")
    try:
        task, trigger, origin_line = _fire(schedule, due_at, manual=False)
    except Exception as exc:
        # One bad schedule must not stop the loop (as in
        # ExtensionWakeWatch.run_once); the failure is recorded on the row
        # and logged, never silent, and the run is not retried.
        detail = f"{type(exc).__name__}: {exc}"
        if schedule.last_outcome != "failed" or schedule.last_outcome_detail != detail:
            logger.warning("Schedule %s (%s): creating the task failed: %s", schedule.id, schedule.title, detail)
        return done("failed", detail=detail)
    return done("fired", task=task, trigger=trigger, origin_line=origin_line)


def run_now(schedule_id: str, *, now: datetime) -> ScheduleRun:
    """Run the saved schedule once, now, as a real occurrence ("Run now").

    Works whether the schedule is enabled or not, and never enables it. The
    task is created through ``_fire`` exactly as a scheduled run's is, with
    the manual description line; ``fired`` is recorded with
    ``last_occurrence_at = now``. The worker's timetable is not touched, so
    the next scheduled run stays where it was.

    Args:
        schedule_id: The schedule to run.
        now: The aware instant of the click.

    Returns:
        The run (``changed=True``), with the task, its wake trigger spec and
        the persisted DM origin line for the caller to deliver.

    Raises:
        LookupError: No such schedule.
        ScheduleRunRefused: The agent is on vacation, or the last run's task
            is still open. Nothing is recorded: no occurrence happened.
        ScheduleRunFailed: Creating the task raised; ``failed`` was recorded
            with the detail, as for a scheduled run.
        pydantic.ValidationError: The stored rule is corrupt.
    """
    schedule = db.get_schedule(schedule_id)
    if schedule is None:
        raise LookupError("Schedule not found")
    refusal = _refusal(schedule)
    if refusal is not None:
        raise refusal
    try:
        task, trigger, origin_line = _fire(schedule, now, manual=True)
    except Exception as exc:
        # Recorded and raised to the operator who clicked, never swallowed.
        detail = f"{type(exc).__name__}: {exc}"
        logger.warning("Schedule %s (%s): Run now failed: %s", schedule.id, schedule.title, detail)
        db.record_outcome(schedule.id, occurrence=now, outcome="failed", detail=detail, task_id=None)
        raise ScheduleRunFailed(detail) from exc
    db.record_outcome(schedule.id, occurrence=now, outcome="fired", detail=None, task_id=task.id)
    return ScheduleRun(
        schedule_id=schedule.id, agent_id=schedule.agent_id, title=schedule.title, outcome="fired",
        task=task, trigger=trigger, rule=schedule.recurrence, changed=True, origin_line=origin_line,
    )


def _refusal(schedule: AgentSchedule) -> ScheduleRunRefused | None:
    """Why a run of ``schedule`` should not start now, or ``None``.

    The one rule both paths apply: the agent on vacation, then the last
    fired run's task still open (a run is not stacked on a stuck one). A
    scheduled run records it as a skip; Run now raises it.

    Returns:
        The refusal (not raised), or ``None`` when a run may start.
    """
    agent = db.get_agent(schedule.agent_id)
    if is_on_vacation(agent):
        return ScheduleRunRefused("vacation", f"{agent.name} is on vacation")
    if schedule.last_task_id:
        previous = db.get_task(schedule.last_task_id)
        if previous is not None and previous.status not in TERMINAL_TASK_STATUSES:
            return ScheduleRunRefused("open", "The last run is still open", task_id=previous.id)
    return None


def _fire(
    schedule: AgentSchedule, due_at: datetime, *, manual: bool,
) -> tuple[Task, dict[str, Any] | None, dict[str, Any]]:
    """Create this occurrence's task, its wake trigger spec, and its persisted origin line.

    The task is the operator's (``requester=HUMAN_SENDER_ID``, source
    ``api``), so its status lines land in the operator's DM with the agent
    through ``origin_thread_target`` unless the policy is ``none``. The
    description's last line tells a scheduled run (``Scheduled run: … — due
    …``) from a Run now (``Manual run (Run now): …``).

    Raises:
        RuntimeError: Creation returned no task (an invariant breach).
        Whatever ``create_or_bind_task`` raises (``FloorDenied``,
        ``ValueError``, a database error).
    """
    summary = describe(schedule.recurrence)
    if manual:
        trailer = f"Manual run (Run now): {summary}"
    else:
        local = due_at.astimezone()
        trailer = f"Scheduled run: {summary} — due {local:%H:%M}, {local:%a} {local.day} {local:%b %Y}"
    result = create_or_bind_task(
        title=schedule.title,
        description=f"{schedule.instructions}\n\n{trailer}",
        project=None,
        assigned_to=schedule.agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="api",
        notification_policy=schedule.notification_policy,
        notification_channel_id=None,
        audit_author_name="Schedule",
        audit_author_type="system",
        audit_event_type="assignment",
        schedule_id=schedule.id,
    )
    if result.task is None:
        raise RuntimeError(f"creating a run of schedule {schedule.id} returned no task ({result.outcome})")
    return result.task, assignment_wake_trigger(result.task), result.origin_line
