"""BossMod AI — Trigger queue domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


TriggerStatus = Literal["queued", "claimed", "completed", "failed"]
TriggerChannel = Literal["chat", "channel", "work", "system"]


class AgentTrigger(BaseModel):
    """A durable wake-up event for an agent."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    trigger_type: str
    source_channel: TriggerChannel
    payload: str
    task_id: str | None = None
    status: TriggerStatus = "queued"
    retry_count: int = 0
    failure_reason: str | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    created_at: datetime
