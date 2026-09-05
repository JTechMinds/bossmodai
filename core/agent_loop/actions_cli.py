"""BossMod CLI execution handler.

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from typing import Any

from core.bm_cli import execute_bm_cli
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
    result = {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "agent_name": agent.name,
        "cli_prompt_content": cli_result.prompt_content,
        "suppress_world_broadcast": True,
        "suppress_activity_broadcast": not cli_result.approval_required,
    }
    if cli_result.approval_required:
        result["approval_required"] = True
        result["approval_request_id"] = cli_result.approval_request_id
        result["event"] = "cli_approval_required"
        result["detail"] = f"{agent.name} requests approval: {command}"
    return result
