"""BossMod AI — per-agent recurring schedule models.

A schedule is the rule for recurring work ("every weekday at 06:00 and
12:00") plus the last outcome the runtime worker recorded for it. Each
occurrence the worker fires becomes an ordinary Task for the agent.

``RecurrenceRule`` is the one closed shape of a rule. It is validated here,
at the boundary, so the pure math in ``core.scheduling.recurrence`` and the
database layer trust it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.models.notification import TaskNotificationPolicy
from core.models.task import Task, TaskStatus

ScheduleFrequency = Literal["daily", "weekly", "monthly"]
ScheduleOutcome = Literal["fired", "missed", "skipped_open", "skipped_vacation", "failed"]

# Inclusive interval bounds per frequency: up to a year of days, a year of
# weeks, or a year of months between runs.
INTERVAL_BOUNDS: dict[str, tuple[int, int]] = {
    "daily": (1, 365),
    "weekly": (1, 52),
    "monthly": (1, 12),
}
MAX_TIMES_PER_DAY = 12
# "Every" mode's longest step: every 12 hours.
MAX_EVERY_MINUTES = 720
TITLE_MAX_CHARS = 200
INSTRUCTIONS_MAX_CHARS = 4000
# How many upcoming runs one preview may ask for.
PREVIEW_MAX_COUNT = 20
# The schedule fields a scheduled task's ``task_assigned`` trigger payload
# carries (core/agent_loop/activity_scheduler.py ``_schedule_payload``),
# with the description the prompt editor shows for each ``trigger.<key>``
# variable (core/llm/context_builder.py).
SCHEDULE_TRIGGER_FIELDS: tuple[tuple[str, str], ...] = (
    ("schedule_title", "Title of the schedule that created this task (scheduled tasks only)"),
    ("schedule_summary", "The schedule's rule in words, e.g. 'Every weekday at 06:00' (scheduled tasks only)"),
    ("schedule_next_run", "The schedule's next run on the host's clock; empty while it is switched off"),
    ("schedule_enabled", "'true' or 'false': whether the schedule that created this task is on"),
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_text(value: str | None, *, label: str, max_chars: int) -> str:
    """Strip ``value`` and require 1..``max_chars`` characters.

    Raises:
        ValueError: The value is missing, blank, or too long.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError(f"A schedule {label} cannot be blank")
    if len(text) > max_chars:
        raise ValueError(f"A schedule {label} can be at most {max_chars} characters")
    return text


class RecurrenceRule(BaseModel):
    """When a schedule runs: a frequency, an interval, times of day, and an anchor.

    - ``daily``: every ``interval`` days from ``start_date``.
    - ``weekly``: on ``weekdays`` (Mon=0) of every ``interval``-th week,
      counted in whole Monday-started weeks from ``start_date``'s week.
    - ``monthly``: on ``month_day`` of every ``interval``-th month from
      ``start_date``'s month; a shorter month uses its last day.

    On each chosen day the rule runs in exactly one of two modes:

    - **At** set times: ``times``, wall-clock ``HH:MM`` on the host (the
      operator's desktop); ``every_minutes`` and the window are ``None``.
    - **Every** ``every_minutes`` (1..``MAX_EVERY_MINUTES``) within the window
      ``window_start``..``window_end`` (``HH:MM``, inclusive, same day):
      slots are aligned to the wall clock from ``window_start``; ``times`` is
      empty. A window that crosses midnight is refused (which day it belongs
      to would be ambiguous).

    A stored rule from before "Every" mode has none of its keys and parses as
    "At". The validator sorts ``times`` and ``weekdays``.

    Raises:
        pydantic.ValidationError: An unknown field; an interval out of its
            frequency's bounds; both modes or neither; in "At" mode more than
            ``MAX_TIMES_PER_DAY`` times, a time that is not ``HH:MM``
            (00:00-23:59), or a duplicate time; in "Every" mode a missing
            window end, ``every_minutes`` out of bounds, a window end that is
            not ``HH:MM``, or a window that ends before it starts;
            weekly without weekdays, weekdays outside 0..6 or repeated, or
            weekdays on another frequency; monthly without ``month_day``,
            a ``month_day`` outside 1..31, or one on another frequency.
    """

    model_config = ConfigDict(extra="forbid")

    frequency: ScheduleFrequency
    interval: int
    times: list[str] = []
    every_minutes: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    weekdays: list[int] = []
    month_day: int | None = None
    start_date: date

    @model_validator(mode="after")
    def _validate_rule(self) -> "RecurrenceRule":
        low, high = INTERVAL_BOUNDS[self.frequency]
        if not low <= self.interval <= high:
            raise ValueError(f"A {self.frequency} interval must be between {low} and {high}")
        repeat = (self.every_minutes, self.window_start, self.window_end)
        if self.times and any(value is not None for value in repeat):
            raise ValueError("Choose set times or a repeat, not both")
        if not self.times and all(value is None for value in repeat):
            raise ValueError("A schedule needs at least one time of day, or a repeat")
        if not self.times:
            self._validate_repeat()
        if len(self.times) > MAX_TIMES_PER_DAY:
            raise ValueError(f"A schedule can have at most {MAX_TIMES_PER_DAY} times of day")
        for value in self.times:
            if not _TIME_RE.match(value):
                raise ValueError(f"Time {value!r} is not a 24-hour HH:MM time")
        if len(set(self.times)) != len(self.times):
            raise ValueError("Each time of day can appear only once")
        self.times = sorted(self.times)
        if self.frequency == "weekly":
            if not self.weekdays:
                raise ValueError("A weekly schedule needs at least one weekday")
            if any(day < 0 or day > 6 for day in self.weekdays):
                raise ValueError("Weekdays run from 0 (Monday) to 6 (Sunday)")
            if len(set(self.weekdays)) != len(self.weekdays):
                raise ValueError("Each weekday can appear only once")
            self.weekdays = sorted(self.weekdays)
        elif self.weekdays:
            raise ValueError("Only a weekly schedule takes weekdays")
        if self.frequency == "monthly":
            if self.month_day is None:
                raise ValueError("A monthly schedule needs a day of the month")
            if not 1 <= self.month_day <= 31:
                raise ValueError("The day of the month must be between 1 and 31")
        elif self.month_day is not None:
            raise ValueError("Only a monthly schedule takes a day of the month")
        return self

    def _validate_repeat(self) -> None:
        """Check "Every" mode's three fields.

        Raises:
            ValueError: A missing field, ``every_minutes`` out of bounds, a
                window end that is not ``HH:MM``, or an overnight window.
        """
        if self.every_minutes is None:
            raise ValueError("A repeating schedule needs how many minutes apart its runs are")
        if self.window_start is None or self.window_end is None:
            raise ValueError("A repeating schedule needs a window start and end")
        if not 1 <= self.every_minutes <= MAX_EVERY_MINUTES:
            raise ValueError(f"A repeat must be every 1 to {MAX_EVERY_MINUTES} minutes")
        for value in (self.window_start, self.window_end):
            if not _TIME_RE.match(value):
                raise ValueError(f"Window time {value!r} is not a 24-hour HH:MM time")
        # Zero-padded HH:MM compares correctly as text.
        if self.window_end < self.window_start:
            raise ValueError("The window must end after it starts (overnight windows are not supported)")


