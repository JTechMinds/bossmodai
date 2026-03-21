"""BossMod AI — Agent action handlers.

Parses the JSON action from an LLM response and executes it:
walk_to, send_message, work, idle, sign_off. Each action modifies
agent state and/or creates database records.

Walk_to returns path data in the result — the caller (simulation)
manages movement. No circular imports.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm.client import count_tokens
from core.models import Agent, AgentState
from core.world.pathfinding import find_path
from core.world.tilemap import DEFAULT_ROOMS, MAP_HEIGHT, MAP_WIDTH
import db

_VALID_TASK_STATUSES = {"complete", "blocked", "delegated", "abandoned"}

logger = logging.getLogger(__name__)


def parse_action(raw_response: str) -> dict[str, Any]:
    """Extract a JSON action object from the LLM response text.

    Handles markdown code fences and extra text around JSON.
    Falls back to idle if parsing fails.
    """
    text = raw_response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse action JSON: %s", text[:200])
                return {"thought": "Failed to parse response", "action": "idle", "params": {}}
        else:
            logger.warning("No JSON found in response: %s", text[:200])
            return {"thought": "No action in response", "action": "idle", "params": {}}

    if "action" not in parsed:
        logger.warning("No 'action' key in response: %s", parsed)
        return {"thought": parsed.get("thought", ""), "action": "idle", "params": {}}

    action_value = parsed["action"]

    # Guard against LLMs returning action as a nested object instead of a string
    if not isinstance(action_value, str):
        logger.warning("Action is not a string (%s): %s", type(action_value).__name__, parsed)
        return {"thought": parsed.get("thought", ""), "action": "idle", "params": {}}

    return {
        "thought": parsed.get("thought", ""),
        "action": action_value,
        "params": parsed.get("params", {}),
    }


async def execute_action(
    action: dict[str, Any],
    agent: Agent,
    state: AgentState,
) -> dict[str, Any]:
    """Execute a parsed agent action and return the result.

    For ``walk_to``, the result includes ``"path"`` and ``"agent_id"``
    so the simulation can manage movement without circular imports.
    """
    action_type = action["action"]
    params = action.get("params", {})

    handler = _ACTION_HANDLERS.get(action_type)
    if not handler:
        logger.warning("Unknown action '%s' from agent %s", action_type, agent.name)
        return {"event": "status_changed", "detail": f"Unknown action: {action_type}"}

    return await handler(agent, state, params)


async def _handle_walk_to(
    agent: Agent,
    state: AgentState,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Compute path and return it for the simulation to manage."""
    room_name = params.get("room")
    dest_x = params.get("x")
    dest_y = params.get("y")

    if room_name:
        room = next((r for r in DEFAULT_ROOMS if r["id"] == room_name), None)
        if not room:
            return {"event": "status_changed", "detail": f"Unknown room: {room_name}"}
        bounds = room["bounds"]
        dest_x = (bounds[0] + bounds[2]) // 2
        dest_y = (bounds[1] + bounds[3]) // 2

    if dest_x is None or dest_y is None:
        return {"event": "status_changed", "detail": "No destination specified"}

    if not (0 <= dest_x < MAP_WIDTH and 0 <= dest_y < MAP_HEIGHT):
        return {"event": "status_changed", "detail": f"Destination out of bounds: ({dest_x}, {dest_y})"}

    path = find_path(state.x, state.y, dest_x, dest_y)
    if not path:
        return {"event": "status_changed", "detail": f"No path to ({dest_x}, {dest_y})"}

    db.update_agent_state(agent.id, status="in_transit")

    destination_name = room_name or f"({dest_x}, {dest_y})"
    return {
        "event": "agent_moved",
        "detail": f"{agent.name} walking to {destination_name} ({len(path)-1} steps)",
        "agent_name": agent.name,
        # Path data for the simulation to consume (not broadcast)
        "path": path,
        "agent_id": agent.id,
    }


async def _handle_send_message(
    agent: Agent,
    state: AgentState,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Send a message to another agent."""
    to_name = params.get("to")
    content = params.get("content", "")
    msg_type = params.get("type", "work")

    if not content:
        return {"event": "status_changed", "detail": "Empty message content"}

    to_agent_id = None
    if to_name:
        agents = db.list_agents()
        target = next((a for a in agents if a.name.lower() == to_name.lower()), None)
        if target:
            to_agent_id = target.id
        else:
            return {"event": "status_changed", "detail": f"Agent not found: {to_name}"}

    token_count = count_tokens(content)

    db.create_message(
        from_agent=agent.id,
        to_agent=to_agent_id,
        content=content,
        message_type=msg_type,
        location_x=state.x,
        location_y=state.y,
        token_count=token_count,
    )

    to_display = to_name or "everyone"
    return {
        "event": "message_sent",
        "detail": f"{agent.name} → {to_display}: {content[:80]}{'...' if len(content) > 80 else ''}",
        "agent_name": agent.name,
        "content": content,
    }


async def _handle_work(
    agent: Agent,
    state: AgentState,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Agent produces work output."""
    output = params.get("output", "")
    if not output:
        return {"event": "status_changed", "detail": "Empty work output"}

    db.update_agent_state(agent.id, status="work_active")

    db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=output,
        message_type="work",
        location_x=state.x,
        location_y=state.y,
        token_count=count_tokens(output),
    )

    return {
        "event": "agent_updated",
        "detail": f"{agent.name} produced work output ({len(output)} chars)",
        "agent_name": agent.name,
        "content": output,
    }


async def _handle_idle(
    agent: Agent,
    state: AgentState,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Agent does nothing this turn."""
    db.update_agent_state(agent.id, status="idle")
    return {
        "event": "status_changed",
        "detail": f"{agent.name} is idle",
        "agent_name": agent.name,
    }


async def _handle_sign_off(
    agent: Agent,
    state: AgentState,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Agent completes or abandons current work."""
    status = params.get("status", "complete")
    summary = params.get("summary", "")

    if status not in _VALID_TASK_STATUSES:
        logger.warning("Unknown sign_off status '%s' from %s, defaulting to 'complete'", status, agent.name)
        status = "complete"

    if state.current_task_id:
        db.update_task(state.current_task_id, status=status)

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} signed off: {status}" + (f" — {summary}" if summary else ""),
        "agent_name": agent.name,
    }


_ACTION_HANDLERS = {
    "walk_to": _handle_walk_to,
    "send_message": _handle_send_message,
    "work": _handle_work,
    "idle": _handle_idle,
    "sign_off": _handle_sign_off,
}
