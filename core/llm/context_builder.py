"""BossMod AI — LLM context assembly."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import db
from core import config
from core.agent_loop.action_contract import render_action_contract
from core.agent_loop.deliverables import format_deliverables_for_context
from core.agent_loop.decision_contract import render_decision_contract
from core.models import Agent, AgentState
from core.models.notification import Notification
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)



_STATUS_LABELS = {
    "idle": "idle",
    "work_active": "working",
    "social_active": "socializing",
    "in_transit": "walking",
}


@dataclass
class TurnContext:
    """Structured input for building an agent prompt."""

    agent: Agent
    state: AgentState
    trigger: dict[str, Any]
    conversation_history: list[dict[str, Any]]
    prompt_notifications: list[Notification]
    reference_materials: list[str]
    current_activity: dict[str, Any] | None = None
    current_task: dict[str, Any] | None = None
    nearby_agents: list[dict[str, Any]] | None = None
    pending_trigger_count: int = 0
    contract_kind: str = "execution"


def build_context(turn: TurnContext) -> list[dict[str, str]]:
    """Assemble the full message list for an agent turn."""
    messages: list[dict[str, str]] = []

    # ─── Build template variables ───
    personality = turn.agent.prompt_template or _default_role_prompt(turn.agent)
    personality = (
        personality
        .replace("{{agent_name}}", turn.agent.name)
        .replace("{{role}}", turn.agent.role or "AI Assistant")
    )

    variables = {
        "{{personality}}": personality,
        "{{agent_name}}": turn.agent.name,
        "{{role}}": turn.agent.role or "AI Assistant",
        "{{worldStatus}}": _format_world_status(
            turn.agent,
            turn.state,
            turn.nearby_agents,
            turn.current_activity,
            turn.current_task,
            turn.pending_trigger_count,
        ),
        "{{activity}}": _format_activity(turn.current_activity),
        "{{task}}": _format_task(turn.current_task),
        "{{pending_tasks}}": _format_pending_tasks(turn.agent.id, turn.current_task),
        "{{references}}": _format_references(turn.agent.id, turn.reference_materials, turn.prompt_notifications),
    }

    # ─── Resolve template from settings ───
    template = config.require("system_prompt_template")
    system_prompt = template
    for key, value in variables.items():
        system_prompt = system_prompt.replace(key, value)

    messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "system",
        "content": _render_turn_contract(turn.contract_kind),
    })

    for msg in turn.conversation_history:
        role = "assistant" if msg.get("from_agent") == turn.agent.id else "user"
        sender = msg.get("from_name", "Unknown")
        content = msg.get("content", "")

        if role == "user":
            messages.append({"role": "user", "content": f"[{sender}]: {content}"})
        else:
            messages.append({"role": "assistant", "content": content})

    # ─── Trigger event ───
    messages.append({"role": "user", "content": _format_trigger(turn.trigger, turn.contract_kind)})

    return messages


def _default_role_prompt(agent: Agent) -> str:
    """Generate a default system prompt for agents without a custom template."""
    role = agent.role or "AI Assistant"
    return (
        f"You are {agent.name}, a {role} at BossMod AI. "
        f"You work in a virtual office with other AI agents. "
        f"You communicate professionally, stay focused on your tasks, "
        f"and collaborate effectively with your team."
    )


def _format_world_status(
    agent: Agent,
    state: AgentState,
    nearby_agents: list[dict[str, Any]] | None = None,
    current_activity: dict[str, Any] | None = None,
    current_task: dict[str, Any] | None = None,
    pending_trigger_count: int = 0,
) -> str:
    """Build the structured world status block."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)

    pending_count = pending_trigger_count
    open_tasks = _list_open_tasks(agent.id, current_task)

    # Nearby agents
    nearby_str = "none"
    if nearby_agents:
        names = [a.get("name", "Unknown") for a in nearby_agents]
        nearby_str = ", ".join(names)

    task_str = "none"
    if current_task:
        task_str = f"{current_task.get('title', 'Untitled')} ({current_task.get('status', 'unknown')})"
    activity_str = "none"
    if current_activity:
        activity_str = current_activity.get("kind", "unknown")
        if current_activity.get("title"):
            activity_str += f' - {current_activity["title"]}'

    return (
        f"location: {room_name}\n"
        f"status: {status_label}\n"
        f"nearby_agents: {nearby_str}\n"
        f"pending_triggers: {pending_count}\n"
        f"open_task_count: {len(open_tasks)}\n"
        f"current_activity: {activity_str}\n"
        f"current_task: {task_str}"
    )

def _format_task(task: dict[str, Any]) -> str:
    if not task:
        return "none"
    title = task.get("title", "Untitled")
    desc = task.get("description", "No description")
    status = task.get("status", "unknown")
    summary = task.get("completion_summary") or task.get("status_note")
    details = [
        f"id: {task.get('id', 'unknown')}",
        f"title: {title}",
        f"status: {status}",
        f"description: {desc}",
    ]
    if summary:
        details.append(f"latest_summary: {summary}")
    deliverable_lines = format_deliverables_for_context(task)
    if deliverable_lines:
        details.append("deliverables:")
        details.extend(deliverable_lines)
    return "\n".join(details)


def _format_activity(activity: dict[str, Any]) -> str:
    if not activity:
        return "none"
    title = activity.get("title") or activity.get("kind", "activity")
    detail = activity.get("detail") or "No detail"
    status = activity.get("status", "unknown")
    destination = activity.get("destination")
    preferred_destination = ((activity.get("metadata") or {}).get("preferred_destination"))
    lines = [
        f"kind: {activity.get('kind', 'unknown')}",
        f"status: {status}",
        f"title: {title}",
        f"detail: {detail}",
    ]
    if destination:
        lines.append(f"destination: {destination}")
    if preferred_destination:
        lines.append(f"preferred_destination: {preferred_destination}")
    return "\n".join(lines)


