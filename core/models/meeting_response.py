"""BossMod AI — Shared meeting response-round domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


MeetingResponseRoundStatus = Literal["active", "completed"]
MeetingResponseCandidateStatus = Literal["pending", "queued", "responding", "responded", "observed"]


class MeetingResponseRound(BaseModel):
    """One serialized response queue for a shared meeting message."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    source_message_id: str
    status: MeetingResponseRoundStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class MeetingResponseCandidate(BaseModel):
    """One agent's participation status inside a response round."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    round_id: str
    agent_id: str
    status: MeetingResponseCandidateStatus
    queue_position: int | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
