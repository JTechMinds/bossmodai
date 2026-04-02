"""BossMod AI — LLM context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any

import db
from core import config
from core.agent_loop.communication import communication_profile_for_trigger
from core.agent_loop.deliverables import format_deliverables_for_context, get_work_contract
from core.bm_cli.filesystem import slugify_name
from core.default_prompts import load_default_role_prompt
from core.models import Agent, AgentState
from core.models.notification import Notification
from core.llm.template_engine import render_template, syntax_guide
from core.prompting.runtime_prompt_registry import resolve_runtime_prompt_text
from core.tasking import build_task_board
from core.time import ensure_utc, now_local
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)



_STATUS_LABELS = {
    "idle": "idle",
    "waiting": "waiting",
    "blocked": "blocked",
    "work_active": "working",
    "social_active": "socializing",
    "in_transit": "walking",
}

_AUTHORED_PROMPT_VARIABLES: list[tuple[str, str]] = [
    ("agent_name", "Agent display name"),
    ("role", "Agent role title"),
    ("personality", "Rendered personality prompt text"),
    ("current_date_time", "Current local date/time string for this turn"),
    ("current_time.iso_local", "Current local time in ISO-8601 format"),
    ("current_time.iso_utc", "Current UTC time in ISO-8601 format"),
    ("current_time.date", "Current local calendar date"),
    ("current_time.time", "Current local clock time"),
    ("current_time.day_name", "Current local day name"),
    ("current_time.timezone", "Current local timezone label"),
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
    ("task_board", "Formatted task board summary for open, waiting, owned, and delegated work"),
    ("task_board.open_count", "Count of other open tasks on the personal board"),
    ("task_board.waiting_count", "Count of owned/delegated tasks waiting on this agent"),
    ("task_board.delegated_count", "Count of delegated child tasks this agent owns"),
    ("task_board.blocked_count", "Count of blocked or stalled tasks visible on the board"),
    ("pending_tasks", "Legacy alias for the old flat open-task list; prefer task_board in new prompts"),
    ("references", "Formatted reference block"),
    ("turn.contract_kind", "Current runtime contract kind"),
    ("trigger.type", "Current trigger type"),
    ("trigger.from_name", "Trigger speaker/sender name"),
    ("trigger.content", "Trigger message content"),
    ("trigger.source_channel", "Trigger source channel"),
    ("trigger.task_title", "Assigned task title when present"),
    ("trigger.task_description", "Assigned task description when present"),
    ("trigger.task_status", "Task status for task-bound follow-up turns"),
    ("trigger.task_party", "Recipient role for task-bound follow-up turns"),
    ("trigger.attention_kind", "Why this task-thread turn needs a response"),
    ("trigger.activity_kind", "Activity kind for resumed work triggers"),
    ("trigger.nearby_names", "Comma-separated nearby agent names for social triggers"),
    ("channel.kind", "Current channel kind when present"),
    ("channel.name", "Current channel name when present"),
    ("channel.participant_count", "Current channel participant count"),
    ("session.kind", "Current session kind when present"),
    ("session.name", "Current session name when present"),
    ("session.participant_count", "Current session participant count"),
    ("cli.shell_enabled", "Whether BossMod CLI shell access is enabled"),
    ("cli.cwd", "Current BossMod CLI working directory"),
    ("workspace.personal_root", "Default personal workspace root"),
    ("workspace.projects_root", "Default shared projects workspace root"),
    ("workspace.default_save_root", "Preferred default save root for new files in this turn"),
    ("workspace.project_root", "Relevant shared project folder when present"),
    ("conversation.speaker_name", "Conversation speaker display name"),
    ("conversation.speaker_type", "Conversation speaker type"),
    ("conversation.speaker_id", "Conversation speaker runtime id"),
    ("conversation.channel_kind", "Conversation channel kind"),
    ("conversation.channel_name", "Conversation channel name"),
    ("conversation.turn_purpose", "Conversation turn purpose label"),
    ("conversation.audience_mode", "Conversation audience mode"),
    ("conversation.audience_targets", "Conversation audience targets as text"),
    ("conversation.participants", "Conversation participants as text"),
    ("file_guidance.required_files", "Comma-separated required file paths for the current work contract"),
    ("file_guidance.required_file_count", "Number of required file deliverables for the current work contract"),
    ("communication_snapshot.json", "Serialized authoritative communication snapshot JSON"),
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
    communication_snapshot_json: str | None = None


def build_context(
    turn: TurnContext,
    template_overrides: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Assemble the full message list for an agent turn."""
    messages: list[dict[str, str]] = []
    render_context = _build_prompt_render_context(turn)
    system_prompt = _render_system_prompt(turn, render_context, template_overrides)

    messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "system",
            "content": _render_turn_contract(turn.contract_kind, render_context, template_overrides),
        }
    )
    file_guidance = _render_file_deliverable_guidance(turn, template_overrides)
    if file_guidance:
        messages.append({"role": "system", "content": file_guidance})
    if turn.contract_kind == "decision":
        messages.append(
            {
                "role": "system",
                "content": _render_conversation_envelope(turn, template_overrides),
            }
        )
        communication_snapshot = _render_communication_snapshot(turn, template_overrides)
        if communication_snapshot:
            messages.append({"role": "system", "content": communication_snapshot})

    for msg in turn.conversation_history:
        role = "assistant" if msg.get("from_agent") == turn.agent.id else "user"
        sender = msg.get("from_name", "Unknown")
        content = msg.get("content", "")

        if role == "user":
            messages.append({"role": "user", "content": f"[{sender}]: {content}"})
        else:
            messages.append({"role": "assistant", "content": content})

    # ─── Trigger event ───
    messages.append({"role": "user", "content": _format_trigger(turn.trigger, turn.contract_kind, template_overrides)})

    return messages