class AgentSchedule(BaseModel):
    """One stored schedule: the rule, what each run creates, and the last outcome.

    ``last_task_id`` is the task the last *fired* occurrence created; the
    runner reads it to skip an occurrence while that task is still open.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    title: str
    instructions: str
    recurrence: RecurrenceRule
    notification_policy: TaskNotificationPolicy
    enabled: bool
    last_occurrence_at: datetime | None = None
    last_outcome: ScheduleOutcome | None = None
    last_outcome_detail: str | None = None
    last_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleCreate(BaseModel):
    """Payload accepted by POST /api/agents/{agent_id}/schedules.

    ``title`` becomes each run's task title and ``instructions`` its
    description. ``notification_policy`` defaults to the same
    ``completion_blocked`` that POST /api/tasks uses.

    Raises:
        pydantic.ValidationError: An unknown field, a blank or over-long
            title (200) or instructions (4000), or an invalid rule.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    instructions: str
    recurrence: RecurrenceRule
    notification_policy: TaskNotificationPolicy = "completion_blocked"
    enabled: bool = True

    @model_validator(mode="after")
    def _clean(self) -> "ScheduleCreate":
        self.title = _clean_text(self.title, label="title", max_chars=TITLE_MAX_CHARS)
        self.instructions = _clean_text(
            self.instructions, label="instructions", max_chars=INSTRUCTIONS_MAX_CHARS,
        )
        return self


class ScheduleUpdate(BaseModel):
    """Payload accepted by PATCH /api/schedules/{schedule_id}: the operator's edit.

    Only the fields present in the body change (``model_fields_set``). None
    of them may be sent as ``null``: every stored field is required.

    Raises:
        pydantic.ValidationError: No field is present, an unknown field is
            sent, a present field is ``null``, or ``title``/``instructions``
            is blank or too long, or ``recurrence`` is invalid.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    instructions: str | None = None
    recurrence: RecurrenceRule | None = None
    notification_policy: TaskNotificationPolicy | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate_changes(self) -> "ScheduleUpdate":
        present = self.model_fields_set
        if not present:
            raise ValueError(
                "A schedule edit needs at least one of title, instructions, recurrence, "
                "notification_policy or enabled"
            )
        for name in present:
            if getattr(self, name) is None:
                raise ValueError(f"A schedule's {name} cannot be null")
        if "title" in present:
            self.title = _clean_text(self.title, label="title", max_chars=TITLE_MAX_CHARS)
        if "instructions" in present:
            self.instructions = _clean_text(
                self.instructions, label="instructions", max_chars=INSTRUCTIONS_MAX_CHARS,
            )
        return self


class ScheduleView(AgentSchedule):
    """A schedule as the API returns it, with what the UI shows computed server-side.

    ``summary`` is ``core.scheduling.recurrence.describe``'s wording,
    ``next_run_at`` the next occurrence after the request time (``None``
    while disabled), and ``last_task_status`` the status of the last fired
    run's task (``None`` when there is none, or it no longer exists).
    """

    summary: str
    next_run_at: datetime | None = None
    last_task_status: TaskStatus | None = None


class ScheduleRunResult(BaseModel):
    """POST /api/schedules/{id}/run: the schedule after the run, and the task it created."""

    schedule: ScheduleView
    task: Task


class SchedulePreviewRequest(BaseModel):
    """Payload accepted by POST /api/schedules/preview: a draft rule and how many runs to list.

    Raises:
        pydantic.ValidationError: An unknown field, an invalid rule, or
            ``count`` outside 1..``PREVIEW_MAX_COUNT``.
    """

    model_config = ConfigDict(extra="forbid")

    recurrence: RecurrenceRule
    count: int = Field(ge=1, le=PREVIEW_MAX_COUNT)


class SchedulePreview(BaseModel):
    """The draft rule's summary and its next runs, soonest first (aware UTC)."""

    summary: str
    next_runs: list[datetime]
