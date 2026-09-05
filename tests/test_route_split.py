"""HA-STRUCT-P1-01 — split api/routes.py keeps the public route table."""

from __future__ import annotations

from api.routes import router


# Captured from api/routes.py on main @ 25f0732 before the package split.
EXPECTED_ROUTES = {
    ((), "/api/ws", "websocket_endpoint"),
    (("GET",), "/api/map", "get_map"),
    (("GET",), "/api/world", "get_world_state"),
    (("GET",), "/api/agents", "list_agents"),
    (("GET",), "/api/agents/{agent_id}", "get_agent"),
    (("GET",), "/api/company/agents", "list_company_agents"),
    (("GET",), "/api/metrics/dashboard", "get_metrics_dashboard"),
    (("GET",), "/api/company/files", "get_company_files"),
    (("PUT",), "/api/company/files", "save_company_file"),
    (("POST",), "/api/company/files/open-folder", "open_company_folder"),
    (("POST",), "/api/company/files/create", "create_company_file"),
    (("DELETE",), "/api/company/files", "delete_company_file"),
    (("PATCH",), "/api/company/files/rename", "rename_company_file"),
    (("POST",), "/api/company/files/move", "move_company_file"),
    (("POST",), "/api/company/files/copy", "copy_company_file"),
    (("GET",), "/api/company/files/search", "search_company_files"),
    (("GET",), "/api/company/files/raw", "get_company_file_raw"),
    (("GET",), "/api/channels", "list_channels"),
    (("POST",), "/api/channels", "create_channel"),
    (("GET",), "/api/channels/{channel_id}", "get_channel"),
    (("POST",), "/api/channels/{channel_id}/messages", "create_channel_message"),
    (("GET",), "/api/agents/{agent_id}/api-key", "get_agent_api_key"),
    (("GET",), "/api/agents/{agent_id}/prompt-history-policy", "get_agent_prompt_history_policy"),
    (("GET",), "/api/agents/{agent_id}/desk", "get_agent_desk"),
    (("POST",), "/api/agents/{agent_id}/desk/open-folder", "open_agent_desk_folder"),
    (("POST",), "/api/agents", "create_agent"),
    (("PATCH",), "/api/agents/{agent_id}", "update_agent"),
    (("PATCH",), "/api/agents/{agent_id}/prompt-history-policy", "update_agent_prompt_history_policy"),
    (("DELETE",), "/api/agents", "delete_all_agents"),
    (("DELETE",), "/api/agents/{agent_id}", "delete_agent"),
    (("GET",), "/api/agents/{agent_id}/messages", "get_agent_messages"),
    (("GET",), "/api/agents/{agent_id}/notifications", "get_agent_notifications"),
    (("GET",), "/api/tasks", "list_tasks"),
    (("POST",), "/api/tasks", "create_task"),
    (("GET",), "/api/tasks/board", "get_task_board"),
    (("GET",), "/api/tasks/{task_id}/events", "get_task_events"),
    (("GET",), "/api/tasks/{task_id}", "get_task"),
    (("GET",), "/api/activity/feed", "get_activity_feed"),
    (("POST",), "/api/agents/{agent_id}/activate", "activate_agent"),
    (("GET",), "/api/agents/{agent_id}/meeting-session", "get_agent_meeting_session"),
    (("POST",), "/api/agents/{agent_id}/meeting-session/messages", "create_agent_meeting_session_message"),
    (("DELETE",), "/api/agents/{agent_id}/chat-history", "clear_agent_chat_history"),
    (("POST",), "/api/agents/{agent_id}/reset-runtime", "reset_agent_runtime"),
    (("GET",), "/api/diagnostics", "list_diagnostics"),
    (("GET",), "/api/diagnostics/{diagnostic_id}", "get_diagnostic_detail"),
    (("GET",), "/api/cli-policy/virtual-commands", "list_virtual_commands"),
    (("GET",), "/api/cli-policy/rules", "list_cli_policy_rules"),
    (("POST",), "/api/cli-policy/rules", "create_cli_policy_rule"),
    (("PUT",), "/api/cli-policy/rules/{rule_id}", "update_cli_policy_rule"),
    (("DELETE",), "/api/cli-policy/rules/{rule_id}", "delete_cli_policy_rule"),
    (("POST",), "/api/cli-policy/rules/seed-defaults", "seed_cli_policy_rules"),
    (("GET",), "/api/cli-policy/approvals", "list_cli_approval_requests"),
    (("POST",), "/api/cli-policy/approvals/{request_id}/approve", "approve_cli_request"),
    (("POST",), "/api/cli-policy/approvals/{request_id}/reject", "reject_cli_request"),
    (("POST",), "/api/cli-policy/simulate", "simulate_cli_policy"),
    (("POST",), "/api/cli-policy/simulator/execute", "simulator_execute"),
    (("GET",), "/api/settings", "get_settings"),
    (("GET",), "/api/settings/desktop-open-folder-options", "get_desktop_open_folder_options"),
    (("GET",), "/api/runtime/contracts", "get_runtime_contracts"),
    (("GET",), "/api/runtime/state", "get_runtime_state"),
    (("PUT",), "/api/runtime/state", "set_runtime_state"),
    (("PUT",), "/api/runtime/contracts", "set_runtime_contracts"),
    (("POST",), "/api/runtime/contracts/reset", "reset_runtime_contracts"),
    (("POST",), "/api/runtime/contracts/preview", "preview_runtime_contract"),
    (("POST",), "/api/settings/reseed", "reseed_settings"),
    (("POST",), "/api/settings/reseed-application", "reseed_application"),
    (("POST",), "/api/settings/{key}/reset", "reset_setting_to_default"),
    (("PUT",), "/api/settings/{key}", "set_setting"),
    (("GET",), "/api/connections", "list_connections"),
    (("GET",), "/api/connections/{connection_id}", "get_connection"),
    (("POST",), "/api/connections", "create_connection"),
    (("PATCH",), "/api/connections/{connection_id}", "update_connection"),
    (("DELETE",), "/api/connections/{connection_id}", "delete_connection"),
    (("POST",), "/api/connections/test", "test_connection"),
    (("GET",), "/api/personalities", "list_personalities"),
    (("GET",), "/api/personalities/{personality_id}", "get_personality"),
    (("POST",), "/api/personalities", "create_personality"),
    (("PATCH",), "/api/personalities/{personality_id}", "update_personality"),
    (("DELETE",), "/api/personalities/{personality_id}", "delete_personality"),
}


def _route_table():
    rows = set()
    for route in router.routes:
        methods = tuple(sorted(getattr(route, "methods", None) or []))
        rows.add((methods, getattr(route, "path", None), getattr(route, "name", None)))
    return rows


def test_public_route_table_unchanged() -> None:
    got = _route_table()
    assert got == EXPECTED_ROUTES
    assert len(got) == 79


def test_from_api_routes_import_router_still_works() -> None:
    from api.routes import router as exported

    assert exported is router
    assert router.prefix == "/api"
