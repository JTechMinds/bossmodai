"""BossMod AI — Shared channel domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


ChannelKind = Literal["manual"]
ChannelStatus = Literal["active", "archived"]
ChannelAuthorType = Literal["human", "agent", "system"]


class Channel(BaseModel):
    """One reusable shared communication channel."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: ChannelKind
    status: ChannelStatus
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ChannelMember(BaseModel):
    """One agent membership inside a shared channel."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    agent_id: str
    created_at: datetime


class ChannelMessage(BaseModel):
    """One ordered message inside a shared channel transcript."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    author_type: ChannelAuthorType
    author_agent_id: str | None = None
    author_name: str
    content: str
    source_channel: str
    notification_kind: str | None = None
    consent_id: str | None = None
    created_at: datetime
