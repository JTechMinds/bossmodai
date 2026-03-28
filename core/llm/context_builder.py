"""BossMod AI — LLM context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

import db
from core import config
from core.agent_loop.deliverables import format_deliverables_for_context, get_work_contract
from core.models import Agent, AgentState
from core.models.notification import Notification
from core.llm.template_engine import render_template, syntax_guide
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)



_STATUS_LABELS = {
    "idle": "idle",
    "work_active": "working",
    "social_active": "socializing",
    "in_transit": "walking",
}

_AUTHORED_PROMPT_VARIABLES: list[tuple[str, str]] = [
    ("agent_name", "Agent display name"),
    ("role", "Agent role title"),
    ("personality", "Rendered personality prompt text"),
    ("worldStatus", "Formatted world status block"),
    ("worldStatus.location", "Current room/location label"),
    ("worldStatus.status", "Current agent status label"),
    ("worldStatus.nearby_agents", "Comma-separated nearby agent names"),
    ("worldStatus.pending_triggers", "Queued trigger count"),
    ("worldStatus.open_task_count", "Open task count"),
    ("worldStatus.current_activity", "Current activity summary"),
    ("worldStatus.current_task", "Current task summary"),
    ("activity", "Formatted current activity block"),
    ("activity.kind", "Current activity kind"),
    ("activity.status", "Current activity status"),
    ("activity.title", "Current activity title"),
    ("activity.detail", "Current activity detail"),
    ("activity.destination", "Current activity destination"),
    ("activity.preferred_destination", "Preferred destination when present"),
    ("task", "Formatted current task block"),
    ("task.id", "Current task id"),
    ("task.title", "Current task title"),
    ("task.status", "Current task status"),
    ("task.description", "Current task description"),
    ("task.project", "Current task project"),
    ("task.completion_summary", "Current task completion summary"),
    ("task.status_note", "Current task status note"),
    ("pending_tasks", "Formatted open task list"),
    ("references", "Formatted reference block"),
    ("turn.contract_kind", "Current runtime contract kind"),
    ("trigger.type", "Current trigger type"),
    ("trigger.from_name", "Trigger speaker/sender name"),
    ("trigger.content", "Trigger message content"),
    ("trigger.source_channel", "Trigger source channel"),
    ("trigger.task_title", "Assigned task title when present"),
    ("trigger.task_description", "Assigned task description when present"),
    ("channel.kind", "Current channel kind when present"),
    ("channel.name", "Current channel name when present"),
    ("channel.participant_count", "Current channel participant count"),
    ("session.kind", "Current session kind when present"),
    ("session.name", "Current session name when present"),
    ("session.participant_count", "Current session participant count"),
]
AUTHORED_PROMPT_ALLOWED_PATHS = {name for name, _ in _AUTHORED_PROMPT_VARIABLES}


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
    current_session: dict[str, Any] | None = None
    current_channel: dict[str, Any] | None = None
    nearby_agents: list[dict[str, Any]] | None = None
    pending_trigger_count: int = 0
    contract_kind: str = "execution"


def build_context(turn: TurnContext) -> list[dict[str, str]]:
    """Assemble the full message list for an agent turn."""
    messages: list[dict[str, str]] = []
    render_context = _build_prompt_render_context(turn)
    personality_template = turn.agent.prompt_template or _default_role_prompt(turn.agent)
    rendered_personality = render_template(
        personality_template,
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )
    render_context["personality"] = rendered_personality

    system_prompt = render_template(
        config.require("system_prompt_template"),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )

    messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "system",
            "content": _render_turn_contract(turn.contract_kind, render_context),
        }
    )
    file_guidance = _render_file_deliverable_guidance(turn)
    if file_guidance:
        messages.append({"role": "system", "content": file_guidance})
    if turn.contract_kind == "decision":
        messages.append(
            {
                "role": "system",
                "content": _render_conversation_envelope(turn),
            }
        )

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


def template_variable_metadata() -> list[dict[str, str]]:
    """Return the supported authored prompt variables for UI metadata."""
    return [{"name": name, "description": description} for name, description in _AUTHORED_PROMPT_VARIABLES]


def template_syntax_examples() -> list[str]:
    """Return supported authored prompt syntax examples for UI metadata."""
    return syntax_guide()


def _build_prompt_render_context(turn: TurnContext) -> dict[str, Any]:
    """Build the structured render context for authored prompts."""
    world_status = _world_status_context(
        turn.agent,
        turn.state,
        turn.nearby_agents,
        turn.current_activity,
        turn.current_task,
        turn.pending_trigger_count,
    )
    activity = _activity_context(turn.current_activity)
    task = _task_context(turn.current_task)
    return {
        "agent_name": turn.agent.name,
        "role": turn.agent.role or "AI Assistant",
        "personality": "",
        "worldStatus": world_status,
        "activity": activity,
        "task": task,
        "pending_tasks": _format_pending_tasks(turn.agent.id, turn.current_task),
        "references": _format_references(
            turn.agent.id,
            turn.reference_materials,
            turn.prompt_notifications,
            turn.current_session,
            turn.current_channel,
        ),
        "turn": {
            "contract_kind": turn.contract_kind,
        },
        "trigger": _template_trigger(turn.trigger),
        "channel": _template_channel(turn.current_channel),
        "session": _template_session(turn.current_session),
    }


def _world_status_context(
    agent: Agent,
    state: AgentState,
    nearby_agents: list[dict[str, Any]] | None = None,
    current_activity: dict[str, Any] | None = None,
    current_task: dict[str, Any] | None = None,
    pending_trigger_count: int = 0,
) -> dict[str, Any]:
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)
    open_tasks = _list_open_tasks(agent.id, current_task)
    nearby_names = [str(item.get("name") or "Unknown") for item in (nearby_agents or [])]
    task_summary = "none"
    if current_task:
        task_summary = f"{current_task.get('title', 'Untitled')} ({current_task.get('status', 'unknown')})"
    activity_summary = "none"
    if current_activity:
        activity_summary = current_activity.get("kind", "unknown")
        if current_activity.get("title"):
            activity_summary += f' - {current_activity["title"]}'

    return {
        "value": _format_world_status(
            agent,
            state,
            nearby_agents,
            current_activity,
            current_task,
            pending_trigger_count,
        ),
        "location": room_name,
        "status": status_label,
        "nearby_agents": ", ".join(nearby_names) if nearby_names else "none",
        "pending_triggers": pending_trigger_count,
        "open_task_count": len(open_tasks),
        "current_activity": activity_summary,
        "current_task": task_summary,
    }


def _activity_context(activity: dict[str, Any] | None) -> dict[str, Any]:
    metadata = activity.get("metadata") if activity else {}
    return {
        "value": _format_activity(activity),
        "kind": str((activity or {}).get("kind") or ""),
        "status": str((activity or {}).get("status") or ""),
        "title": str((activity or {}).get("title") or ""),
        "detail": str((activity or {}).get("detail") or ""),
        "destination": str((activity or {}).get("destination") or ""),
        "preferred_destination": str((metadata or {}).get("preferred_destination") or ""),
    }


def _task_context(task: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "value": _format_task(task),
        "id": str((task or {}).get("id") or ""),
        "title": str((task or {}).get("title") or ""),
        "status": str((task or {}).get("status") or ""),
        "description": str((task or {}).get("description") or ""),
        "project": str((task or {}).get("project") or ""),
        "completion_summary": str((task or {}).get("completion_summary") or ""),
        "status_note": str((task or {}).get("status_note") or ""),
    }


def _template_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": str(trigger.get("type") or ""),
        "from_name": str(trigger.get("from_name") or ""),
        "content": str(trigger.get("content") or ""),
        "source_channel": str(trigger.get("source_channel") or ""),
        "task_title": str(trigger.get("task_title") or ""),
        "task_description": str(trigger.get("task_description") or ""),
    }


def _template_channel(channel: dict[str, Any] | None) -> dict[str, Any]:
    participants = (channel or {}).get("participants") or []
    return {
        "kind": str((channel or {}).get("kind") or ""),
        "name": str((channel or {}).get("name") or ""),
        "participant_count": len(participants),
    }


def _template_session(session: dict[str, Any] | None) -> dict[str, Any]:
    participants = (session or {}).get("participants") or []
    return {
        "kind": "meeting" if session else "",
        "name": str((session or {}).get("title") or (session or {}).get("room_name") or ""),
        "participant_count": len(participants),
    }


def _preview_trigger(trigger_type: str) -> dict[str, Any]:
    base = {
        "type": trigger_type,
        "from_name": "Human Operator",
        "content": "Can you give me a quick status update?",
        "source_channel": "chat",
        "task_title": "Write API summary",
        "task_description": "Summarize the current API behavior and save the draft.",
    }
    if trigger_type == "peer_message":
        base.update({"from_name": "Morgan", "content": "Can you take a look at the deployment notes?"})
    if trigger_type in {"session_message", "session_response"}:
        base.update({"from_name": "Meeting Room", "content": "Please share your progress."})
    if trigger_type in {"channel_message", "channel_response"}:
        base.update({"from_name": "Planning Channel", "content": "Who can summarize next steps?"})
    if trigger_type == "task_assigned":
        base.update({"content": "", "from_name": "Human Operator"})
    if trigger_type == "activity_resumed":
        base.update({"content": "Continue the current work activity."})
    if trigger_type == "watchdog_status_ping":
        base.update({"content": "Provide a status update on the current task."})
    if trigger_type == "social":
        base.update({"content": "", "from_name": "Nearby Team"})
    return base


def _preview_activity(contract_kind: str, trigger_type: str) -> dict[str, Any] | None:
    if contract_kind != "execution":
        return None
    kind = "meeting" if trigger_type in {"session_message", "session_response", "channel_message", "channel_response"} else "work"
    title = "Draft API summary" if kind == "work" else "Planning sync"
    detail = "Continue the current activity with the team context already in progress."
    return {
        "id": "preview-activity",
        "kind": kind,
        "status": "active",
        "title": title,
        "detail": detail,
        "destination": "meetingRoom" if kind == "meeting" else "",
        "metadata": {},
    }


def _preview_task(contract_kind: str, trigger_type: str) -> dict[str, Any] | None:
    if contract_kind != "execution" and trigger_type != "task_assigned":
        return None
    return {
        "id": "preview-task",
        "title": "Write API summary",
        "description": "Summarize the current API behavior and save the result to /me/api_summary.md",
        "status": "active" if contract_kind == "execution" else "pending",
        "project": "BossMod AI",
        "completion_summary": "",
        "status_note": "",
        "work_contract": {
            "deliverables": [{"type": "file", "path": "/me/api_summary.md", "description": None}],
        },
    }


def _preview_session(trigger_type: str) -> dict[str, Any] | None:
    if trigger_type not in {"session_message", "session_response"}:
        return None
    return {
        "title": "Planning Sync",
        "room_name": "Meeting Room",
        "participants": [{"name": "Taylor"}, {"name": "Morgan"}, {"name": "Riley"}],
    }


def _preview_channel(trigger_type: str) -> dict[str, Any] | None:
    if trigger_type not in {"channel_message", "channel_response"}:
        return None
    return {
        "kind": "channel",
        "name": "Planning",
        "participants": [{"name": "Taylor"}, {"name": "Morgan"}, {"name": "Riley"}],
    }


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
    current_session: dict[str, Any] | None,
    current_channel: dict[str, Any] | None,
) -> str:
    sections = [
        _format_current_session(current_session),
        _format_current_channel(current_channel),
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


def _format_current_session(session: dict[str, Any] | None) -> str:
    """Render the active meeting session summary when relevant."""
    if not session:
        return ""
    lines = [
        "CURRENT MEETING SESSION:",
        f"title: {session.get('title') or 'Meeting'}",
        f"room: {session.get('room_name') or session.get('room_id') or 'meeting'}",
    ]
    participants = session.get("participants") or []
    if participants:
        names = [str(item.get("name") or "Unknown") for item in participants]
        lines.append(f"participants: {', '.join(names)}")
    else:
        lines.append("participants: none")
    return "\n".join(lines)


def _format_current_channel(channel: dict[str, Any] | None) -> str:
    """Render the active shared channel summary when relevant."""
    if not channel:
        return ""
    lines = [
        "CURRENT SHARED CHANNEL:",
        f"name: {channel.get('name') or 'Channel'}",
        f"kind: {channel.get('kind') or 'manual'}",
    ]
    participants = channel.get("participants") or []
    if participants:
        names = [str(item.get("name") or "Unknown") for item in participants]
        lines.append(f"participants: {', '.join(names)}")
    else:
        lines.append("participants: none")
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

    if trigger_type in ("message", "human_chat", "peer_message", "session_message", "session_response", "channel_message", "channel_response"):
        sender = trigger.get("from_name", "Someone")
        content = trigger.get("content", "")
        if trigger_type == "channel_message":
            return f"CURRENT SHARED CHANNEL MESSAGE FROM [{sender}]: {content}"
        if trigger_type == "channel_response":
            return f"YOUR TURN TO RESPOND IN THE SHARED CHANNEL after [{sender}] said: {content}"
        if trigger_type == "session_message":
            return f"CURRENT MEETING MESSAGE FROM [{sender}]: {content}"
        if trigger_type == "session_response":
            return f"YOUR TURN TO RESPOND IN THE MEETING after [{sender}] said: {content}"
        return f"CURRENT REQUEST FROM [{sender}]: {content}"

    if trigger_type == "task_assigned":
        title = trigger.get("task_title", "a task")
        desc = trigger.get("task_description", "")
        sender = trigger.get("from_name", "someone")
        extra = f"\nTask description: {desc}" if desc else ""
        if contract_kind == "decision":
            return f'[{sender}] assigned you a task: "{title}". Decide whether to accept it, ask a clarifying question, defer it, or decline it.{extra}'
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


def _render_turn_contract(contract_kind: str, render_context: dict[str, Any]) -> str:
    """Render the current settings-backed runtime contract for one turn."""
    setting_key = "runtime_contract_decision" if contract_kind == "decision" else "runtime_contract_execution"
    template = config.require(setting_key)
    return render_template(template, render_context, allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS)


def preview_runtime_contract(contract_kind: str, trigger_type: str, template_override: str | None = None) -> str:
    """Render one runtime contract against a representative preview context."""
    now = datetime.now(timezone.utc)
    agent = Agent(
        id="preview-agent",
        storage_key="preview-agent",
        name="Taylor",
        role="Operations Analyst",
        prompt_template="You are {{agent_name}}, keep answers concise and operational.",
        created_at=now,
    )
    state = AgentState(
        agent_id=agent.id,
        x=14,
        y=9,
        status="work_active" if contract_kind == "execution" else "idle",
        last_active_at=now,
        idle_since=now,
    )
    turn = TurnContext(
        agent=agent,
        state=state,
        trigger=_preview_trigger(trigger_type),
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=["RECENT COMPLETED TASKS:\n- API summary completed"],
        current_activity=_preview_activity(contract_kind, trigger_type),
        current_task=_preview_task(contract_kind, trigger_type),
        current_session=_preview_session(trigger_type),
        current_channel=_preview_channel(trigger_type),
        nearby_agents=[{"name": "Morgan"}, {"name": "Riley"}],
        pending_trigger_count=1,
        contract_kind=contract_kind,
    )
    render_context = _build_prompt_render_context(turn)
    render_context["personality"] = render_template(
        agent.prompt_template or _default_role_prompt(agent),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )
    if template_override is not None:
        return render_template(template_override, render_context, allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS)
    return _render_turn_contract(contract_kind, render_context)


def _render_conversation_envelope(turn: TurnContext) -> str:
    """Render runtime-owned speaker, audience, and channel facts for conversation turns."""
    trigger = turn.trigger
    trigger_type = str(trigger.get("type") or "unknown")
    speaker_type, speaker_name, speaker_id = _conversation_speaker(trigger)
    channel_kind, channel_name, participant_names = _conversation_channel(turn, trigger_type)
    audience_mode, target_names = _conversation_audience(turn, trigger_type)
    turn_purpose = _conversation_turn_purpose(trigger_type)

    lines = [
        "CONVERSATION ENVELOPE:",
        f"current_agent: {turn.agent.name}",
        f"speaker: {speaker_name} ({speaker_type})",
        f"speaker_id: {speaker_id}",
        f"channel_kind: {channel_kind}",
        f"channel_name: {channel_name}",
        f"turn_purpose: {turn_purpose}",
        f"audience_mode: {audience_mode}",
    ]
    if target_names:
        lines.append(f"audience_targets: {', '.join(target_names)}")
    else:
        lines.append("audience_targets: none")
    if participant_names:
        lines.append(f"participants: {', '.join(participant_names)}")
    else:
        lines.append("participants: none")
    lines.extend(
        [
            "Use this envelope to understand who is speaking, who else is present, and whether this is direct or shared conversation.",
            "Do not restate these runtime facts unless they matter to your actual reply.",
        ]
    )
    return "\n".join(lines)


def _render_file_deliverable_guidance(turn: TurnContext) -> str | None:
    """Return runtime-owned file-writing guidance for contract-bound work."""
    if turn.contract_kind != "execution" or not turn.current_task:
        return None
    contract = get_work_contract(turn.current_task)
    file_paths = [item.path for item in contract.deliverables if item.type == "file" and item.path]
    if not file_paths:
        return None
    lines = [
        "FILE DELIVERABLE GUIDANCE:",
        f"required_files: {', '.join(file_paths)}",
        "If the current work contract requires a file, prefer BossMod CLI write directly instead of putting the full document into data.out.",
        "For one substantial document, call write <path> with no body to use runtime-managed authoring.",
        "For multiple generated files, call batch-write with a short manifest body listing each path and goal.",
        "Do not put long-form document bodies into CLI JSON.",
        "Use work.out for short progress/status text, not the final long-form file body.",
    ]
    return "\n".join(lines)


def _conversation_speaker(trigger: dict[str, Any]) -> tuple[str, str, str]:
    """Return speaker metadata for one conversation trigger."""
    trigger_type = str(trigger.get("type") or "")
    if trigger_type == "human_chat":
        return "human", "Human Operator", "human"
    if trigger_type == "task_assigned":
        if trigger.get("from_agent"):
            return "agent", str(trigger.get("from_name") or "Coworker"), str(trigger.get("from_agent"))
        return "human", str(trigger.get("from_name") or "Human Operator"), "human"
    if trigger.get("author_type") == "human":
        return "human", str(trigger.get("from_name") or "Human Operator"), "human"
    if trigger.get("from_agent"):
        return "agent", str(trigger.get("from_name") or "Coworker"), str(trigger.get("from_agent"))
    return "runtime", str(trigger.get("from_name") or "System"), "runtime"


def _conversation_channel(turn: TurnContext, trigger_type: str) -> tuple[str, str, list[str]]:
    """Return channel metadata for one conversation turn."""
    if trigger_type in {"channel_message", "channel_response"} and turn.current_channel:
        participants = [str(item.get("name") or "Unknown") for item in (turn.current_channel.get("participants") or [])]
        return (
            str(turn.current_channel.get("kind") or "channel"),
            str(turn.current_channel.get("name") or "Channel"),
            participants,
        )
    if trigger_type in {"session_message", "session_response"} and turn.current_session:
        participants = [str(item.get("name") or "Unknown") for item in (turn.current_session.get("participants") or [])]
        return (
            "meeting",
            str(turn.current_session.get("title") or turn.current_session.get("room_name") or "Meeting"),
            participants,
        )
    if trigger_type == "peer_message":
        peer_name = str(turn.trigger.get("from_name") or "Coworker")
        return "direct", f"{peer_name} DM", [turn.agent.name, peer_name]
    if trigger_type == "task_assigned":
        assigner = str(turn.trigger.get("from_name") or "Assigning Party")
        return "assignment", "Task Assignment", [turn.agent.name, assigner]
    return "direct", "Direct Chat", [turn.agent.name, "Human Operator"]


def _conversation_audience(turn: TurnContext, trigger_type: str) -> tuple[str, list[str]]:
    """Return audience intent facts for one conversation turn."""
    if trigger_type in {"channel_message", "channel_response"}:
        participants = [str(item.get("name") or "Unknown") for item in ((turn.current_channel or {}).get("participants") or [])]
        return "group", participants
    if trigger_type in {"session_message", "session_response"}:
        participants = [str(item.get("name") or "Unknown") for item in ((turn.current_session or {}).get("participants") or [])]
        return "group", participants
    if trigger_type == "peer_message":
        return "direct", [str(turn.trigger.get("from_name") or "Coworker")]
    if trigger_type == "task_assigned":
        return "direct", [str(turn.trigger.get("from_name") or "Assigning Party")]
    return "direct", ["Human Operator"]


def _conversation_turn_purpose(trigger_type: str) -> str:
    """Return a short runtime-owned purpose label for one conversation turn."""
    if trigger_type in {"channel_message", "session_message"}:
        return "shared_intake"
    if trigger_type in {"channel_response", "session_response"}:
        return "shared_reply"
    if trigger_type == "task_assigned":
        return "assignment_review"
    return "direct_reply"
