"""BossMod AI — Agent and AgentState Pydantic models.

Defines the core Agent identity/configuration model, the runtime AgentState
for position and activity tracking, plus API input models for create/update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.agent_loop.communication_contract import (
    CommunicationContractError,
    load_communication_value,
)

# Hire-contract LABEL cap. Specialty renders in rosters, chips and menus, so
# it stays short; agent packs share it. Description and done bar are the
# agent's own prompt and are unbounded. Over a cap is rejected, never cut.
HIRE_ROLE_MAX_LEN = 120


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
    description: str | None = None
    done_fail_bar: str | None = None
    communication: dict[str, str] | None = None
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
    guardian_no_progress_threshold: int = 100

    # Home floor. One per agent. Lobby until an operator moves them.
    floor_id: str | None = None
    # Set while the agent is on vacation: off every floor (floor_id None),
    # never woken. core/floors.py keeps the two in step.
    vacation_since: datetime | None = None

    # System AI auto-approve for this agent's DM, and for its work with no
    # origin thread. Set only through PATCH /api/agents/{id}/cli-auto-approve.
    cli_auto_approve_dm: bool = False

    created_at: datetime

    @field_validator("communication", mode="before")
    @classmethod
    def _normalize_communication(cls, value: Any) -> dict[str, str] | None:
        return _coerce_communication(value)


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

def normalize_hire_text(value: str | None) -> str | None:
    """Strip optional hire-contract text and drop empty strings.

    Never shortens the text: prose fields are the agent's prompt and are kept
    whole. Labels that carry a cap go through ``check_hire_label``.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text


def check_hire_label(value: str | None, *, max_len: int, field_name: str) -> str | None:
    """Normalize a short hire-contract label and reject it when too long.

    Args:
        value: Raw label text, or None.
        max_len: Longest allowed length after stripping.
        field_name: Name used in the error message.

    Returns:
        The stripped label, or None when empty.

    Raises:
        ValueError: The stripped label exceeds ``max_len``. Pydantic reports
            this as a 422 instead of saving a silently shortened label.
    """
    text = normalize_hire_text(value)
    if text is not None and len(text) > max_len:
        raise ValueError(f"{field_name} must be {max_len} characters or fewer")
    return text


def _coerce_communication(value: Any) -> dict[str, str] | None:
    """Accept a mapping or JSON TEXT column; reject essays and unknown enums."""
    try:
        return load_communication_value(value)
    except CommunicationContractError as exc:
        raise ValueError(str(exc)) from exc


class AgentCreate(BaseModel):
    """Payload accepted by POST /api/agents to create a new agent."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    role: str | None = None
    description: str | None = None
    done_fail_bar: str | None = None
    communication: dict[str, str] | None = None
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
    floor_id: str | None = None

    @field_validator("role")
    @classmethod
    def _normalize_role(cls, value: str | None) -> str | None:
        return check_hire_label(value, max_len=HIRE_ROLE_MAX_LEN, field_name="role")

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        return normalize_hire_text(value)

    @field_validator("done_fail_bar")
    @classmethod
    def _normalize_done_fail_bar(cls, value: str | None) -> str | None:
        return normalize_hire_text(value)

    @field_validator("communication", mode="before")
    @classmethod
    def _normalize_communication(cls, value: Any) -> dict[str, str] | None:
        return _coerce_communication(value)


class AgentUpdate(BaseModel):
    """Partial update payload for PATCH /api/agents/{id}.
    All fields are optional — only supplied fields are written."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = None
    role: str | None = None
    description: str | None = None
    done_fail_bar: str | None = None
    communication: dict[str, str] | None = None
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

    @field_validator("role")
    @classmethod
    def _normalize_role(cls, value: str | None) -> str | None:
        return check_hire_label(value, max_len=HIRE_ROLE_MAX_LEN, field_name="role")

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        return normalize_hire_text(value)

    @field_validator("done_fail_bar")
    @classmethod
    def _normalize_done_fail_bar(cls, value: str | None) -> str | None:
        return normalize_hire_text(value)

    @field_validator("communication", mode="before")
    @classmethod
    def _normalize_communication(cls, value: Any) -> dict[str, str] | None:
        return _coerce_communication(value)
