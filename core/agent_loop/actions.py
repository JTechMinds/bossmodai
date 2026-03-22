"""BossMod AI — Agent action handlers.

Parses flat JSON actions from LLM responses and executes them.
Actions are intent declarations — the system handles avatar mechanics.

Flat JSON format (no nested params):
  {"action": "work", "output": "...", "tracking": "task", "thought": "..."}
  {"action": "walkTo", "destination": "breakRoom", "tracking": "chat", "thought": "..."}
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

_TRACKING_REQUIRED_ACTIONS = {"work", "walkTo", "remoteMeeting"}
_VALID_TRACKING = {"chat", "task"}
_VALID_MESSAGE_RECIPIENT_TYPES = {"human", "agent"}


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

    if not isinstance(parsed, dict):
        logger.warning("Parsed action is not an object: %s", parsed)
        return {"action": "_parse_failed", "thought": "", "_raw_snippet": "Action payload must be a JSON object"}

    if "action" not in parsed:
        logger.warning("No 'action' key in response: %s", parsed)
        return {"action": "_parse_failed", "thought": parsed.get("thought", ""), "_raw_snippet": str(parsed)[:200]}

    if not isinstance(parsed["action"], str):
        logger.warning("Action is not a string (%s): %s", type(parsed["action"]).__name__, parsed)
        return {"action": "_parse_failed", "thought": parsed.get("thought", ""), "_raw_snippet": str(parsed)[:200]}

    validation_error = _validate_action_payload(parsed)
    if validation_error:
        logger.warning("Invalid action payload for %s: %s", parsed["action"], validation_error)
        return {
            "action": "_parse_failed",
            "thought": parsed.get("thought", ""),
            "_raw_snippet": f'{parsed["action"]}: {validation_error}'[:200],
        }

    return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_action_payload(action: dict[str, Any]) -> str | None:
    """Validate shape and required fields for parsed actions."""
    action_name = action["action"]

    if action_name in _TRACKING_REQUIRED_ACTIONS:
        tracking = action.get("tracking")
        if not isinstance(tracking, str) or tracking.strip().lower() not in _VALID_TRACKING:
            return 'missing or invalid "tracking"'
        if action_name == "work" and tracking.strip().lower() != "task":
            return '"work" requires tracking="task"'

    if action_name == "message":
        recipient_type = action.get("recipientType")
        if not isinstance(recipient_type, str) or recipient_type.strip().lower() not in _VALID_MESSAGE_RECIPIENT_TYPES:
            return 'missing or invalid "recipientType"'
        if recipient_type.strip().lower() == "agent":
            agent_id = action.get("agentId")
            if not isinstance(agent_id, str) or not agent_id.strip():
                return '"message" to an agent requires a non-empty "agentId"'
        else:
            if action.get("agentId") not in (None, ""):
                return '"message" to the human operator must not include "agentId"'

    if action_name in {"remoteMeeting", "delegated"}:
        agent_id = action.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return f'"{action_name}" requires a non-empty "agentId"'

    return None


def _resolve_agent_by_id(agent_id: Any) -> Agent | None:
    """Resolve an explicit agent ID from an action payload."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    return db.get_agent(agent_id.strip())


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
    }


async def _handle_message(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Send a message to another agent. Works from any location."""
    recipient_type = (action.get("recipientType") or "").strip().lower()
    content = action.get("content", "")

    if not content:
        return {"event": "status_changed", "detail": "Empty message content", "agent_name": agent.name}

    from core.models.message import HUMAN_SENDER_ID

    target = None
    if recipient_type == "human":
        to_agent_id = HUMAN_SENDER_ID
        to_display = "Human Operator"
    else:
        target = _resolve_agent_by_id(action.get("agentId"))
        if target is None:
            return {"event": "status_changed", "detail": "Agent not found for provided agentId", "agent_name": agent.name}
        to_agent_id = target.id
        to_display = target.name

    message_type = "social" if state.status == "social_active" else "work"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=to_agent_id,
        content=content,
        message_type=message_type,
        location_x=state.x,
        location_y=state.y,
        token_count=count_tokens(content),
    )

    result = {
        "event": "message_sent",
        "detail": f"{agent.name} → {to_display}: {content[:80]}{'...' if len(content) > 80 else ''}",
        "agent_name": agent.name,
    }
    if to_agent_id == HUMAN_SENDER_ID:
        result["chat_message"] = {
            "agent_id": agent.id,
            "content": content,
            "from_type": "agent",
            "from_name": agent.name,
            "message_id": msg.id,
            "created_at": msg.created_at,
        }
    elif target:
        result["queued_triggers"] = [{
            "agent_id": target.id,
            "trigger_type": "peer_message",
            "source_channel": "chat" if message_type == "social" else "work",
            "payload": {
                "content": content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": message_type,
                "source_message_id": msg.id,
            },
        }]
    return result


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
    target = _resolve_agent_by_id(action.get("agentId"))
    topic = action.get("topic", "")

    if target is None:
        return {"event": "status_changed", "detail": "No valid meeting participant specified", "agent_name": agent.name}

    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] != "workspace":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to your desk first.",
            "agent_name": agent.name,
        }

    # Send a meeting request message
    meeting_content = f"Remote meeting requested: {topic}" if topic else "Remote meeting requested"
    msg = db.create_message(
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
        "detail": f"{agent.name} started remote meeting with {target.name}" + (f": {topic}" if topic else ""),
        "agent_name": agent.name,
        "queued_triggers": [{
            "agent_id": target.id,
            "trigger_type": "peer_message",
            "source_channel": "work",
            "payload": {
                "content": meeting_content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "meeting",
                "source_message_id": msg.id,
            },
        }],
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
        db.update_task(
            task_id,
            status="complete",
            completion_summary=summary or None,
            status_note=None,
            watchdog_pinged_at=None,
        )

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
        db.update_task(
            task_id,
            status="blocked",
            status_note=reason or None,
            completion_summary=None,
            watchdog_pinged_at=None,
        )

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
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "status_changed", "detail": "No valid delegate target specified", "agent_name": agent.name}

    if task_id:
        db.update_task(
            task_id,
            status="delegated",
            status_note=f"Delegated to {target.name}",
            watchdog_pinged_at=None,
        )

        # Create a child task for the target agent (vision doc: delegation
        # creates a formal task record with its own watchdog)
        original_task = db.get_task(task_id)
        if original_task:
            child = db.create_task(
                title=original_task.title,
                description=original_task.description,
                project=original_task.project,
                assigned_to=target.id,
                created_by=agent.id,
                parent_task_id=task_id,
            )
        else:
            child = None
    else:
        child = None

    db.update_agent_state(agent.id, status="idle", current_task_id=None)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} delegated task to {target.name}",
        "agent_name": agent.name,
    }
    if child:
        result["queued_triggers"] = [{
            "agent_id": target.id,
            "trigger_type": "task_assigned",
            "source_channel": "work",
            "task_id": child.id,
            "payload": {
                "task_title": child.title,
                "task_description": child.description or "",
                "project": child.project,
            },
        }]
    return result


async def _handle_abandoned(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Abandon current task."""
    task_id = action.get("taskId") or state.current_task_id
    reason = action.get("reason", "")

    if task_id:
        db.update_task(
            task_id,
            status="abandoned",
            status_note=reason or None,
            completion_summary=None,
            watchdog_pinged_at=None,
        )

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
