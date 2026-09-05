"""BossMod AI — Pydantic domain models.

Re-exports every model for convenient top-level imports::

    from core.models import Agent, AgentState, AIConnection, AIPersonality
"""

from __future__ import annotations

from core.models.cli import AgentCliState
from core.models.cli_policy import CliApprovalRequest, CliPolicyRule, CliPolicyRuleCreate
from core.models.activity import Activity
from core.models.agent import Agent, AgentCreate, AgentState, AgentUpdate
from core.models.artifact import Artifact
from core.models.channel import Channel, ChannelMember, ChannelMessage
from core.models.channel_response import ChannelResponseCandidate, ChannelResponseRound
from core.models.meeting_response import MeetingResponseCandidate, MeetingResponseRound
from core.models.meeting_session import MeetingSession, MeetingSessionMessage
from core.models.memory import Setting
from core.models.message import Message, MessageCreate
from core.models.notification import Notification, NotificationLink, TaskNotificationSettings
from core.models.prompt_history import AgentPromptHistoryPolicy, AgentPromptHistoryPolicyUpdate
from core.models.runtime import RuntimeCommand, RuntimeWorkerState
from core.models.settings import (
    AIConnection,
    AIConnectionCreate,
    AIConnectionUpdate,
    AIPersonality,
    AIPersonalityCreate,
    AIPersonalityUpdate,
)
from core.models.task import Task, TaskCreate, TaskCreateOutcome, TaskCreateResponse
from core.models.task_event import TaskEvent
from core.models.trigger import AgentTrigger
from core.models.work_contract import DeliverableSpec, TaskWorkContract, WorkContract

__all__ = [
    # Agent
    "Agent",
    "AgentCreate",
    "AgentState",
    "AgentUpdate",
    "AgentCliState",
    "CliApprovalRequest",
    "CliPolicyRule",
    "CliPolicyRuleCreate",
    "Activity",
    "Artifact",
    "Channel",
    "ChannelMember",
    "ChannelMessage",
    "ChannelResponseCandidate",
    "ChannelResponseRound",
    "MeetingResponseCandidate",
    "MeetingResponseRound",
    "MeetingSession",
    "MeetingSessionMessage",
    # Task
    "Task",
    "TaskCreate",
    "TaskCreateOutcome",
    "TaskCreateResponse",
    "TaskEvent",
    "AgentTrigger",
    "DeliverableSpec",
    "TaskWorkContract",
    "WorkContract",
    # Message
    "Message",
    "MessageCreate",
    "Notification",
    "NotificationLink",
    "TaskNotificationSettings",
    "AgentPromptHistoryPolicy",
    "AgentPromptHistoryPolicyUpdate",
    "RuntimeCommand",
    "RuntimeWorkerState",
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
