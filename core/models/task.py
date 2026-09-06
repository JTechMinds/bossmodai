"""BossMod AI — Task Pydantic models.

Defines the Task model used for project work items assigned to agents,
plus the API input model for creating new tasks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
]


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
    work_contract: WorkContract | None = None
    work_contract_updated_at: datetime | None = None
    source_channel: NotificationSourceChannel | None = None
    notification_policy: TaskNotificationPolicy | None = None
    notification_channel_id: str | None = None
    notification_policy_updated_at: datetime | None = None
    parent_task_id: str | None = None
    cost_ceiling: float | None = None
    completion_summary: str | None = None
    status_note: str | None = None
    watchdog_pinged_at: datetime | None = None
    last_progress_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_activity: datetime
    created_at: datetime


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
    requested_specialty: str | None = None
    confirm_specialty_mismatch: bool = False


TaskCreateOutcome = Literal[
    "create_new_task",
    "bind_existing_task",
    "clarify_ambiguous_match",
    "specialty_mismatch",
]


class TaskCandidateSummary(BaseModel):
    """One open task that matched an assign/reuse request."""

    id: str
    title: str
    status: TaskStatus
    assigned_to: str | None = None
    assigned_to_name: str | None = None
    last_activity: datetime | None = None


class AssigneeSuggestion(BaseModel):
    """One teammate suggested when assign routing prefers a specialty match."""

    id: str
    name: str
    role: str | None = None
    match: Literal["match", "unknown", "mismatch"] = "unknown"


class TaskCreateResponse(BaseModel):
    """POST /api/tasks result, including whether the workstream was reused."""

    task: Task | None = None
    outcome: TaskCreateOutcome
    candidates: list[TaskCandidateSummary] = []
    reason: str | None = None
    specialty_warning: str | None = None
    suggested_assignees: list[AssigneeSuggestion] = Field(default_factory=list)
