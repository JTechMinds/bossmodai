"""BossMod AI — Database package.

Re-exports all public functions so consumers can use ``import db``
and call ``db.create_agent()``, ``db.get_task()``, etc.
"""

# Connection lifecycle
from db.connection import close_connection, get_connection, init_db, reset_database, transaction

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
from db.agent_storage import normalize_agent_personal_storage_roots
from db.agent_cli import ensure_agent_cli_state, get_agent_cli_state, update_agent_cli_state
from db.agent_prompt_history_policies import (
    create_agent_prompt_history_policy,
    ensure_agent_prompt_history_policy,
    get_agent_prompt_history_policy,
    update_agent_prompt_history_policy,
)
from db.artifacts import (
    build_artifact_title,
    get_artifact,
    get_artifact_by_absolute_path,
    list_artifacts,
    upsert_artifact,
)
from db.bm_cli_events import create_bm_cli_event, has_bm_cli_write_for_path, list_bm_cli_events
from db.notifications import create_notification, delete_agent_notifications, list_notifications
from db.notification_links import create_notification_link, list_notification_links
from db.channels import (
    add_channel_members,
    create_channel,
    create_channel_message,
    get_channel,
    get_formatted_channel_messages,
    get_latest_channel_message,
    list_channel_member_details,
    list_channel_members,
    list_channel_messages,
    list_channels,
    update_channel,
)
from db.channel_response_rounds import (
    activate_next_channel_response_candidate,
    create_channel_response_candidate,
    create_channel_response_round,
    get_active_responding_channel_candidate,
    get_channel_response_candidate,
    get_channel_response_round,
    list_channel_response_candidates,
    mark_channel_candidate_observed,
    mark_channel_candidate_responded,
    maybe_complete_channel_response_round,
    reserve_channel_response_slot,
    update_channel_response_candidate,
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
from db.meeting_sessions import (
    create_meeting_session,
    create_meeting_session_message,
    end_meeting_session,
    ensure_room_meeting_session,
    get_active_meeting_session_by_room,
    get_active_meeting_session_for_agent,
    get_formatted_meeting_session_messages,
    get_meeting_session,
    list_active_meeting_participants,
    list_meeting_session_messages,
    update_meeting_session,
)
from db.meeting_response_rounds import (
    activate_next_response_candidate,
    create_meeting_response_candidate,
    create_meeting_response_round,
    delete_meeting_response_rounds,
    get_active_responding_candidate,
    get_meeting_response_candidate,
    get_meeting_response_round,
    list_meeting_response_candidates,
    mark_candidate_observed,
    mark_candidate_responded,
    maybe_complete_meeting_response_round,
    reserve_response_slot,
    update_meeting_response_candidate,
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
from db.task_notification_policies import get_task_notification_settings, set_task_notification_settings
from db.task_notification_targets import (
    delete_task_notification_target,
    get_task_notification_target_channel_id,
    set_task_notification_target_channel_id,
)

# Settings
from db.settings import force_reseed, get_settings, set_setting

# Activity log
from db.activity_log import create_activity_log_entry, get_recent_activity_log_entries
from db.unified_feed import (
    classify_category,
    get_unified_feed,
    normalize_activity_entry,
    normalize_activity_log_entry,
    normalize_notification_entry,
)
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
    force_reseed_personalities,
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
    "normalize_agent_personal_storage_roots",
    "ensure_agent_cli_state",
    "build_artifact_title",
    "create_agent_prompt_history_policy",
    "ensure_agent_prompt_history_policy",
    "get_artifact",
    "get_artifact_by_absolute_path",
    "get_agent_cli_state",
    "get_agent_prompt_history_policy",
    "list_artifacts",
    "upsert_artifact",
    "update_agent_cli_state",
    "update_agent_prompt_history_policy",
    "create_bm_cli_event",
    "has_bm_cli_write_for_path",
    "list_bm_cli_events",
    "create_notification",
    "create_notification_link",
    "delete_agent_notifications",
    "create_channel",
    "create_channel_message",
    "add_channel_members",
    "get_channel",
    "get_formatted_channel_messages",
    "get_latest_channel_message",
    "list_channel_member_details",
    "list_channel_members",
    "list_channel_messages",
    "list_channels",
    "update_channel",
    "create_channel_response_candidate",
    "create_channel_response_round",
    "activate_next_channel_response_candidate",
    "get_active_responding_channel_candidate",
    "get_channel_response_candidate",
    "get_channel_response_round",
    "list_channel_response_candidates",
    "mark_channel_candidate_observed",
    "mark_channel_candidate_responded",
    "maybe_complete_channel_response_round",
    "reserve_channel_response_slot",
    "update_channel_response_candidate",
    "list_notification_links",
    "list_notifications",
    # Messages
    "create_message",
    "delete_human_chat_thread",
    "get_agent_direct_thread",
    "get_formatted_messages",
    "get_human_chat_thread",
    "get_recent_authored_messages",
    "get_recent_work_artifacts",
    "get_recent_completed_tasks",
    "create_meeting_session",
    "create_meeting_session_message",
    "create_meeting_response_candidate",
    "create_meeting_response_round",
    "delete_meeting_response_rounds",
    "end_meeting_session",
    "get_active_responding_candidate",
    "ensure_room_meeting_session",
    "get_active_meeting_session_by_room",
    "get_active_meeting_session_for_agent",
    "get_meeting_response_candidate",
    "get_meeting_response_round",
    "get_formatted_meeting_session_messages",
    "get_meeting_session",
    "list_active_meeting_participants",
    "list_meeting_response_candidates",
    "list_meeting_session_messages",
    "mark_candidate_observed",
    "mark_candidate_responded",
    "maybe_complete_meeting_response_round",
    "reserve_response_slot",
    "update_meeting_session",
    "update_meeting_response_candidate",
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
    "get_task_notification_settings",
    "set_task_notification_settings",
    "delete_task_notification_target",
    "get_task_notification_target_channel_id",
    "set_task_notification_target_channel_id",
    # Settings
    "force_reseed",
    "get_settings",
    "set_setting",
    # Activity
    "create_activity_log_entry",
    "create_runtime_activity",
    "cancel_open_activities",
    "get_active_activity",
    "get_activity",
    "get_resumable_work_activity",
    "list_activities",
    "update_activity",
    "get_recent_activity_log_entries",
    "classify_category",
    "get_unified_feed",
    "normalize_activity_entry",
    "normalize_activity_log_entry",
    "normalize_notification_entry",
    # AI Connections
    "create_connection",
    "delete_connection",
    "get_connection_by_id",
    "list_connections",
    "update_connection",
    # AI Personalities
    "create_personality",
    "delete_personality",
    "force_reseed_personalities",
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
