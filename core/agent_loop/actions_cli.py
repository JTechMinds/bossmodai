"""BossMod CLI execution handler.

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.activity_runtime import get_active_task_id
from core.bm_cli import execute_bm_cli
from core.bm_cli.host_path_consent import request_host_path_access
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.types import BossModCliResult
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
    cli_result = execute_bm_cli(
        agent,
        state,
        command,
        content if isinstance(content, str) else None,
        trigger_type=(trigger or {}).get("type") if isinstance(trigger, dict) else None,
    )
    return _cli_action_result(agent, cli_result, command=command)


async def _handle_request_host_access(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open the host-path consent card before any CLI attempt."""
    del state, trigger
    path = str(action.get("path") or "").strip()
    reason = str(action.get("reason") or "").strip()
    cli_result = request_host_path_access(
        agent=agent,
        raw_path=path,
        reason=reason,
        cwd=get_cli_cwd(agent.id),
        task_id=get_active_task_id(agent.id),
    )
    result = _cli_action_result(agent, cli_result, command="request_host_access")
    if cli_result.ok and not cli_result.consent_required:
        result["event"] = "host_path_already_allowed"
    return result


def _cli_action_result(
    agent: Agent,
    cli_result: BossModCliResult,
    *,
    command: str,
) -> dict[str, Any]:
    """Map a CLI / host-access result onto the execution-turn payload."""
    result = {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "agent_name": agent.name,
        "cli_prompt_content": cli_result.prompt_content,
        "suppress_world_broadcast": True,
        "suppress_activity_broadcast": not (
            cli_result.approval_required or cli_result.consent_required
        ),
    }
    if cli_result.approval_required:
        result["approval_required"] = True
        result["approval_request_id"] = cli_result.approval_request_id
        result["event"] = "cli_approval_required"
        result["detail"] = f"{agent.name} requests approval: {command}"
    if cli_result.consent_required:
        data = cli_result.data or {}
        card = data.get("host_path_consent") if isinstance(data.get("host_path_consent"), dict) else {}
        path = card.get("path") or "host path"
        result["consent_required"] = True
        result["consent_request_id"] = cli_result.consent_request_id
        result["consent_reused"] = bool(data.get("consent_reused"))
        result["host_path_consent"] = card
        result["event"] = "host_path_consent_required"
        result["detail"] = f"{agent.name} requests host-path access: {path}"
    return result
