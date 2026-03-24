"""BossMod AI — Prompt-history policy models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentPromptHistoryPolicy(BaseModel):
    """Per-agent prompt-history view settings.

    This is not a separate transcript store. It controls how the backend reads
    from existing authored messages and runtime notifications when building the
    model-visible history for a turn.
    """

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    last_n_histories: int = Field(default=30, ge=0, le=500)
    max_allowed_history_tokens: int = Field(default=2000, ge=0, le=50_000)
    earliest_ts_allowed: datetime | None = None
    include_notifications: bool = True
    created_at: datetime
    updated_at: datetime


class AgentPromptHistoryPolicyUpdate(BaseModel):
    """Partial update payload for per-agent prompt-history policy."""

    model_config = ConfigDict(from_attributes=True)

    last_n_histories: int | None = Field(default=None, ge=0, le=500)
    max_allowed_history_tokens: int | None = Field(default=None, ge=0, le=50_000)
    earliest_ts_allowed: datetime | None = None
    include_notifications: bool | None = None
