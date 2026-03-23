"""BossMod AI — Execution action handlers.

Parses flat JSON execution actions from LLM responses and executes them.
These actions only carry out existing commitments. Direct requests are handled
by the decision runtime, not by creating work or movement directly from chat.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.llm.client import count_tokens
from core.models.message import HUMAN_SENDER_ID
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

_VALID_MESSAGE_RECIPIENT_TYPES = {"human", "agent"}
_TASK_LIFECYCLE_ACTIONS = {"complete", "blocked", "delegated", "abandoned"}
_SUPPORTED_ACTIONS = {
    "work",
    "message",
    "walkTo",
    "attendMeeting",
    "remoteMeeting",
    "idle",
    "complete",
    "blocked",
    "delegated",
    "abandoned",
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
    if action_name not in _SUPPORTED_ACTIONS:
        return f'unsupported action "{action_name}"'

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

    if action_name == "attendMeeting" and action.get("agentId") not in (None, ""):
        agent_id = action.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return '"attendMeeting" requires a non-empty "agentId" when provided'

    if action_name in _TASK_LIFECYCLE_ACTIONS and action.get("taskId") not in (None, ""):
        return f'"{action_name}" must not include "taskId"; the runtime binds the active task'

    return None


def _resolve_agent_by_id(agent_id: Any) -> Agent | None:
    """Resolve an explicit agent ID from an action payload."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    return db.get_agent(agent_id.strip())


def _resolve_task_lifecycle_target(
    agent: Agent,
    action: dict[str, Any],
    *,
    action_name: str,
) -> tuple[str | None, str | None]:
    """Resolve the task targeted by a lifecycle action.

    Task lifecycle actions always act on the currently bound active task.
    """
    active_task_id = activity_runtime.get_active_task_id(agent.id)
    if not active_task_id:
        return None, f'"{action_name}" requires an active task'

    return active_task_id, None


