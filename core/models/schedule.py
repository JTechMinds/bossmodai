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

from pydantic import BaseModel, ConfigDict, model_validator

from core.models.notification import TaskNotificationPolicy
from core.models.task import TaskStatus

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
TITLE_MAX_CHARS = 200
INSTRUCTIONS_MAX_CHARS = 4000

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

    ``times`` are wall-clock ``HH:MM`` on the host (the operator's desktop).
    The validator sorts ``times`` and ``weekdays``.

    Raises:
        pydantic.ValidationError: An unknown field; an interval out of its
            frequency's bounds; no times, more than ``MAX_TIMES_PER_DAY``,
            a time that is not ``HH:MM`` (00:00-23:59), or a duplicate time;
            weekly without weekdays, weekdays outside 0..6 or repeated, or
            weekdays on another frequency; monthly without ``month_day``,
            a ``month_day`` outside 1..31, or one on another frequency.
    """

    model_config = ConfigDict(extra="forbid")

    frequency: ScheduleFrequency
    interval: int
    times: list[str]
    weekdays: list[int] = []
    month_day: int | None = None
    start_date: date

    @model_validator(mode="after")
    def _validate_rule(self) -> "RecurrenceRule":
        low, high = INTERVAL_BOUNDS[self.frequency]
        if not low <= self.interval <= high:
            raise ValueError(f"A {self.frequency} interval must be between {low} and {high}")
        if not self.times:
            raise ValueError("A schedule needs at least one time of day")
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