def _default_role_prompt(agent: Agent) -> str:
    """Generate a default system prompt for agents without a custom template."""
    return load_default_role_prompt()


def _render_system_prompt(
    turn: TurnContext,
    render_context: dict[str, Any],
    template_overrides: dict[str, str] | None = None,
) -> str:
    """Render the base authored system prompt once for any turn flavor."""
    personality_template = turn.agent.prompt_template or _default_role_prompt(turn.agent)
    rendered_personality = render_template(
        personality_template,
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )
    render_context["personality"] = rendered_personality
    return render_template(
        resolve_runtime_prompt_text("system_prompt_template", template_overrides),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def template_variable_metadata() -> list[dict[str, str]]:
    """Return the supported authored prompt variables for UI metadata."""
    return [{"name": name, "description": description} for name, description in _AUTHORED_PROMPT_VARIABLES]


def template_syntax_examples() -> list[str]:
    """Return supported authored prompt syntax examples for UI metadata."""
    return syntax_guide()


def _build_prompt_render_context(turn: TurnContext) -> dict[str, Any]:
    """Build the structured render context for authored prompts."""
    current_time = _current_time_context(now_local())
    task_board = _task_board_context(turn.agent.id, turn.current_task)
    world_status = _world_status_context(
        turn.agent,
        turn.state,
        turn.nearby_agents,
        turn.current_activity,
        turn.current_task,
        turn.pending_trigger_count,
        open_task_count=task_board["open_count"],
    )
    activity = _activity_context(turn.current_activity)
    task = _task_context(turn.current_task)
    cli_cwd = _current_cli_cwd(turn.agent.id)
    workspace = _workspace_context(cli_cwd, turn.current_task)
    return {
        "agent_name": turn.agent.name,
        "role": turn.agent.role or "AI Assistant",
        "personality": "",
        "current_date_time": current_time["value"],
        "current_time": current_time,
        "worldStatus": world_status,
        "activity": activity,
        "task": task,
        "task_board": task_board,
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
        "cli": {
            "shell_enabled": config.get("cli_shell_enabled") == "true",
            "cwd": cli_cwd,
        },
        "workspace": workspace,
    }


def _current_time_context(now: datetime) -> dict[str, str]:
    """Return one stable per-turn local time context for authored prompts."""
    local_now = now.astimezone(now.tzinfo or timezone.utc)
    timezone_name = local_now.tzname() or "local"
    return {
        "value": f'{local_now.strftime("%Y-%m-%d %H:%M:%S")} {timezone_name}',
        "iso_local": local_now.isoformat(),
        "iso_utc": ensure_utc(local_now).isoformat(),
        "date": local_now.date().isoformat(),
        "time": local_now.strftime("%H:%M:%S"),
        "day_name": local_now.strftime("%A"),
        "timezone": timezone_name,
    }


def _current_cli_cwd(agent_id: str) -> str:
    """Return the current BossMod CLI working directory for one agent."""
    cli_state = db.get_agent_cli_state(agent_id)
    return cli_state.cwd if cli_state is not None else "/me"


def _workspace_context(cli_cwd: str, task: dict[str, Any] | None) -> dict[str, str]:
    """Return compact workspace defaults for prompt rendering."""
    project_root = _workspace_project_root(cli_cwd, task)
    default_save_root = cli_cwd if project_root and cli_cwd.startswith(project_root) else (project_root or "/me")
    return {
        "personal_root": "/me",
        "projects_root": "/projects",
        "default_save_root": default_save_root,
        "project_root": project_root,
    }


def _workspace_project_root(cli_cwd: str, task: dict[str, Any] | None) -> str:
    """Infer the relevant shared project root from cwd or current task."""
    normalized_cwd = str(cli_cwd or "").strip() or "/me"
    if normalized_cwd.startswith("/projects/"):
        parts = [part for part in normalized_cwd.split("/") if part]
        if len(parts) >= 2:
            return f"/projects/{parts[1]}"
    project_name = str((task or {}).get("project") or "").strip()
    if project_name:
        return f"/projects/{slugify_name(project_name)}"
    return ""


def _world_status_context(
    agent: Agent,
    state: AgentState,
    nearby_agents: list[dict[str, Any]] | None = None,
    current_activity: dict[str, Any] | None = None,
    current_task: dict[str, Any] | None = None,
    pending_trigger_count: int = 0,
    *,
    open_task_count: int | None = None,
) -> dict[str, Any]:
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)
    open_count = open_task_count if open_task_count is not None else len(_list_open_tasks(agent.id, current_task))
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
            open_task_count=open_count,
        ),
        "location": room_name,
        "status": status_label,
        "nearby_agents": ", ".join(nearby_names) if nearby_names else "none",
        "pending_triggers": pending_trigger_count,
        "open_task_count": open_count,
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
        "task_status": str(trigger.get("task_status") or ""),
        "task_party": str(trigger.get("task_party") or ""),
        "attention_kind": str(trigger.get("attention_kind") or ""),
        "activity_kind": str(trigger.get("activity_kind") or ""),
        "nearby_names": ", ".join(str(item or "") for item in (trigger.get("nearby_names") or [] if isinstance(trigger.get("nearby_names"), list) else [])),
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
        base.update({"from_name": "Morgan", "from_agent": "agent-morgan", "content": "Can you take a look at the deployment notes?"})
    if trigger_type == "task_follow_up":
        base.update(
            {
                "from_name": "Morgan",
                "from_agent": "agent-morgan",
                "content": "I finished the draft and need your review.",
                "task_status": "accepted",
                "task_party": "stakeholder",
                "attention_kind": "review_request",
            }
        )
    if trigger_type in {"session_message", "session_response"}:
        base.update({"from_name": "Meeting Room", "content": "Please share your progress."})
    if trigger_type in {"channel_message", "channel_response"}:
        base.update({"from_name": "Planning Channel", "content": "Who can summarize next steps?"})
    if trigger_type == "task_assigned":
        base.update({"content": "", "from_name": "Human Operator"})
    if trigger_type == "activity_resumed":
        base.update({"content": "Continue the current work activity.", "activity_kind": "work"})
    if trigger_type == "watchdog_status_ping":
        base.update({"content": "Provide a status update on the current task.", "task_title": "Write API summary"})
    if trigger_type == "social":
        base.update({"content": "", "from_name": "Nearby Team", "nearby_names": ["Morgan", "Riley"]})
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
    if contract_kind != "execution" and trigger_type not in {"task_assigned", "task_follow_up"}:
        return None
    return {
        "id": "preview-task",
        "title": "Write API summary",
        "description": "Summarize the current API behavior and save the result to /me/api_summary.md",
        "status": "active" if contract_kind == "execution" else ("accepted" if trigger_type == "task_follow_up" else "pending"),
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
    *,
    open_task_count: int | None = None,
) -> str:
    """Build the structured world status block."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)

    pending_count = pending_trigger_count
    open_count = open_task_count if open_task_count is not None else len(_list_open_tasks(agent.id, current_task))

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
        f"open_task_count: {open_count}\n"
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


def _task_board_context(agent_id: str, current_task: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact board-first task summary for authored prompts."""
    self_board = build_task_board(agent_id, scope="self")
    owned_board = build_task_board(agent_id, scope="owned")
    current_task_id = str((current_task or {}).get("id") or "").strip() or None

    open_rows = _tasks_from_board_section(self_board, "my_open_tasks", current_task_id=current_task_id)
    waiting_self_rows = _tasks_from_board_section(self_board, "my_waiting_tasks", current_task_id=current_task_id)
    blocked_rows = _tasks_from_board_section(self_board, "my_blocked_tasks", current_task_id=current_task_id)
    waiting_rows = _tasks_from_board_section(owned_board, "tasks_waiting_on_me")
    delegated_rows = _tasks_from_board_section(owned_board, "tasks_i_delegated")
    recent_completed_delegated_rows = _tasks_from_board_section(owned_board, "recent_completed_delegated_tasks")
    waiting_child_rows = _tasks_from_board_section(owned_board, "waiting_child_tasks")
    blocked_child_rows = _tasks_from_board_section(owned_board, "blocked_or_stalled_child_tasks")

    sections: list[tuple[str, list[str]]] = []
    if open_rows:
        sections.append(("MY OPEN TASKS", _task_board_lines(open_rows, limit=5)))
    if waiting_self_rows:
        sections.append(("MY WAITING TASKS", _task_board_lines(waiting_self_rows, limit=3)))
    if blocked_rows:
        sections.append(("MY BLOCKED TASKS", _task_board_lines(blocked_rows, limit=3)))
    if waiting_rows:
        sections.append(("TASKS WAITING ON ME", _task_board_lines(waiting_rows, limit=3)))
    if delegated_rows:
        sections.append(("TASKS I DELEGATED", _task_board_lines(delegated_rows, limit=3)))
    if recent_completed_delegated_rows:
        sections.append(("RECENT COMPLETED DELEGATIONS", _task_board_lines(recent_completed_delegated_rows, limit=3)))
    if waiting_child_rows:
        sections.append(("WAITING CHILD TASKS", _task_board_lines(waiting_child_rows, limit=3)))
    if blocked_child_rows:
        sections.append(("BLOCKED OR STALLED CHILD TASKS", _task_board_lines(blocked_child_rows, limit=3)))
    assignee_rollup = owned_board.get("assignee_rollup") or []
    if assignee_rollup:
        sections.append(("ASSIGNEE ROLLUP", _task_board_rollup_lines(assignee_rollup[:5])))

    if not sections:
        value = "none"
    else:
        rendered_sections: list[str] = []
        for title, lines in sections:
            rendered_sections.append(f"{title}:")
            rendered_sections.extend(lines)
        value = "\n".join(rendered_sections)

    return {
        "value": value,
        "open_count": len(open_rows),
        "waiting_count": len(waiting_rows),
        "delegated_count": len(delegated_rows),
        "blocked_count": len(blocked_rows) + len(blocked_child_rows),
    }


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
    """Return the personal board's other open tasks excluding the current one."""
    current_task_id = str((current_task or {}).get("id") or "").strip() or None
    board = build_task_board(agent_id, scope="self")
    return _tasks_from_board_section(board, "my_open_tasks", current_task_id=current_task_id)


