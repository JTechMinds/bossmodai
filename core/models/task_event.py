"""BossMod AI — Durable task thread models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


TaskEventType = Literal[
    "comment",
    "clarification",
    "answer",
    "status_update",
    "blocker",
    "completion",
    "assignment",
    "reprioritized",
    "system",
]

TaskEventAuthorType = Literal["human", "agent", "system"]


class TaskEvent(BaseModel):
    """One durable task-thread event attached to a task."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    author_type: TaskEventAuthorType
    author_agent_id: str | None = None
    author_name: str
    event_type: TaskEventType
    content: str
    source_message_id: str | None = None
    source_trigger_id: str | None = None
    created_at: datetime
