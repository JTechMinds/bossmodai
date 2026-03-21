"""BossMod AI — Pydantic domain models.

Re-exports every model for convenient top-level imports::

    from core.models import Agent, AgentState, Task, Message, Setting
"""

from __future__ import annotations

from core.models.agent import Agent, AgentCreate, AgentState, AgentUpdate
from core.models.memory import Setting
from core.models.message import Message, MessageCreate
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
]
