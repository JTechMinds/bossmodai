"""BossMod AI — Pydantic domain models.

Re-exports every model for convenient top-level imports::

    from core.models import Agent, AgentState, AIConnection, AIPersonality
"""

from __future__ import annotations

from core.models.agent import Agent, AgentCreate, AgentState, AgentUpdate
from core.models.memory import Setting
from core.models.message import Message, MessageCreate
from core.models.settings import (
    AIConnection,
    AIConnectionCreate,
    AIConnectionUpdate,
    AIPersonality,
    AIPersonalityCreate,
    AIPersonalityUpdate,
)
from core.models.task import Task, TaskCreate

__all__ = [
    # Agent
    "Agent",
    "AgentCreate",
    "AgentState",
    "AgentUpdate",
    # Task
    "Task",
    "TaskCreate",
    # Message
    "Message",
    "MessageCreate",
    # Settings
    "Setting",
    # AI Connections
    "AIConnection",
    "AIConnectionCreate",
    "AIConnectionUpdate",
    # AI Personalities
    "AIPersonality",
    "AIPersonalityCreate",
    "AIPersonalityUpdate",
]
