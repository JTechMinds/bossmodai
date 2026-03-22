"""BossMod AI — Settings-related Pydantic models.

AI Connections, AI Personalities, and API input models.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
    created_at: datetime


class AIConnectionCreate(BaseModel):
    """Payload for creating a new AI connection."""

    name: str
    api_base_url: str
    api_key: str | None = None
    model: str | None = None
    extra_body: str | None = None


class AIConnectionUpdate(BaseModel):
    """Partial update for an AI connection."""

    name: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    extra_body: str | None = None


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
