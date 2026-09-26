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

    from core.floors import peers_share_floor

    teammates = [
        agent for agent in db.list_agents()
        if agent.id != agent_id and peers_share_floor(agent_id, agent.id)
    ]
    for teammate in teammates:
        role = f" ({teammate.role})" if teammate.role else ""
        bar = teammate.done_fail_bar.strip() if teammate.done_fail_bar else ""
        if len(bar) > 120:
            bar = f"{bar[:117]}..."
        suffix = f" — done/fail: {bar}" if bar else ""
        materials.append(f"- {teammate.name}{role} — agentId: {teammate.id}{suffix}")

    return materials

_CHANNEL_WAKE_TYPES = {"channel_message", "channel_response"}
_LATEST_LINE_KEYS = ("latest_from_name", "latest_content", "latest_author_type", "latest_from_agent")
# Same window as channel_rounds._latest_channel_line.
_LATEST_LINE_WINDOW = 12


def stamp_channel_latest_line(agent_id: str, trigger: dict[str, Any]) -> None:
    """Stamp the newest thread line on a shared-channel wake, resolved at turn time.

    A channel wake keeps its snapshot opener (``content``, ``from_name``,
    ``author_type``, ``from_agent``, ``source_message_id``) because engine
    logic reads it. When a newer human or agent line exists by someone
    other than ``agent_id``, this sets ``latest_from_name``,
    ``latest_content``, ``latest_author_type`` and ``latest_from_agent``
    so the model-facing "current message" is that line, not the opener.
    Resolving here, when the turn runs, means queue delay can never make
    it stale.

    Args:
        agent_id: The agent taking the turn. Its own posts are never its
            current message.
        trigger: The wake payload, mutated in place. Only
            ``channel_message`` / ``channel_response`` triggers with a
            non-empty ``channel_id`` are touched.

    When the newest qualifying line is the opener itself, or there is
    none, any ``latest_*`` keys are removed so a retried trigger does not
    carry old values. System lines and round markers are skipped.
    """
    if trigger.get("type") not in _CHANNEL_WAKE_TYPES:
        return
    channel_id = trigger.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return
    for key in _LATEST_LINE_KEYS:
        trigger.pop(key, None)
    for row in reversed(db.list_channel_messages(channel_id, limit=_LATEST_LINE_WINDOW)):
        if row.author_type not in {"human", "agent"}:
            continue
        if row.author_agent_id == agent_id:
            continue
        content = (row.content or "").strip()
        if not content:
            continue
        if row.id == trigger.get("source_message_id"):
            return
        trigger["latest_from_name"] = row.author_name
        trigger["latest_content"] = content
        trigger["latest_author_type"] = row.author_type
        trigger["latest_from_agent"] = row.author_agent_id or ""
        return
