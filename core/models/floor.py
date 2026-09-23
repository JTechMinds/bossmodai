"""BossMod AI — Floor domain model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Floor(BaseModel):
    """One labeled co-mingle domain. Not a people filter."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime | None = None
