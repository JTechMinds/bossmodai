"""BossMod AI — Runtime control-plane domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


RuntimeCommandType = Literal[
    "wake_dispatcher",
    "pause_runtime",
    "resume_runtime",
    "reset_agent_runtime",
    "shutdown_runtime",
]

RuntimeCommandStatus = Literal["queued", "claimed", "completed", "failed"]
RuntimeWorkerLifecycle = Literal["starting", "running", "stopping", "stopped", "error"]


class RuntimeCommand(BaseModel):
    """A durable app-to-runtime control command."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    command_type: RuntimeCommandType
    payload: str
    status: RuntimeCommandStatus = "queued"
    failure_reason: str | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    created_at: datetime


class RuntimeWorkerState(BaseModel):
    """Observed health and lifecycle state for the runtime worker."""

    model_config = ConfigDict(from_attributes=True)

    worker_name: str
    pid: int | None = None
    lifecycle_state: RuntimeWorkerLifecycle
    last_heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime
