"""BossMod AI — Task Pydantic models.

Defines the Task model used for project work items assigned to agents,
plus the API input model for creating new tasks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.models.notification import NotificationSourceChannel, TaskNotificationPolicy
from core.models.work_contract import WorkContract


# ---------------------------------------------------------------------------
# Literal type for task status
# ---------------------------------------------------------------------------

TaskStatus = Literal[
    "pending",
    "accepted",
    "active",
    "waiting",
    "blocked",
    "complete",
    "stalled",
    "abandoned",
    "delegated",
    "declined",
    "cancelled",
]

# How serious a task is, for triage:
# P0 critical — broken or unsafe now; stop and fix.
# P1 high     — a major function is broken with no workaround.
# P2 medium   — broken with a workaround, or degraded.
# P3 low      — cosmetic or minor.
TaskSeverity = Literal["P0", "P1", "P2", "P3"]

# Every task has a severity; anything not rated otherwise is P3. The SQL
# column default (db/schema.sql, db/connection.py) is the only other place
# this value is written.
DEFAULT_TASK_SEVERITY: TaskSeverity = "P3"

# The severity and reference fields every ``task_assigned`` trigger payload
# carries (core/agent_loop/activity_scheduler.py ``build_task_assigned_trigger``),
# with the description the prompt editor shows for each ``trigger.<key>``
# variable (core/llm/context_builder.py), as SCHEDULE_TRIGGER_FIELDS does.
TASK_TRIGGER_FIELDS: tuple[tuple[str, str], ...] = (
    ("task_severity", "Assigned task severity (P0 critical … P3 low)"),
    ("task_references", "Assigned task reference documents, one '- path — description' line each, when present"),
)


class TaskReference(BaseModel):
    """A document the assignee reads before starting: input material.

    The opposite of ``DeliverableSpec``, which is output the assignee must
    produce. ``path`` is a BossMod CLI path (``/projects/...``); the rules
    for which paths a reference may name live in ``core/tasking/references.py``.

    Raises:
        pydantic.ValidationError: ``path`` is blank or an unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    description: str | None = None

    @field_validator("path")
    @classmethod
    def _strip_path(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError('reference "path" must be non-empty')
        return text


# ---------------------------------------------------------------------------
# Task — a unit of work
# ---------------------------------------------------------------------------

class Task(BaseModel):
    """A work item that can be assigned to an agent, optionally nested
    under a parent task for sub-task decomposition."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None = None
    project: str | None = None
    assigned_to: str | None = None
    requester_id: str | None = None
    owner_id: str | None = None
    created_by: str | None = None
    status: TaskStatus = "pending"
    severity: TaskSeverity = DEFAULT_TASK_SEVERITY
    work_contract: WorkContract | None = None
    # Documents the assignee reads first (task_references side table).
    references: list[TaskReference] = Field(default_factory=list)
    source_channel: NotificationSourceChannel | None = None
    notification_policy: TaskNotificationPolicy | None = None
    notification_channel_id: str | None = None
    notification_policy_updated_at: datetime | None = None
    parent_task_id: str | None = None
    # The schedule whose occurrence created this task (core/scheduling), or
    # None for every other task. Detached to None when the schedule is deleted.
    schedule_id: str | None = None
    cost_ceiling: float | None = None
    completion_summary: str | None = None
    status_note: str | None = None
    watchdog_pinged_at: datetime | None = None
    last_progress_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_activity: datetime
    created_at: datetime
    closed_at: datetime | None = None


# ---------------------------------------------------------------------------
# API input models
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    """Payload accepted by POST /api/tasks to create a new task."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    description: str | None = None
    project: str | None = None
    assigned_to: str | None = None
    requester_id: str | None = None
    owner_id: str | None = None
    parent_task_id: str | None = None
    bind_task_id: str | None = None
    work_contract: WorkContract | None = None
    source_channel: NotificationSourceChannel | None = None
    notification_policy: TaskNotificationPolicy | None = None
    notification_channel_id: str | None = None


class TaskCancelRequest(BaseModel):
    """Payload accepted by POST /api/tasks/cancel to kill selected tasks."""

    model_config = ConfigDict(from_attributes=True)

    task_ids: list[str] = Field(min_length=1)


class TaskUpdateRequest(BaseModel):
    """Payload accepted by PATCH /api/tasks/{id}: the operator's edit of an open task.

    Only the fields present in the body change; ``model_fields_set`` tells an
    omitted field apart from one set to ``null``. ``assigned_to: null``
    unassigns; ``work_contract: null`` (or an empty ``deliverables`` list)
    drops the file requirement. ``severity`` can be changed but never
    cleared, so ``severity: null`` is refused.

    Raises:
        pydantic.ValidationError: No editable field is present, ``title`` is
            blank, ``severity`` is null or not P0–P3, or an unknown field is
            sent.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    assigned_to: str | None = None
    work_contract: WorkContract | None = None
    severity: TaskSeverity | None = None

    @model_validator(mode="after")
    def _validate_changes(self) -> "TaskUpdateRequest":
        editable = {"title", "description", "assigned_to", "work_contract", "severity"}
        if not editable & self.model_fields_set:
            raise ValueError(
                "A task edit needs at least one of title, description, assigned_to, work_contract or severity"
            )
        if "severity" in self.model_fields_set and self.severity is None:
            raise ValueError("A task severity cannot be cleared; choose P0, P1, P2 or P3")
        if "title" in self.model_fields_set:
            title = (self.title or "").strip()
            if not title:
                raise ValueError("A task title cannot be blank")
            self.title = title
        return self


class BacklogItemCreate(BaseModel):
    """The body of an agent's ``backlog add``: one unassigned backlog item to file.

    ``severity`` may be left out and is then P3 (``DEFAULT_TASK_SEVERITY``);
    ``model_fields_set`` tells the caller whether it was given. Description
    is optional, as on ``TaskCreate``.

    Raises:
        pydantic.ValidationError: ``title`` is blank, ``severity`` is not
            P0–P3, a reference is malformed, or an unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    severity: TaskSeverity = DEFAULT_TASK_SEVERITY
    description: str | None = None
    project: str | None = None
    references: list[TaskReference] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A backlog item needs a title")
        return text

    @field_validator("project")
    @classmethod
    def _strip_project(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None


class TaskCompleteRequest(BaseModel):
    """Payload accepted by POST /api/tasks/{id}/complete: the operator marks a task done.

    ``summary`` is why the operator considers it done; it becomes the
    completion summary, the task event and the origin thread line.

    Raises:
        pydantic.ValidationError: ``summary`` is blank or an unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)

    @field_validator("summary")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A completion summary cannot be blank")
        return text


TaskCreateOutcome = Literal[
    "create_new_task",
    "bind_existing_task",
    "clarify_ambiguous_match",
]


class TaskCandidateSummary(BaseModel):
    """One open task that matched an assign/reuse request."""

    id: str
    title: str
    status: TaskStatus
    assigned_to: str | None = None
    assigned_to_name: str | None = None
    last_activity: datetime | None = None


class TaskCreateResponse(BaseModel):
    """POST /api/tasks result, including whether the workstream was reused."""

    task: Task | None = None
    outcome: TaskCreateOutcome
    candidates: list[TaskCandidateSummary] = []
    reason: str | None = None
