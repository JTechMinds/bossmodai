#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

uv run pytest -q \
  tests/test_agent_runtime.py::test_run_turn_human_chat_chat_lane_uses_standard_decision_turn \
  tests/test_agent_runtime.py::test_run_turn_human_chat_work_request_creates_task_and_accepts_assignment \
  tests/test_agent_runtime.py::test_run_turn_status_reply_schedules_activity_resume_for_active_work \
  tests/test_agent_runtime.py::test_execute_bm_cli_exposes_expanded_read_commands \
  tests/test_agent_runtime.py::test_work_completion_requires_requested_saved_file \
  tests/test_agent_runtime.py::test_activity_resumed_managed_writer_saves_long_file_and_commits_once \
  tests/test_agent_runtime.py::test_bm_cli_write_registers_artifact_and_desk_view_can_open_it \
  tests/test_agent_runtime.py::test_complete_action_can_reply_to_human_requester_when_follow_up_message_is_provided \
  tests/test_agent_runtime.py::test_run_turn_peer_message_grounded_question_uses_shared_communication_lane \
  tests/test_agent_runtime.py::test_message_action_routes_to_agent_by_explicit_id \
  tests/test_agent_runtime.py::test_run_turn_end_to_end_manager_delegation_chain_reports_back_to_human
