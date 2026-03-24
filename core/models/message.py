"""BossMod AI — Message Pydantic models.

Defines the authored message model used for human and agent communication,
plus the API input model for creating new messages.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Literal type for message classification
# ---------------------------------------------------------------------------

# Sentinel value for human operator in from_agent field
HUMAN_SENDER_ID = "__human__"


MessageType = Literal[
    "work",         # Task-related direct message
    "social",       # Casual / social conversation between agents
    "human",        # Message from the human operator
    "meeting",      # Team meeting message (broadcast)
]


# ---------------------------------------------------------------------------
# Message — a single communication event
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A message sent by (or to) an agent, recorded with the sender's
    tile position at the time of sending for spatial context."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    from_agent: str
    to_agent: str | None = None
    content: str
    message_type: MessageType = "work"
    location_x: int = 0
    location_y: int = 0
    token_count: int = 0
    created_at: datetime


# ---------------------------------------------------------------------------
# API input models
# ---------------------------------------------------------------------------

class MessageCreate(BaseModel):
    """Payload accepted by POST /api/messages to create a new message."""

    model_config = ConfigDict(from_attributes=True)

    from_agent: str
    to_agent: str | None = None
    content: str
    message_type: MessageType = "work"
