"""BossMod AI — Shared channel response-round domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


ChannelResponseRoundStatus = Literal["active", "completed"]
ChannelResponseCandidateStatus = Literal["pending", "queued", "responding", "responded", "observed"]


class ChannelResponseRound(BaseModel):
    """One serialized reply queue for a shared channel message."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    source_message_id: str
    status: ChannelResponseRoundStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ChannelResponseCandidate(BaseModel):
    """One agent's participation state inside a shared channel reply round."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    round_id: str
    agent_id: str
    status: ChannelResponseCandidateStatus
    queue_position: int | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
