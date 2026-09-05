"""Work, walk, idle, and social-message execution handlers.

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions_shared import (
    _ACTION_PROMPT_ALLOWED_PATHS,
    _DESTINATIONS,
    _MAX_INLINE_FILE_DELIVERABLE_WORK_CHARS,
    _build_trigger_request,
    _count_action_tokens,
    _resolve_agent_by_id,
)
from core.agent_loop.deliverables import missing_deliverables, summarize_deliverable
from core.agent_loop.message_delivery import (
    resolve_peer_message_type,
    source_channel_for_message_type,
)
from core.default_prompts import render_default_prompt
from core.models.message import HUMAN_SENDER_ID
from core.models import Agent, AgentState
from core.tasking.service import list_open_child_tasks
from core.tasking.transitions import transition_task
from core.world.pathfinding import find_path
from core.world.tilemap import DEFAULT_DESKS, DEFAULT_ROOMS, MAP_HEIGHT, MAP_WIDTH, get_room_at
import db


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
            "detail": render_default_prompt(
                "internal_action_requires_workspace",
                {"room_name": room_name},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            ),
            "agent_name": agent.name,
            "feedback_code": "walk_to_desk_first",
        }

    task = db.get_task(task_id)
    if task and task.status == "accepted":
        transition_task(
            task.id,
            "active",
            reason="Started producing work.",
            actor=agent.name,
            actor_type="agent",
            actor_agent_id=agent.id,
            status_note=None,
            watchdog_pinged_at=None,
        )
        task = db.get_task(task.id)

    pending_deliverables = missing_deliverables(
        agent_id=agent.id,
        agent_storage_key=agent.storage_key,
        task=task,
    )
    file_deliverables = [item for item in pending_deliverables if item.type == "file" and item.path]
    if file_deliverables and len(output) > _MAX_INLINE_FILE_DELIVERABLE_WORK_CHARS:
        if len(file_deliverables) == 1:
            target = summarize_deliverable(file_deliverables[0])
            detail = render_default_prompt(
                "internal_action_large_work_single_file_guidance",
                {"target": target},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            )
        else:
            targets = ", ".join(summarize_deliverable(item) for item in file_deliverables[:3])
            if len(file_deliverables) > 3:
                targets = f"{targets}, ..."
            detail = render_default_prompt(
                "internal_action_large_work_multi_file_guidance",
                {"targets": targets},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            )
        return {
            "event": "world_feedback",
            "detail": detail,
            "agent_name": agent.name,
            "missing_deliverables": [item.model_dump() for item in pending_deliverables],
        }

    db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=output,
        message_type="work",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, output),
    )

    result = {
        "event": "agent_updated",
        "detail": f"{agent.name} produced work output ({len(output)} chars)",
        "agent_name": agent.name,
    }
    if pending_deliverables:
        result["missing_deliverables"] = [item.model_dump() for item in pending_deliverables]
    return result


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

    active = activity_runtime.get_active_activity(agent.id)
    if (
        target is not None
        and active is not None
        and active.kind in {"assignment", "work"}
    ):
        return _reject_work_lane_agent_chat(
            agent=agent,
            target=target,
            active_task_id=active.task_id,
        )

    if to_agent_id == HUMAN_SENDER_ID:
        message_type = "social" if state.status == "social_active" else "work"
    else:
        message_type = resolve_peer_message_type(state=state, trigger=trigger)
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
            source_channel=source_channel_for_message_type(message_type),
            payload={
                "content": content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": message_type,
                "source_message_id": msg.id,
            },
        )]
    return result


def _reject_work_lane_agent_chat(
    *,
    agent: Agent,
    target: Agent,
    active_task_id: str | None,
) -> dict[str, Any]:
    """Return deterministic repair feedback when work execution tries to use generic coworker chat."""
    if isinstance(active_task_id, str) and active_task_id.strip():
        existing_children = list_open_child_tasks(parent_task_id=active_task_id, assigned_to=target.id)
        if len(existing_children) == 1:
            child = existing_children[0]
            return {
                "event": "world_feedback",
                "detail": (
                    f'There is already an open task thread with {target.name} on "{child.title}" '
                    f'({child.id}). Use "taskmsg" with that task id instead of "socialmsg".'
                ),
                "agent_name": agent.name,
                "task_id": child.id,
                "expected_action": "taskMessage",
            }
        if len(existing_children) > 1:
            return {
                "event": "world_feedback",
                "detail": (
                    f'There is more than one open delegated task for {target.name} under the current work. '
                    'Use "taskmsg" with the specific task id from Task Board, or use "assign" only if this is a new task.'
                ),
                "agent_name": agent.name,
                "task_ids": [task.id for task in existing_children],
                "expected_action": "taskMessage",
            }

    return {
        "event": "world_feedback",
        "detail": (
            'Agent-to-agent communication during work execution must stay in the task system. '
            'Use "assign" to create delegated work, or "taskmsg" to continue an existing task thread. '
            'Use "socialmsg" only for non-task social chat.'
        ),
        "agent_name": agent.name,
        "expected_actions": ["delegateTask", "taskMessage"],
    }


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
async def _handle_idle(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Yield the current turn without changing the active work commitment."""
    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "work":
        return {
            "event": "agent_error",
            "detail": 'Idle is not valid while a task is active. Use "wait", "done", "block", or keep working.',
            "agent_name": agent.name,
        }

    if active and active.kind != "work":
        activity_runtime.complete_activity(active.id, detail=active.detail)
    activity_runtime.refresh_agent_status(agent.id)
    return {
        "event": "status_changed",
        "detail": f"{agent.name} is idle",
        "agent_name": agent.name,
    }
