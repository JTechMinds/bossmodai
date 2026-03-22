"""BossMod AI — Agent action handlers.

Parses flat JSON actions from LLM responses and executes them.
Actions are intent declarations — the system handles avatar mechanics.

Flat JSON format (no nested params):
  {"action": "work", "output": "...", "thought": "..."}
  {"action": "walkTo", "destination": "breakRoom", "thought": "..."}
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm.client import count_tokens
from core.models import Agent, AgentState
from core.world.pathfinding import find_path
from core.world.tilemap import DEFAULT_DESKS, DEFAULT_ROOMS, MAP_HEIGHT, MAP_WIDTH, get_room_at
import db

logger = logging.getLogger(__name__)

# Actions that end the multi-turn loop and return agent to idle
TERMINAL_ACTIONS = {"idle", "complete", "blocked", "delegated", "abandoned"}

# camelCase destination names → internal room IDs
_DESTINATIONS = {
    "desk": None,  # special: resolved to agent's desk_x/desk_y
    "meetingRoom": "meeting_room",
    "breakRoom": "break_room",
    "mainWorkspace": "workspace_main",
    "southWorkspace": "workspace_south",
    "hallway": "hallway_main",
}


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_action(raw_response: str) -> dict[str, Any]:
    """Extract a flat JSON action from the LLM response.

    Returns the full parsed dict. Returns ``_parse_failed`` action on failure
    so callers can distinguish "agent chose idle" from "LLM returned garbage".
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
                return {"action": "_parse_failed", "thought": "Failed to parse response", "_raw_snippet": text[:200]}
        else:
            logger.warning("No JSON found in response: %s", text[:200])
            return {"action": "_parse_failed", "thought": "No action in response", "_raw_snippet": text[:200]}

    if "action" not in parsed:
        logger.warning("No 'action' key in response: %s", parsed)
        return {"action": "_parse_failed", "thought": parsed.get("thought", ""), "_raw_snippet": str(parsed)[:200]}

    if not isinstance(parsed["action"], str):
        logger.warning("Action is not a string (%s): %s", type(parsed["action"]).__name__, parsed)
        return {"action": "_parse_failed", "thought": parsed.get("thought", ""), "_raw_snippet": str(parsed)[:200]}

    return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_agent_by_name(name: str) -> Agent | None:
    """Find an agent by case-insensitive name match."""
    agents = db.list_agents()
    return next((a for a in agents if a.name.lower() == name.lower()), None)


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

async def execute_action(
    action: dict[str, Any],
    agent: Agent,
    state: AgentState,
) -> dict[str, Any]:
    """Execute a flat action dict and return the result."""
    action_type = action["action"]

    handler = _ACTION_HANDLERS.get(action_type)
    if not handler:
        logger.warning("Unknown action '%s' from agent %s", action_type, agent.name)
        return {"event": "status_changed", "detail": f"Unknown action: {action_type}", "agent_name": agent.name}

    return await handler(agent, state, action)


# ---------------------------------------------------------------------------
# Handlers — each receives the full flat action dict
# ---------------------------------------------------------------------------

