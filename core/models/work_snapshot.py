"""Frozen working transcript of one execution-turn work activity."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkInterlude(BaseModel):
    """One interrupt handled while the work was frozen."""

    model_config = ConfigDict(extra="forbid")

    from_name: str
    content: str
    reply: str


class WorkSnapshot(BaseModel):
    """The agent's own steps on a work activity, persisted across pauses.

    ``transcript`` is the working part of an execution turn's message list
    (``{role, content}`` pairs after the preamble). ``fingerprints`` is every
    command fingerprint this activity has already run, used to tell a first
    read from a repeat. ``interludes`` are chat turns that happened while the
    work was paused; they are rendered once on the next resume and cleared.
    """

    model_config = ConfigDict(from_attributes=True)

    activity_id: str
    agent_id: str
    task_id: str | None = None
    transcript: list[dict[str, str]] = Field(default_factory=list)
    fingerprints: list[str] = Field(default_factory=list)
    interludes: list[WorkInterlude] = Field(default_factory=list)
    no_progress_checkpoints: int = 0
    created_at: datetime
    updated_at: datetime

    @field_validator("transcript", "fingerprints", "interludes", mode="before")
    @classmethod
    def _decode_json_column(cls, value: Any) -> Any:
        """Stored columns are JSON text; decode them before validation."""
        if isinstance(value, str):
            return json.loads(value)
        return value
