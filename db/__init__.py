"""BossMod AI — Database package.

Re-exports all public functions so consumers can use ``import db``
and call ``db.create_agent()``, ``db.get_task()``, etc.
"""

# Connection lifecycle
from db.connection import close_connection, get_connection, init_db, reset_database

# Reusable CRUD helpers (for custom queries in domain code)
from db.crud import execute, fetch_all, fetch_one, query, query_one

# Agents + state
from db.agents import (
    create_agent,
    delete_agent,
    get_agent,
    get_agent_state,
    get_agents_by_ids,
    list_agents,
    update_agent,
    update_agent_state,
)

# Messages
from db.messages import (
    create_message,
    delete_human_chat_thread,
    get_agent_direct_thread,
    get_formatted_messages,
    get_human_chat_thread,
    get_recent_authored_messages,
    get_recent_work_artifacts,
    get_recent_completed_tasks,
)
from db.agent_triggers import (
    claim_trigger,
    complete_agent_trigger,
    count_queued_triggers,
    create_agent_trigger,
    delete_queued_triggers,
    delete_open_triggers,
    fail_agent_trigger,
    get_agent_trigger,
    get_latest_trigger,
    has_open_trigger,
    has_open_trigger_matching,
    has_queued_trigger_matching,
    list_queued_triggers,
    list_agent_triggers,
    release_trigger,
    requeue_stale_triggers,
)

# Tasks
from db.tasks import create_task, get_task, list_tasks, update_task

# Settings
from db.settings import force_reseed, get_settings, set_setting

# Activity log
from db.activity import create_activity, get_recent_activity
from db.activities import (
    cancel_open_activities,
    create_activity as create_runtime_activity,
    get_active_activity,
    get_activity,
    get_resumable_work_activity,
    list_activities,
    update_activity,
)

# AI Connections
from db.ai_connections import (
    create_connection,
    delete_connection,
    get_connection_by_id,
    list_connections,
    update_connection,
)

# AI Personalities
from db.ai_personalities import (
    create_personality,
    delete_personality,
    get_personality,
    list_personalities,
    update_personality,
)

# Diagnostics
from db.diagnostics import create_diagnostic, get_diagnostic, get_diagnostic_steps, get_diagnostics

# World state + spatial
from db.world import get_nearby_agents, get_world_state

__all__ = [
    # Connection
    "close_connection",
    "get_connection",
    "init_db",
    "reset_database",
    # CRUD helpers
    "execute",
    "fetch_all",
    "fetch_one",
    "query",
    "query_one",
    # Agents
    "create_agent",
    "delete_agent",
    "get_agent",
    "get_agent_state",
    "get_agents_by_ids",
    "list_agents",
    "update_agent",
    "update_agent_state",
    # Messages
    "create_message",
    "delete_human_chat_thread",
    "get_agent_direct_thread",
    "get_formatted_messages",
    "get_human_chat_thread",
    "get_recent_authored_messages",
    "get_recent_work_artifacts",
    "get_recent_completed_tasks",
    # Trigger queue
    "claim_trigger",
    "complete_agent_trigger",
    "count_queued_triggers",
    "create_agent_trigger",
    "delete_queued_triggers",
    "delete_open_triggers",
    "fail_agent_trigger",
    "get_agent_trigger",
    "get_latest_trigger",
    "has_open_trigger",
    "has_open_trigger_matching",
    "has_queued_trigger_matching",
    "list_queued_triggers",
    "list_agent_triggers",
    "release_trigger",
    "requeue_stale_triggers",
    # Tasks
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    # Settings
    "force_reseed",
    "get_settings",
    "set_setting",
    # Activity
    "create_activity",
    "create_runtime_activity",
    "cancel_open_activities",
    "get_active_activity",
    "get_activity",
    "get_resumable_work_activity",
    "list_activities",
    "update_activity",
    "get_recent_activity",
    # AI Connections
    "create_connection",
    "delete_connection",
    "get_connection_by_id",
    "list_connections",
    "update_connection",
    # AI Personalities
    "create_personality",
    "delete_personality",
    "get_personality",
    "list_personalities",
    "update_personality",
    # Diagnostics
    "create_diagnostic",
    "get_diagnostic",
    "get_diagnostic_steps",
    "get_diagnostics",
    # World
    "get_nearby_agents",
    "get_world_state",
]
