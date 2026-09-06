"""Gather prompt-snapshot fields and classify the current trigger."""

from __future__ import annotations

from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.llm import routing
from core.models import AgentState
import db

_DECISION_TRIGGER_TYPES = {
    "human_chat",
    "peer_message",
    "task_follow_up",
    "task_update",
    "session_message",
    "session_response",
    "channel_message",
    "channel_response",
    "task_assigned",
    "watchdog_status_ping",
}
_COMMUNICATION_TRIGGER_TYPES = _DECISION_TRIGGER_TYPES - {"task_assigned"}


def _determine_mode(trigger: dict[str, Any]) -> routing.ActivationMode:
    """Map a trigger to an activation mode for model selection."""
    trigger_type = trigger.get("type", "")

    if trigger_type == "social":
        return "social"

    return "work"

def _contract_kind_for_trigger(trigger_type: str) -> str:
    """Return the prompt contract kind for one trigger."""
    if trigger_type in _DECISION_TRIGGER_TYPES:
        return "decision"
    return "execution"

def _get_nearby_agents(
    agent_id: str,
    state: AgentState,
) -> list[dict[str, Any]]:
    """Find agents within proximity of the current agent."""
    radius = config.get_int("social_proximity_tiles") or 8
    return db.get_nearby_agents(agent_id, state.x, state.y, radius)

def _get_current_task(agent_id: str) -> dict[str, Any] | None:
    """Fetch the agent's current task if any."""
    active = activity_runtime.get_active_activity(agent_id)
    if not active or not active.task_id:
        return None

    task = db.get_task(active.task_id)
    if not task:
        return None

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "project": task.project,
        "work_contract": task.work_contract.model_dump() if task.work_contract else None,
        "completion_summary": task.completion_summary,
        "status_note": task.status_note,
    }

def _get_current_activity(agent_id: str) -> dict[str, Any] | None:
    """Fetch the current runtime activity for prompt rendering."""
    activity = activity_runtime.get_active_activity(agent_id)
    if not activity:
        return None
    return {
        "id": activity.id,
        "kind": activity.kind,
        "status": activity.status,
        "title": activity.title,
        "detail": activity.detail,
        "destination": activity.destination,
        "metadata": activity.metadata,
    }

def _get_current_session(agent_id: str, trigger: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch the active meeting session context when relevant."""
    session_id = trigger.get("session_id")
    session = db.get_meeting_session(session_id) if isinstance(session_id, str) and session_id.strip() else None
    if session is None:
        session = db.get_active_meeting_session_for_agent(agent_id)
    if session is None:
        return None
    participants = db.list_active_meeting_participants(session.room_id)
    meta = db.get_meeting_session_meta(session.id)
    expected = db.list_meeting_participant_details(session.id) if meta is not None else []
    return {
        "id": session.id,
        "title": session.title,
        "room_id": session.room_id,
        "room_name": "Meeting Room" if session.room_id == "meeting_room" else session.room_id,
        "participants": participants,
        "phase": (meta or {}).get("phase") if meta is not None else None,
        "expected_participants": expected,
    }

def _get_current_channel(trigger: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch the active shared channel context when relevant."""
    channel_id = trigger.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        return None
    return {
        "id": channel.id,
        "name": channel.name,
        "kind": channel.kind,
        "participants": db.list_channel_member_details(channel.id),
    }

def _get_reference_materials(agent_id: str) -> list[str]:
    """Build non-chat references for the turn."""
    materials: list[str] = []

    teammates = [agent for agent in db.list_agents() if agent.id != agent_id]
    for teammate in teammates:
        role = f" ({teammate.role})" if teammate.role else ""
        bar = teammate.done_fail_bar.strip() if teammate.done_fail_bar else ""
        if len(bar) > 120:
            bar = f"{bar[:117]}..."
        suffix = f" — done/fail: {bar}" if bar else ""
        materials.append(f"- {teammate.name}{role} — agentId: {teammate.id}{suffix}")

    return materials