def _build_trigger_request(
    *,
    agent_id: str,
    trigger_type: str,
    source_channel: str,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a normalized trigger request emitted by an action."""
    return {
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "source_channel": source_channel,
        "payload": payload,
        "task_id": task_id,
    }


def _resolve_token_model(agent: Agent, action: dict[str, Any]) -> str | None:
    """Resolve the tokenizer model for action-side token accounting."""
    explicit_model = action.get("_token_model")
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model.strip()
    for field in (
        agent.model_work,
        agent.model_social,
        agent.model_reasoning,
        agent.model_extraction,
        agent.model_self_queue,
    ):
        if field and field.strip():
            return field.strip()
    return config.get("default_model_work")


def _count_action_tokens(agent: Agent, action: dict[str, Any], text: str) -> int:
    """Count tokens for persisted artifacts/messages without heuristic fallback."""
    return count_tokens(text, model=_resolve_token_model(agent, action))


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

async def execute_action(
    action: dict[str, Any],
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any] | None = None,
    token_model: str | None = None,
) -> dict[str, Any]:
    """Execute a flat action dict and return the result."""
    if token_model:
        action = {**action, "_token_model": token_model}
    action_type = action["action"]

    handler = _ACTION_HANDLERS.get(action_type)
    if not handler:
        logger.warning("Unknown action '%s' from agent %s", action_type, agent.name)
        return {"event": "status_changed", "detail": f"Unknown action: {action_type}", "agent_name": agent.name}

    return await handler(agent, state, action, trigger)


# ---------------------------------------------------------------------------
# Handlers — each receives the full flat action dict
# ---------------------------------------------------------------------------

async def _handle_work(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Agent produces work output. Must be at a workspace."""
    output = action.get("output", "")
    if not output:
        return {"event": "status_changed", "detail": "Empty work output", "agent_name": agent.name}
    task_id = activity_runtime.get_active_task_id(agent.id)
    if task_id is None:
        return {"event": "agent_error", "detail": "No active work activity is bound", "agent_name": agent.name}

    # Desk rule: must be in a workspace
    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] not in ("workspace",):
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to your desk first.",
            "agent_name": agent.name,
        }

    task = db.get_task(task_id)
    if task and task.status == "accepted":
        db.update_task(task.id, status="active", status_note=None, watchdog_pinged_at=None)

    db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=output,
        message_type="work",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, output),
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
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a message to another agent. Works from any location."""
    recipient_type = (action.get("recipientType") or "").strip().lower()
    content = action.get("content", "")

    if not content:
        return {"event": "status_changed", "detail": "Empty message content", "agent_name": agent.name}

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
        token_count=_count_action_tokens(agent, action, content),
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
            "message_type": message_type,
            "message_id": msg.id,
            "created_at": msg.created_at,
        }
    elif target:
        result["trigger_requests"] = [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel="chat" if message_type == "social" else "work",
            payload={
                "content": content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": message_type,
                "source_message_id": msg.id,
            },
        )]
    return result


async def _handle_walk_to(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
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

    if len(path) <= 1:
        destination_label = destination
        if destination == "desk":
            destination_label = "your desk"
        return {
            "event": "world_feedback",
            "detail": f"You're already at {destination_label}. Choose the next action.",
            "agent_name": agent.name,
        }

    active = activity_runtime.get_active_activity(agent.id)
    if trigger and trigger.get("type") == "social" and active is None:
        active = db.create_runtime_activity(
            agent_id=agent.id,
            kind="social",
            title="Social interaction",
            detail=trigger.get("content"),
        )
        activity_runtime.refresh_agent_status(agent.id)

    activity_runtime.start_movement_activity(
        agent.id,
        destination=destination,
        parent_activity_id=active.id if active else None,
        detail=f"Walking to {destination}",
        metadata={
            "destination": destination,
            "destination_x": dest_x,
            "destination_y": dest_y,
        },
    )

    return {
        "event": "agent_moved",
        "detail": f"{agent.name} walking to {destination} ({len(path)-1} steps)",
        "agent_name": agent.name,
        "path": path,
        "agent_id": agent.id,
        "activity_extra": {
            "agent_id": agent.id,
            "path": path,
            "tiles_per_second": config.get_float("movement_tiles_per_second") or 4.0,
        },
    }


async def _handle_remote_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
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
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "meeting":
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=meeting_content,
            metadata={**active.metadata, "topic": topic, "meeting_mode": "remote"} if topic else {**active.metadata, "meeting_mode": "remote"},
        )
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        activity_runtime.start_meeting_activity(
            agent.id,
            title=topic or "Remote meeting",
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"topic": topic, "meeting_mode": "remote"} if topic else {"meeting_mode": "remote"},
        )

    return {
        "event": "meeting_started",
        "detail": f"{agent.name} started remote meeting with {target.name}" + (f": {topic}" if topic else ""),
        "agent_name": agent.name,
        "trigger_requests": [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel="work",
            payload={
                "content": meeting_content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "meeting",
                "source_message_id": msg.id,
            },
        )],
    }


async def _handle_attend_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attend an in-person meeting from the meeting room."""
    topic = (action.get("topic") or "").strip()
    target = None
    room = get_room_at(state.x, state.y)

    if not room or room["room_type"] != "meeting":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to the meetingRoom first.",
            "agent_name": agent.name,
        }

    if action.get("agentId") not in (None, ""):
        target = _resolve_agent_by_id(action.get("agentId"))
        if target is None:
            return {"event": "status_changed", "detail": "Agent not found for provided agentId", "agent_name": agent.name}

    meeting_content = f"In-person meeting in Meeting Room: {topic}" if topic else "In-person meeting in Meeting Room"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=target.id if target else None,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    detail = f"{agent.name} joined an in-person meeting"
    if target:
        detail += f" with {target.name}"
    if topic:
        detail += f": {topic}"

    result = {
        "event": "meeting_started",
        "detail": detail,
        "agent_name": agent.name,
    }
    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "meeting":
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=meeting_content,
            metadata={**active.metadata, "topic": topic} if topic else active.metadata,
        )
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        activity_runtime.start_meeting_activity(
            agent.id,
            title=topic or "In-person meeting",
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"topic": topic} if topic else {},
        )
    if target:
        result["trigger_requests"] = [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel="chat",
            payload={
                "content": meeting_content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "meeting",
                "source_message_id": msg.id,
            },
        )]
    return result


async def _handle_idle(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Agent has nothing to do."""
    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind != "work":
        activity_runtime.complete_activity(active.id, detail=active.detail)
    activity_runtime.refresh_agent_status(agent.id)
    return {
        "event": "status_changed",
        "detail": f"{agent.name} is idle",
        "agent_name": agent.name,
    }


async def _handle_complete(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as complete."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="complete")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    summary = action.get("summary", "")

    db.update_task(
        task_id,
        status="complete",
        completion_summary=summary or None,
        status_note=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=summary or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} completed task" + (f" — {summary}" if summary else ""),
        "agent_name": agent.name,
    }


async def _handle_blocked(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as blocked."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="blocked")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    db.update_task(
        task_id,
        status="blocked",
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=reason or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    return {
        "event": "status_changed",
        "detail": f"{agent.name} blocked" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
    }


async def _handle_delegated(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delegate current task to another agent."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="delegated")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "status_changed", "detail": "No valid delegate target specified", "agent_name": agent.name}

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

    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=f"Delegated to {target.name}")
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} delegated task to {target.name}",
        "agent_name": agent.name,
    }
    if child:
        result["trigger_requests"] = [build_task_assigned_trigger(child)]
    return result


async def _handle_abandoned(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Abandon current task."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="abandoned")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    db.update_task(
        task_id,
        status="abandoned",
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=reason or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

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
    "attendMeeting": _handle_attend_meeting,
    "remoteMeeting": _handle_remote_meeting,
    "idle": _handle_idle,
    "complete": _handle_complete,
    "blocked": _handle_blocked,
    "delegated": _handle_delegated,
    "abandoned": _handle_abandoned,
}
