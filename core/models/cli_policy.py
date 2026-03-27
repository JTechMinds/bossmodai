"""BossMod AI — CLI policy rule and approval request models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CliPolicyRule(BaseModel):
    """One configurable CLI policy rule."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    enabled: bool = True
    priority: int = 0
    created_at: datetime
    updated_at: datetime


class CliPolicyRuleCreate(BaseModel):
    """Input payload for creating a CLI policy rule."""

    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    enabled: bool = True
    priority: int = 0


class CliApprovalRequest(BaseModel):
    """A pending, approved, or rejected CLI approval request."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    trigger_id: str | None = None
    command: str
    content: str | None = None
    cwd: str | None = None
    matched_rule_id: str | None = None
    status: str = "pending"
    decision_by: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