def _tasks_from_board_section(
    board: dict[str, Any],
    section_name: str,
    *,
    current_task_id: str | None = None,
) -> list[Any]:
    """Return one board section with the active task filtered out when requested."""
    rows = list((board.get("sections") or {}).get(section_name, []))
    if not current_task_id:
        return rows
    return [task for task in rows if getattr(task, "id", None) != current_task_id]


def _task_board_lines(rows: list[Any], *, limit: int) -> list[str]:
    """Render compact task-board rows for prompt use."""
    lines = ["task id | datetime | status | assignee | task name | description"]
    for task in rows[:limit]:
        assignee_name = _task_assignee_name(task)
        lines.append(
            " | ".join(
                [
                    str(getattr(task, "id", "")),
                    _format_datetime(getattr(task, "last_activity", None) or getattr(task, "created_at", None)),
                    str(getattr(task, "status", "unknown")),
                    assignee_name or "-",
                    str(getattr(task, "title", "Untitled")),
                    _summarize_text(str(getattr(task, "description", None) or getattr(task, "status_note", None) or ""), limit=120),
                ]
            )
        )
    return lines


def _task_assignee_name(task: Any) -> str | None:
    """Return a display name for a task assignee when available."""
    assignee_id = getattr(task, "assigned_to", None)
    if not isinstance(assignee_id, str) or not assignee_id.strip():
        return None
    agent = db.get_agent(assignee_id)
    return agent.name if agent is not None else assignee_id


