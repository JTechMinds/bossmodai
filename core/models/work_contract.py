"""BossMod AI — Structured work contract models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DeliverableType = Literal["file"]


class DeliverableSpec(BaseModel):
    """A tangible output the agent must materialize before sign-off."""

    model_config = ConfigDict(extra="forbid")

    type: DeliverableType
    path: str
    description: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "DeliverableSpec":
        self.path = self.path.strip()
        if not self.path:
            raise ValueError('deliverable "path" must be non-empty')
        return self


class WorkContract(BaseModel):
    """Structured, runtime-validatable requirements for a work activity."""

    model_config = ConfigDict(extra="forbid")

    deliverables: list[DeliverableSpec] = Field(default_factory=list)


class TaskWorkContract(BaseModel):
    """Durable work-contract record bound to a task."""

    model_config = ConfigDict(from_attributes=True)

    task_id: str
    work_contract: WorkContract
    created_at: datetime
    updated_at: datetime
