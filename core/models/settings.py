"""BossMod AI — Settings-related Pydantic models.

AI Connections, AI Personalities, and API input models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from core.models.thinking import ThinkingLevels, parse_thinking_levels


# ---------------------------------------------------------------------------
# AI Connections
# ---------------------------------------------------------------------------

class AIConnection(BaseModel):
    """A saved LLM provider configuration."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    api_base_url: str
    api_key: str | None = None
    model: str | None = None
    extra_body: str | None = None
    # Level → JSON fragment deep-merged over extra_body for agents that pick
    # that level (core.llm.thinking). None = the connection offers no levels.
    thinking_levels: ThinkingLevels | None = None
    created_at: datetime

    @field_validator("thinking_levels", mode="before")
    @classmethod
    def _parse_thinking_levels(cls, value: Any) -> ThinkingLevels | None:
        return parse_thinking_levels(value)


class AIConnectionCreate(BaseModel):
    """Payload for creating a new AI connection.

    ``supports_images`` is not a connection column: it is stored per model
    name in ``model_capabilities`` and applies to every agent using ``model``.
    None (not sent) leaves any existing flag for that model untouched.
    """

    name: str
    api_base_url: str
    api_key: str | None = None
    model: str | None = None
    extra_body: str | None = None
    thinking_levels: ThinkingLevels | None = None
    supports_images: bool | None = None

    @field_validator("thinking_levels", mode="before")
    @classmethod
    def _parse_thinking_levels(cls, value: Any) -> ThinkingLevels | None:
        return parse_thinking_levels(value)


class AIConnectionUpdate(BaseModel):
    """Partial update for an AI connection.

    ``supports_images`` is written to ``model_capabilities`` for the
    effective model (the patched one, else the stored one).

    ``thinking_levels``: None (not sent) leaves the map alone; ``{}`` clears
    it, so it is kept as ``{}`` rather than read as "not sent".
    """

    name: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    extra_body: str | None = None
    thinking_levels: ThinkingLevels | None = None
    supports_images: bool | None = None

    @field_validator("thinking_levels", mode="before")
    @classmethod
    def _parse_thinking_levels(cls, value: Any) -> ThinkingLevels | None:
        levels = parse_thinking_levels(value)
        if levels is None and value is not None:
            return {}
        return levels


# ---------------------------------------------------------------------------
# AI Personalities
# ---------------------------------------------------------------------------

class AIPersonality(BaseModel):
    """A reusable prompt template for agent roles."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prompt_template: str
    created_at: datetime


class AIPersonalityCreate(BaseModel):
    """Payload for creating a new AI personality."""

    name: str
    prompt_template: str


class AIPersonalityUpdate(BaseModel):
    """Partial update for an AI personality."""

    name: str | None = None
    prompt_template: str | None = None
