"""BossMod AI — Database package.

Re-exports all public functions so consumers can use ``import db``
and call ``db.create_agent()``, ``db.get_task()``, etc.
"""

# Connection lifecycle
from db.connection import close_connection, get_connection, init_db

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
from db.messages import create_message, get_messages_for_agent, get_unread_messages

# Tasks
from db.tasks import create_task, get_task, list_tasks, update_task

# Settings
from db.settings import get_settings, set_setting

# Activity log
from db.activity import create_activity, get_recent_activity

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

# World state + spatial
from db.world import get_nearby_agents, get_world_state

__all__ = [
    # Connection
    "close_connection",
    "get_connection",
    "init_db",
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
    "get_messages_for_agent",
    "get_unread_messages",
    # Tasks
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    # Settings
    "get_settings",
    "set_setting",
    # Activity
    "create_activity",
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
    # World
    "get_nearby_agents",
    "get_world_state",
]