def _task_board_rollup_lines(rows: list[dict[str, Any]]) -> list[str]:
    """Render a compact assignee rollup table for prompt use."""
    lines = ["assignee | counts"]
    for row in rows:
        counts = ", ".join(f"{key}={value}" for key, value in sorted((row.get("counts") or {}).items()))
        lines.append(f"{row.get('agent_name') or row.get('agent_id')} | {counts or '-'}")
    return lines


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
    rows = db.get_recent_artifact_refs(agent_id, limit=limit)
    lines = ["RECENT WORK ARTIFACTS:", "datetime | task | path | title"]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for artifact in rows:
        lines.append(
            " | ".join(
                [
                    _format_datetime(artifact["created_at"]),
                    _summarize_text(str(artifact.get("task_title") or "-")),
                    _summarize_text(str(artifact["path"])),
                    _summarize_text(str(artifact["title"])),
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


def _format_trigger(
    trigger: dict[str, Any],
    contract_kind: str,
    template_overrides: dict[str, str] | None = None,
) -> str:
    """Format the trigger event for the turn contract in use."""
    render_context = {
        "trigger": _template_trigger(trigger),
        "turn": {
            "contract_kind": contract_kind,
        },
    }
    return render_template(
        resolve_runtime_prompt_text("runtime_block_trigger_event", template_overrides),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _render_turn_contract(
    contract_kind: str,
    render_context: dict[str, Any],
    template_overrides: dict[str, str] | None = None,
) -> str:
    """Render the current settings-backed runtime contract for one turn."""
    setting_key = "runtime_contract_decision" if contract_kind == "decision" else "runtime_contract_execution"
    template = resolve_runtime_prompt_text(setting_key, template_overrides)
    return render_template(template, render_context, allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS)


def preview_runtime_contract(
    contract_kind: str,
    trigger_type: str,
    template_overrides: dict[str, str] | None = None,
    trigger_overrides: dict[str, Any] | None = None,
) -> str:
    """Render one runtime contract against a representative preview context."""
    turn = _build_preview_turn_context(contract_kind, trigger_type)
    if trigger_overrides:
        turn.trigger = {**turn.trigger, **trigger_overrides}
    agent = turn.agent
    render_context = _build_prompt_render_context(turn)
    render_context["personality"] = render_template(
        agent.prompt_template or _default_role_prompt(agent),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )
    return _render_turn_contract(contract_kind, render_context, template_overrides)


def preview_prompt_bundle(
    contract_kind: str,
    trigger_type: str,
    template_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Render the representative full prompt bundle for one runtime turn."""
    turn = _build_preview_turn_context(contract_kind, trigger_type)
    messages = build_context(turn, template_overrides=template_overrides)
    return {
        "messages": messages,
        "rendered": _render_preview_messages(messages),
    }


def _build_preview_turn_context(contract_kind: str, trigger_type: str) -> TurnContext:
    """Build a representative preview turn context for prompt authoring."""
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
        communication_snapshot_json=_preview_communication_snapshot_json(trigger_type),
    )
    return turn


def _preview_communication_snapshot_json(trigger_type: str) -> str | None:
    """Return a representative snapshot JSON for previewable communication turns."""
    profile = communication_profile_for_trigger(trigger_type)
    if profile is None:
        return None
    snapshot = {
        "communication": {
            "profile": profile.name,
            "trigger_type": trigger_type,
            "speaker": "Human Operator" if trigger_type == "human_chat" else "Morgan",
            "author_type": "human" if trigger_type == "human_chat" else "agent",
        },
        "runtime": {
            "status": "idle",
            "location": "Main Workspace",
            "cwd": "/projects/orchard",
            "current_activity": "none",
            "current_task": "none",
            "self_open_task_count": 1,
            "owned_delegated_task_count": 0,
            "waiting_on_me_count": 0,
        },
        "current_task": {
            "id": "preview-task",
            "title": "Write API summary",
            "status": "active",
            "description": "Summarize the current API behavior and save the result.",
        },
        "task_board": {
            "self": {
                "scope": "self",
                "current_task": {
                    "id": "preview-task",
                    "title": "Write API summary",
                    "status": "active",
                    "description": "Summarize the current API behavior and save the result.",
                },
                "sections": {
                    "my_open_tasks": [
                        {
                            "id": "preview-waiting-task",
                            "title": "Review release notes",
                            "status": "accepted",
                            "description": "Review the draft release notes and flag gaps.",
                        }
                    ]
                },
            },
            "owned": {
                "scope": "owned",
                "current_task": {
                    "id": "preview-task",
                    "title": "Write API summary",
                    "status": "active",
                    "description": "Summarize the current API behavior and save the result.",
                },
                "sections": {},
            },
            "project_summary": [
                {
                    "project": "Orchard",
                    "path": "/projects/orchard",
                    "counts": {"accepted": 1},
                    "latest_tasks": [
                        {
                            "title": "Review release notes",
                            "status": "accepted",
                            "assigned_to": "preview-agent",
                            "assignee_name": "Taylor",
                        }
                    ],
                }
            ],
        },
        "recent_completed_tasks": [
            {
                "task_id": "done-1",
                "title": "Review deployment notes",
                "status": "complete",
                "summary": "Reviewed the notes and flagged rollout risks.",
            }
        ],
    }
    return json.dumps(snapshot, indent=2)


def _render_preview_messages(messages: list[dict[str, str]]) -> str:
    """Format one preview message bundle into a readable text block."""
    counters: dict[str, int] = {}
    blocks: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        counters[role] = counters.get(role, 0) + 1
        heading = f"[{role.upper()} {counters[role]}]"
        blocks.append(f"{heading}\n{message.get('content', '')}")
    return "\n\n".join(blocks)


def _render_conversation_envelope(
    turn: TurnContext,
    template_overrides: dict[str, str] | None = None,
) -> str:
    """Render runtime-owned speaker, audience, and channel facts for conversation turns."""
    trigger = turn.trigger
    trigger_type = str(trigger.get("type") or "unknown")
    speaker_type, speaker_name, speaker_id = _conversation_speaker(trigger)
    channel_kind, channel_name, participant_names = _conversation_channel(turn, trigger_type)
    audience_mode, target_names = _conversation_audience(turn, trigger_type)
    turn_purpose = _conversation_turn_purpose(trigger_type)
    render_context = _build_prompt_render_context(turn) | {
        "conversation": {
            "speaker_name": speaker_name,
            "speaker_type": speaker_type,
            "speaker_id": speaker_id,
            "channel_kind": channel_kind,
            "channel_name": channel_name,
            "turn_purpose": turn_purpose,
            "audience_mode": audience_mode,
            "audience_targets": ", ".join(target_names),
            "participants": ", ".join(participant_names),
        }
    }
    return render_template(
        resolve_runtime_prompt_text("runtime_block_conversation_envelope", template_overrides),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _render_file_deliverable_guidance(
    turn: TurnContext,
    template_overrides: dict[str, str] | None = None,
) -> str | None:
    """Return runtime-owned file-writing guidance for contract-bound work."""
    if turn.contract_kind != "execution" or not turn.current_task:
        return None
    contract = get_work_contract(turn.current_task)
    file_paths = [item.path for item in contract.deliverables if item.type == "file" and item.path]
    if not file_paths:
        return None
    render_context = _build_prompt_render_context(turn) | {
        "file_guidance": {
            "required_files": ", ".join(file_paths),
            "required_file_count": str(len(file_paths)),
        }
    }
    return render_template(
        resolve_runtime_prompt_text("runtime_block_file_deliverable_guidance", template_overrides),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _render_communication_snapshot(
    turn: TurnContext,
    template_overrides: dict[str, str] | None = None,
) -> str | None:
    """Render the authoritative bounded snapshot for communication turns."""
    if turn.contract_kind != "decision":
        return None
    trigger_type = str(turn.trigger.get("type") or "")
    profile = communication_profile_for_trigger(trigger_type)
    if profile is None or not turn.communication_snapshot_json:
        return None
    render_context = _build_prompt_render_context(turn) | {
        "communication_snapshot": {
            "json": turn.communication_snapshot_json,
        }
    }
    return render_template(
        resolve_runtime_prompt_text("runtime_block_communication_snapshot", template_overrides),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _conversation_speaker(trigger: dict[str, Any]) -> tuple[str, str, str]:
    """Return speaker metadata for one conversation trigger."""
    trigger_type = str(trigger.get("type") or "")
    if trigger_type == "human_chat":
        return "human", "Human Operator", "human"
    if trigger_type in {"task_assigned", "task_follow_up"}:
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
    if trigger_type == "task_follow_up":
        counterpart = str(turn.trigger.get("from_name") or "Coworker")
        task_title = str(turn.trigger.get("task_title") or "Task")
        return "task_thread", f'Task Attention: {task_title}', [turn.agent.name, counterpart]
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
    if trigger_type == "task_follow_up":
        return "direct", [str(turn.trigger.get("from_name") or "Coworker")]
    return "direct", ["Human Operator"]


def _conversation_turn_purpose(trigger_type: str) -> str:
    """Return a short runtime-owned purpose label for one conversation turn."""
    if trigger_type in {"channel_message", "session_message"}:
        return "shared_intake"
    if trigger_type in {"channel_response", "session_response"}:
        return "shared_reply"
    if trigger_type == "task_assigned":
        return "assignment_review"
    if trigger_type == "task_follow_up":
        return "task_attention"
    return "direct_reply"
