"""BossMod AI — one schedule occurrence, synchronously, against the database.

Runs inside the runtime worker (core/scheduling/watch.py calls it through
``asyncio.to_thread``); kept apart from the async loop so every outcome is
testable without asyncio timing.

For one due entry it re-reads the row (a deleted or disabled schedule is
dropped, so stale memory in the worker is always harmless), classifies the
occurrence, records exactly one outcome on the row, and hands back what the
loop must deliver. Delivery is at most once per occurrence: a missed,
skipped or failed run is recorded and never retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
    """

    schedule_id: str
    agent_id: str
    title: str
    outcome: ScheduleOutcome
    task: Task | None
    trigger: dict[str, Any] | None
    rule: RecurrenceRule


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
             detail: str | None = None) -> ScheduleRun:
        db.record_outcome(
            schedule.id, occurrence=due_at, outcome=outcome, detail=detail,
            task_id=task.id if task is not None else None,
        )
        return ScheduleRun(
            schedule_id=schedule.id, agent_id=schedule.agent_id, title=schedule.title,
            outcome=outcome, task=task, trigger=trigger, rule=schedule.recurrence,
        )

    if (now - due_at).total_seconds() > timing.grace_seconds:
        return done("missed")
    if is_on_vacation(db.get_agent(schedule.agent_id)):
        return done("skipped_vacation")
    if schedule.last_task_id:
        previous = db.get_task(schedule.last_task_id)
        if previous is not None and previous.status not in TERMINAL_TASK_STATUSES:
            return done("skipped_open")
    try:
        task, trigger = _fire(schedule, due_at)
    except Exception as exc:
        # One bad schedule must not stop the loop (as in
        # ExtensionWakeWatch.run_once); the failure is recorded on the row
        # and logged, never silent, and the run is not retried.
        detail = f"{type(exc).__name__}: {exc}"
        if schedule.last_outcome != "failed" or schedule.last_outcome_detail != detail:
            logger.warning("Schedule %s (%s): creating the task failed: %s", schedule.id, schedule.title, detail)
        return done("failed", detail=detail)
    return done("fired", task=task, trigger=trigger)


def _fire(schedule: AgentSchedule, due_at: datetime) -> tuple[Task, dict[str, Any] | None]:
    """Create this occurrence's task and its wake trigger spec.

    The task is the operator's (``requester=HUMAN_SENDER_ID``, source
    ``api``), so its status lines land in the operator's DM with the agent
    through ``origin_thread_target`` unless the policy is ``none``.

    Raises:
        RuntimeError: Creation returned no task (an invariant breach).
        Whatever ``create_or_bind_task`` raises (``FloorDenied``,
        ``ValueError``, a database error).
    """
    local = due_at.astimezone()
    due = f"{local:%H:%M}, {local:%a} {local.day} {local:%b %Y}"
    result = create_or_bind_task(
        title=schedule.title,
        description=f"{schedule.instructions}\n\nScheduled run: {describe(schedule.recurrence)} — due {due}",
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
    return result.task, assignment_wake_trigger(result.task)
