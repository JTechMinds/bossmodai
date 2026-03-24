"""BossMod AI — Artifact registry models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


ArtifactKind = Literal["file"]
ArtifactCategory = Literal["output", "note", "project"]


class Artifact(BaseModel):
    """A tangible reviewable file produced inside the bounded BossMod workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    task_id: str | None = None
    virtual_path: str
    absolute_path: str
    title: str
    kind: ArtifactKind = "file"
    category: ArtifactCategory = "output"
    size_bytes: int = 0
    source_command: str | None = None
    created_at: datetime
    updated_at: datetime
