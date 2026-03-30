"""BossMod AI — Notification domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


NotificationKind = Literal["receipt", "completion", "blocked", "handoff", "abandoned", "task_update"]
NotificationSourceChannel = Literal["chat", "channel", "api", "slack", "telegram", "peer", "task", "work", "meeting", "system"]
TaskNotificationPolicy = Literal["none", "completion_blocked", "all"]


class Notification(BaseModel):
    """A runtime-owned human-facing notification."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    task_id: str | None = None
    activity_id: str | None = None
    kind: NotificationKind
    content: str
    source_channel: NotificationSourceChannel
    policy: TaskNotificationPolicy
    chat_visible: bool = True
    prompt_visibility: bool = False
    created_at: datetime


class NotificationLink(BaseModel):
    """A structured UI/action target attached to one runtime notification."""

    model_config = ConfigDict(from_attributes=True)

    notification_id: str
    target_kind: Literal["desk"] = "desk"
    target_path: str
    label: str = "Open in Desk"
    created_at: datetime


class TaskNotificationSettings(BaseModel):
    """Durable notification policy bound to one task."""

    model_config = ConfigDict(from_attributes=True)

    task_id: str
    source_channel: NotificationSourceChannel
    policy: TaskNotificationPolicy
    created_at: datetime
    updated_at: datetime
