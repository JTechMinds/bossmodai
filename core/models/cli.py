"""BossMod AI — BossMod CLI session models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentCliState(BaseModel):
    """Persistent virtual CLI session state for one agent."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    cwd: str = "/me"
    updated_at: datetime
