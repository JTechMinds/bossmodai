"""BossMod AI — Agent and AgentState Pydantic models.

Defines the core Agent identity/configuration model, the runtime AgentState
for position and activity tracking, plus API input models for create/update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Agent — persistent identity & configuration
# ---------------------------------------------------------------------------

class Agent(BaseModel):
    """An AI agent with its identity, prompt configuration, model overrides,
    desk assignment, and guardian safety thresholds."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    storage_key: str
    name: str
    role: str | None = None
    prompt_template: str | None = None
    color: str = "#3b82f6"

    # Per-agent model overrides (None = use global default)
    model_social: str | None = None
    model_work: str | None = None
    model_reasoning: str | None = None
    model_extraction: str | None = None
    model_self_queue: str | None = None

    # Optional custom provider endpoint
    api_base_url: str | None = None
    api_key: str | None = Field(default=None, exclude=True)
    extra_body: str | None = None

    # Desk assignment (tile coordinates on the office map)
    desk_x: int | None = None
    desk_y: int | None = None

    # Guardian safety thresholds
    guardian_token_limit: int = 30_000
    guardian_velocity_limit: int = 10
    guardian_repetition_threshold: float = 0.85
    guardian_no_progress_threshold: int = 30

    created_at: datetime


# ---------------------------------------------------------------------------
# AgentState — runtime position & activity
# ---------------------------------------------------------------------------

AgentStatus = Literal["idle", "waiting", "blocked", "work_active", "social_active", "in_transit"]


class AgentState(BaseModel):
    """Tracks an agent's current position on the tilemap and activity status.
    Updated every simulation tick; never persisted historically."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    x: int = 0
    y: int = 0
    status: AgentStatus = "idle"
    last_active_at: datetime | None = None
    idle_since: datetime | None = None


# ---------------------------------------------------------------------------
# API input models
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    """Payload accepted by POST /api/agents to create a new agent."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    role: str | None = None
    prompt_template: str | None = None
    color: str = "#3b82f6"
    desk_x: int | None = None
    desk_y: int | None = None

    model_social: str | None = None
    model_work: str | None = None
    model_reasoning: str | None = None
    model_extraction: str | None = None
    model_self_queue: str | None = None

    api_base_url: str | None = None
    api_key: str | None = None
    extra_body: str | None = None
    connection_id: str | None = None


class AgentUpdate(BaseModel):
    """Partial update payload for PATCH /api/agents/{id}.
    All fields are optional — only supplied fields are written."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = None
    role: str | None = None
    prompt_template: str | None = None
    color: str | None = None

    model_social: str | None = None
    model_work: str | None = None
    model_reasoning: str | None = None
    model_extraction: str | None = None
    model_self_queue: str | None = None

    api_base_url: str | None = None
    api_key: str | None = None
    extra_body: str | None = None
    connection_id: str | None = None

    desk_x: int | None = None
    desk_y: int | None = None

    guardian_token_limit: int | None = None
    guardian_velocity_limit: int | None = None
    guardian_repetition_threshold: float | None = None
    guardian_no_progress_threshold: int | None = None
