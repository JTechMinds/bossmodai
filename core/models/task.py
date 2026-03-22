"""BossMod AI — Task Pydantic models.

Defines the Task model used for project work items assigned to agents,
plus the API input model for creating new tasks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Literal type for task status
# ---------------------------------------------------------------------------

TaskStatus = Literal[
    "pending",
    "active",
    "blocked",
    "complete",
    "stalled",
    "abandoned",
    "delegated",
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
    created_by: str | None = None
    status: TaskStatus = "pending"
    parent_task_id: str | None = None
    cost_ceiling: float | None = None
    completion_summary: str | None = None
    status_note: str | None = None
    watchdog_pinged_at: datetime | None = None
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
