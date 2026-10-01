"""BossMod CLI execution handler.

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.cli_turn_result import map_cli_result
from core.agent_loop.task_origins import consent_origin_channel_id
from core.agent_loop.work_binding import bound_task_id
from core.bm_cli import execute_bm_cli
from core.loop_breathing import run_shell_off_request_loop
from core.bm_cli.host_path_consent import request_host_path_access
from core.bm_cli.session import get_cli_cwd
from core.models import Agent, AgentState


async def _handle_bm_cli(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded BossMod CLI query and return a turn-local result."""
    command = str(action.get("command") or "").strip()
    content = action.get("content")
    channel_id = consent_origin_channel_id(trigger, task_id=bound_task_id(agent.id))
    cli_result = await run_shell_off_request_loop(
        execute_bm_cli,
        agent,
        state,
        command,
        content if isinstance(content, str) else None,
        trigger_type=(trigger or {}).get("type") if isinstance(trigger, dict) else None,
        channel_id=channel_id,
    )
    result = map_cli_result(agent, cli_result, command=command, trigger=trigger)
    # The channel this command ran for, so the turn posts its status lines to
    # the same place (see turn_helpers.post_cli_status_lines).
    result["cli_channel_id"] = channel_id
    return result


async def _handle_request_host_access(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open the host-path consent card before any CLI attempt."""
    del state
    path = str(action.get("path") or "").strip()
    reason = str(action.get("reason") or "").strip()
    task_id = bound_task_id(agent.id)
    channel_id = consent_origin_channel_id(trigger, task_id=task_id)
    cli_result = request_host_path_access(
        agent=agent,
        raw_path=path,
        reason=reason,
        cwd=get_cli_cwd(agent.id),
        task_id=task_id,
        channel_id=channel_id,
    )
    result = map_cli_result(
        agent, cli_result, command="request_host_access", trigger=trigger
    )
    if cli_result.ok and not cli_result.consent_required:
        result["event"] = "host_path_already_allowed"
    return result
