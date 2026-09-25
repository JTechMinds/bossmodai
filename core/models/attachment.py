"""BossMod AI — Attachment Pydantic model.

Metadata row for a file attached to a message. File bytes live on the
local filesystem at storage_path; only metadata is stored in the DB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

PreviewTier = Literal["image", "text", "document", "other"]


class Attachment(BaseModel):
    """A single file attachment linked to a message."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    file_name: str
    file_size: int
    mime_type: str
    storage_path: str
    preview_tier: PreviewTier
    created_at: datetime
