"""BossMod AI — Runtime activity domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ActivityKind = Literal[
    "assignment",
    "break",
    "conversation",
    "meeting",
    "movement",
    "social",
    "work",
]

ActivityStatus = Literal["active", "paused", "completed", "cancelled"]


class Activity(BaseModel):
    """A durable runtime activity for a single agent."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    kind: ActivityKind
    status: ActivityStatus = "active"
    task_id: str | None = None
    parent_activity_id: str | None = None
    title: str | None = None
    detail: str | None = None
    destination: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
