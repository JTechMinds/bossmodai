"""BossMod AI — Prompt-history policy models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentPromptHistoryPolicy(BaseModel):
    """Per-agent prompt-history view settings.

    This is not a separate transcript store. It controls how the backend reads
    from existing authored messages and runtime notifications when building the
    model-visible history for a turn. How much history a turn sees is not
    per agent: it is the per-thread history budget in Settings → Context
    Window (``conversation_history_budget_tokens``).
    """

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    earliest_ts_allowed: datetime | None = None
    include_notifications: bool = True
    created_at: datetime
    updated_at: datetime


class AgentPromptHistoryPolicyUpdate(BaseModel):
    """Partial update payload for per-agent prompt-history policy."""

    model_config = ConfigDict(from_attributes=True)

    earliest_ts_allowed: datetime | None = None
    include_notifications: bool | None = None
