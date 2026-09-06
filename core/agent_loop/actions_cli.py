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