async def _handle_work(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Agent produces work output. Must be at a workspace."""
    output = action.get("output", "")
    if not output:
        return {"event": "status_changed", "detail": "Empty work output", "agent_name": agent.name}

    # Desk rule: must be in a workspace
    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] not in ("workspace",):
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to your desk first.",
            "agent_name": agent.name,
        }

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


async def _handle_message(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Send a message to another agent. Works from any location."""
    to_name = action.get("to")
    content = action.get("content", "")

    if not content:
        return {"event": "status_changed", "detail": "Empty message content", "agent_name": agent.name}

    from core.models.message import HUMAN_SENDER_ID

    to_agent_id = None
    if to_name:
        # Recognize "human" / "human operator" as the human operator
        if to_name.lower() in ("human", "human operator", "operator", "boss"):
            to_agent_id = HUMAN_SENDER_ID
        else:
            target = _find_agent_by_name(to_name)
            if target:
                to_agent_id = target.id
            else:
                return {"event": "status_changed", "detail": f"Agent not found: {to_name}", "agent_name": agent.name}

    db.create_message(
        from_agent=agent.id,
        to_agent=to_agent_id,
        content=content,
        message_type="work",
        location_x=state.x,
        location_y=state.y,
        token_count=count_tokens(content),
    )

    to_display = to_name or "everyone"
    return {
        "event": "message_sent",
        "detail": f"{agent.name} → {to_display}: {content[:80]}{'...' if len(content) > 80 else ''}",
        "agent_name": agent.name,
        "content": content,
    }


async def _handle_walk_to(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Move avatar to a destination."""
    destination = action.get("destination", "")

    if destination not in _DESTINATIONS:
        return {
            "event": "world_feedback",
            "detail": f"Unknown destination: {destination}. Valid: {', '.join(_DESTINATIONS.keys())}",
            "agent_name": agent.name,
        }

    # Resolve destination to coordinates
    if destination == "desk":
        # Use agent's assigned desk
        if agent.desk_x is not None and agent.desk_y is not None:
            dest_x, dest_y = agent.desk_x, agent.desk_y
        else:
            # Find first unassigned desk chair
            desk = next((d for d in DEFAULT_DESKS), None)
            if desk:
                dest_x, dest_y = desk["chair_xy"]
            else:
                return {"event": "world_feedback", "detail": "No desk available", "agent_name": agent.name}
    else:
        room_id = _DESTINATIONS[destination]
        room = next((r for r in DEFAULT_ROOMS if r["id"] == room_id), None)
        if not room:
            return {"event": "world_feedback", "detail": f"Room not found: {room_id}", "agent_name": agent.name}
        bounds = room["bounds"]
        dest_x = (bounds[0] + bounds[2]) // 2
        dest_y = (bounds[1] + bounds[3]) // 2

    if not (0 <= dest_x < MAP_WIDTH and 0 <= dest_y < MAP_HEIGHT):
        return {"event": "world_feedback", "detail": f"Destination out of bounds", "agent_name": agent.name}

    path = find_path(state.x, state.y, dest_x, dest_y)
    if not path:
        return {"event": "world_feedback", "detail": f"No path to {destination}", "agent_name": agent.name}

    db.update_agent_state(agent.id, status="in_transit")

    return {
        "event": "agent_moved",
        "detail": f"{agent.name} walking to {destination} ({len(path)-1} steps)",
        "agent_name": agent.name,
        "path": path,
        "agent_id": agent.id,
    }


async def _handle_remote_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Start a remote meeting from the agent's current location."""
    with_name = action.get("with", "")
    topic = action.get("topic", "")

    if not with_name:
        return {"event": "status_changed", "detail": "No meeting participant specified", "agent_name": agent.name}

    # Resolve target agent
    target = _find_agent_by_name(with_name)
    if not target:
        return {"event": "status_changed", "detail": f"Agent not found: {with_name}", "agent_name": agent.name}

    # Send a meeting request message
    meeting_content = f"Remote meeting requested: {topic}" if topic else "Remote meeting requested"
    db.create_message(
        from_agent=agent.id,
        to_agent=target.id,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=count_tokens(meeting_content),
    )

    return {
        "event": "meeting_started",
        "detail": f"{agent.name} started remote meeting with {with_name}" + (f": {topic}" if topic else ""),
        "agent_name": agent.name,
        "content": meeting_content,
    }


async def _handle_idle(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Agent has nothing to do."""
    db.update_agent_state(agent.id, status="idle")
    return {
        "event": "status_changed",
        "detail": f"{agent.name} is idle",
        "agent_name": agent.name,
    }


async def _handle_complete(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Mark current task as complete."""
    task_id = action.get("taskId") or state.current_task_id
    summary = action.get("summary", "")

    if task_id:
        db.update_task(task_id, status="complete")

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} completed task" + (f" — {summary}" if summary else ""),
        "agent_name": agent.name,
    }


async def _handle_blocked(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Mark current task as blocked."""
    task_id = action.get("taskId") or state.current_task_id
    reason = action.get("reason", "")

    if task_id:
        db.update_task(task_id, status="blocked")

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} blocked" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
    }


async def _handle_delegated(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Delegate current task to another agent."""
    task_id = action.get("taskId") or state.current_task_id
    to_name = action.get("to", "")

    if not to_name:
        return {"event": "status_changed", "detail": "No delegate target specified", "agent_name": agent.name}

    # Resolve target
    target = _find_agent_by_name(to_name)
    if not target:
        return {"event": "status_changed", "detail": f"Agent not found: {to_name}", "agent_name": agent.name}

    if task_id:
        db.update_task(task_id, status="delegated")

        # Create a child task for the target agent (vision doc: delegation
        # creates a formal task record with its own watchdog)
        original_task = db.get_task(task_id)
        if original_task:
            db.create_task(
                title=original_task.title,
                description=original_task.description,
                project=original_task.project,
                assigned_to=target.id,
                created_by=agent.id,
                parent_task_id=task_id,
            )

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} delegated task to {to_name}",
        "agent_name": agent.name,
    }


async def _handle_abandoned(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Abandon current task."""
    task_id = action.get("taskId") or state.current_task_id
    reason = action.get("reason", "")

    if task_id:
        db.update_task(task_id, status="abandoned")

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} abandoned task" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_ACTION_HANDLERS = {
    "work": _handle_work,
    "message": _handle_message,
    "walkTo": _handle_walk_to,
    "remoteMeeting": _handle_remote_meeting,
    "idle": _handle_idle,
    "complete": _handle_complete,
    "blocked": _handle_blocked,
    "delegated": _handle_delegated,
    "abandoned": _handle_abandoned,
}
