"""BossMod AI — Setting model.

Application configuration stored in the settings table.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Setting(BaseModel):
    """A system-wide configuration entry, categorized for the settings UI."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    category: str = "general"
    updated_at: datetime
