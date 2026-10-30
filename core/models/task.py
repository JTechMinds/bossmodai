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
    "held",
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
    (
        "task_prerequisites",
        "Tasks that finished before this one could start, one '- title (assignee): summary; files: paths' "
        "line each, when present",
    ),
    (
        "task_request",
        "Assigned task's request, word for word: its own, or the one behind its parent task, when present",
    ),
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


class TaskRequestMessage(BaseModel):
    """One message, word for word, that asked for the work a task records.

    Input the assignee works from, as ``references`` are, but a different
    kind: ``description`` is the brief the accepting agent wrote,
    ``references`` are documents to read, and a request message is what the
    requester actually said. The system saves it when a turn turns a message
    into a task, a backlog item or a schedule; the rules (which message,
    append only, forwarding to a child) live in ``core/tasking/requests.py``.

    Attributes:
        author_type: ``human`` (the boss), ``agent`` or ``system``.
        author_agent_id: The agent's id for an ``agent`` author; ``None``
            for every other author.
        author_name: The author's name when it was saved, an audit snapshot.
            A human is always shown with the current boss label instead.
            Stripped; never blank.
        content: The message exactly as sent, newlines included. Never blank
            (judged after stripping), and stored stripped.

    Raises:
        pydantic.ValidationError: ``content`` or ``author_name`` is blank, an
            ``agent`` author has no ``author_agent_id``, another author has
            one, ``author_type`` is not one of the three, or an unknown
            field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    # The same three author types the message tables allow (db/schema.sql).
    author_type: Literal["human", "agent", "system"]
    author_agent_id: str | None = None
    author_name: str
    content: str

    @field_validator("author_name")
    @classmethod
    def _strip_author_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a request message needs its author's name")
        return text

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a request message needs content")
        return text

    @model_validator(mode="after")
    def _agent_id_matches_author_type(self) -> "TaskRequestMessage":
        if self.author_type == "agent" and not (self.author_agent_id or "").strip():
            raise ValueError("an agent's request message needs author_agent_id")
        if self.author_type != "agent" and self.author_agent_id is not None:
            raise ValueError(f"a {self.author_type} request message has no author_agent_id")
        return self


# Where one checklist item stands: still to do, done, or not needed.
TaskChecklistStatus = Literal["open", "done", "skipped"]


class TaskChecklistItem(BaseModel):
    """One part of the assignee's own plan for a task: an item on its checklist.

    The assignee writes the checklist to work a large task in parts and
    checks the parts off as it goes; the rules (append only, resolve by
    number) live in ``core/tasking/checklist.py``. Items are numbered by
    position from 1 and are never removed or reordered, so a number names the
    same item for the life of the task.

    Attributes:
        text: The part, in the agent's words. Stripped; never blank.
        status: ``open`` until the agent marks it ``done`` or ``skipped``.
        note: What the agent found or where the result is (``done``), or why
            the part is not needed (``skipped``). Stripped; blank is ``None``.

    Raises:
        pydantic.ValidationError: ``text`` is blank, ``status`` is not
            open, done or skipped, a ``skipped`` item has no note, or an
            unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    status: TaskChecklistStatus = "open"
    note: str | None = None

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a checklist item needs text")
        return text

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _skipped_needs_a_reason(self) -> "TaskChecklistItem":
        if self.status == "skipped" and self.note is None:
            raise ValueError("a skipped checklist item needs a note saying why it is not needed")
        return self


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
    # The assignee's checklist, in item order (task_checklists side table).
    checklist: list[TaskChecklistItem] = Field(default_factory=list)
    # Ids of the tasks this one waits on, in insertion order (task_dependencies
    # side table). Met or not: every edge written at creation is listed.
    prerequisites: list[str] = Field(default_factory=list)
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
    origin_attachment_ids: list[str] | None = None
    # The message(s) that asked for this work, oldest first (the
    # tasks.request_messages JSON column; core/tasking/requests.py).
    request_messages: list[TaskRequestMessage] = Field(default_factory=list)


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


class TaskResumeRequest(BaseModel):
    """Payload accepted by POST /api/tasks/{id}/resume: the operator hands blocked work back.

    ``note`` is an optional hint for the assignee, in the operator's words.
    It is stripped, and a blank note becomes ``None`` (nothing to say). A
    missing body is valid too: the route treats it as no note.

    Raises:
        pydantic.ValidationError: An unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = None

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None


class TaskHoldRequest(BaseModel):
    """Payload accepted by POST /api/tasks/{id}/hold: the operator parks unstarted work.

    ``note`` is why, in the operator's words. It is stripped, and a blank
    note becomes ``None``. A missing body is valid too: the route treats it
    as no note.

    Raises:
        pydantic.ValidationError: An unknown field is sent.
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = None

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None


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