def _format_pending_tasks(agent_id: str, current_task: dict[str, Any] | None) -> str:
    open_tasks = _list_open_tasks(agent_id, current_task)
    if not open_tasks:
        return "datetime | status | task name | description\nnone"

    lines = ["datetime | status | task name | description"]
    for task in open_tasks[-5:]:
        lines.append(
            " | ".join(
                [
                    _format_datetime(task.created_at),
                    task.status,
                    task.title,
                    _summarize_text(task.description or task.status_note or ""),
                ]
            )
        )
    return "\n".join(lines)


def _format_references(
    agent_id: str,
    reference_materials: list[str],
    prompt_notifications: list[Notification],
) -> str:
    sections = [
        _format_team_directory(reference_materials),
        _format_recent_completed_tasks(agent_id),
        _format_recent_work_artifacts(agent_id),
        _format_recent_runtime_notifications(prompt_notifications),
    ]
    return "\n\n".join(section for section in sections if section)


def _list_open_tasks(agent_id: str, current_task: dict[str, Any] | None) -> list[Any]:
    """Return pending/accepted tasks excluding the currently active one."""
    current_task_id = current_task.get("id") if current_task else None
    tasks = db.list_tasks(assigned_to=agent_id, status="pending") + db.list_tasks(assigned_to=agent_id, status="accepted")
    return [task for task in tasks if task.id != current_task_id]


def _format_team_directory(reference_materials: list[str]) -> str:
    """Render the teammate directory section."""
    lines = ["TEAM DIRECTORY:"]
    if not reference_materials:
        lines.append("none")
        return "\n".join(lines)
    lines.extend(reference_materials)
    return "\n".join(lines)


def _format_recent_completed_tasks(agent_id: str) -> str:
    """Render recent completed task history."""
    limit = config.get_int("context_recent_completed_tasks") or 3
    rows = db.get_recent_completed_tasks(agent_id, limit=limit)
    lines = ["RECENT COMPLETED TASKS:", "datetime | status | task name | summary"]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for task in rows:
        summary = task.get("completion_summary") or task.get("status_note") or ""
        lines.append(
            " | ".join(
                [
                    _format_datetime(task.get("last_activity") or task.get("created_at")),
                    str(task.get("status") or "unknown"),
                    str(task.get("title") or "Untitled"),
                    _summarize_text(summary),
                ]
            )
        )
    return "\n".join(lines)


def _format_recent_work_artifacts(agent_id: str) -> str:
    """Render recent work artifacts as historical context."""
    limit = config.get_int("context_recent_work_artifacts") or 5
    rows = db.get_recent_work_artifacts(agent_id, limit=limit)
    lines = ["RECENT WORK ARTIFACTS:", "datetime | type | summary"]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for artifact in rows:
        lines.append(
            " | ".join(
                [
                    _format_datetime(artifact.created_at),
                    artifact.message_type,
                    _summarize_text((artifact.content or "").strip().replace("\n", " ")),
                ]
            )
        )
    return "\n".join(lines)


def _format_recent_runtime_notifications(rows: list[Notification]) -> str:
    """Render recent prompt-visible runtime notifications."""
    lines = ["RECENT RUNTIME NOTIFICATIONS:"]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for item in rows:
        lines.append(f"- {item.kind}: {item.content}")
    return "\n".join(lines)


def _format_datetime(value: Any) -> str:
    """Format datetimes consistently for prompt tables."""
    if value is None:
        return "unknown"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _summarize_text(value: str, limit: int = 160) -> str:
    """Keep prompt rows compact and readable."""
    text = (value or "").strip()
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_trigger(trigger: dict[str, Any], contract_kind: str) -> str:
    """Format the trigger event for the turn contract in use."""
    trigger_type = trigger.get("type", "unknown")

    if trigger_type in ("message", "human_chat", "peer_message"):
        sender = trigger.get("from_name", "Someone")
        content = trigger.get("content", "")
        return f"CURRENT REQUEST FROM [{sender}]: {content}"

    if trigger_type == "task_assigned":
        title = trigger.get("task_title", "a task")
        desc = trigger.get("task_description", "")
        extra = f"\nTask description: {desc}" if desc else ""
        if contract_kind == "decision":
            return f'You have been offered a new task assignment: "{title}". Decide whether to accept it now, defer it, or decline it.{extra}'
        return f'You have an accepted task commitment: "{title}".{extra}'

    if trigger_type == "activity_resumed":
        content = trigger.get("content", "")
        if content:
            return content
        kind = trigger.get("activity_kind", "activity")
        return f"You should continue the current {kind}."

    if trigger_type == "social":
        nearby = trigger.get("nearby_names", [])
        return f"You're idle and nearby: {', '.join(nearby)}. Consider a brief social interaction."

    if trigger_type == "watchdog_status_ping":
        title = trigger.get("task_title", "your current task")
        return (
            f"Watchdog status check: you have been quiet on \"{title}\". "
            "Reply to the human operator with a status update, continue working, or sign off with complete/blocked/delegated/abandoned."
        )

    return "You have been activated."


def _render_turn_contract(contract_kind: str) -> str:
    """Render the code-owned contract for this turn type."""
    if contract_kind == "decision":
        return render_decision_contract()
    return render_action_contract()
