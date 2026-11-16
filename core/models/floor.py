"""BossMod AI — Floor domain model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Floor(BaseModel):
    """One labeled co-mingle domain. Not a people filter.

    ``suspended_at`` is None while the floor is active. When set, the floor
    is out of service (``core/floor_suspend.py``): its agents are never
    woken, and it is listed apart from the active floors until resumed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime | None = None
    suspended_at: datetime | None = None
