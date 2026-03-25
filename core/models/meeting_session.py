"""BossMod AI — Shared meeting session domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


MeetingSessionStatus = Literal["active", "ended"]
MeetingAuthorType = Literal["human", "agent", "system"]


class MeetingSession(BaseModel):
    """One shared in-progress or historical meeting session."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    title: str
    status: MeetingSessionStatus
    created_by_agent_id: str | None = None
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


class MeetingSessionMessage(BaseModel):
    """One ordered message inside a meeting session transcript."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    author_type: MeetingAuthorType
    author_agent_id: str | None = None
    author_name: str
    content: str
    source_channel: str
    created_at: datetime

