"""BossMod AI — LLM context assembly."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import db
from core import config
from core.agent_loop.action_contract import render_action_contract
from core.models import Agent, AgentState
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)

_FALLBACK_TEMPLATE = """# Role

You are {{agent_name}}, an employee at BossMod that works in a virtual office. You are in control of your virtual character which represents your physical location at BossMod.

Each turn you must respond with exactly one JSON action.

## Personality
{{personality}}

# Context

## Work Summaries / Team Directory
{{references}}

## Memories
{{memory}}

## World Status
{{worldStatus}}

## Current Task Details
{{task}}

## Pending Tasks
{{pending_tasks}}

---

# Policies and Rules

- Durable work output can only be produced from a workspace.
- Move to a workspace before starting or resuming durable work.
- You may attend an in-person meeting by walking to `meetingRoom` and then using `attendMeeting`.
- You may start or join a remote meeting from a workspace using `remoteMeeting`.
- Use `message` when you need to reply to the human operator.
- Use `startTask` when a conversation becomes a durable assignment.
- Use `resumeTask` when you should return to pending work after an interruption.

# Output

Return exactly one valid JSON action object and nothing else."""

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
    reference_materials: list[str]
    current_task: dict[str, Any] | None = None
    memory_nodes: list[dict[str, Any]] | None = None
    nearby_agents: list[dict[str, Any]] | None = None
    pending_trigger_count: int = 0


def build_context(turn: TurnContext) -> list[dict[str, str]]:
    """Assemble the full message list for an agent turn."""
    window_size = config.get_int("context_window_messages") or 30
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
        "{{memory}}": _format_memory(turn.memory_nodes) if turn.memory_nodes else "",
        "{{worldStatus}}": _format_world_status(
            turn.agent,
            turn.state,
            turn.nearby_agents,
            turn.current_task,
            turn.pending_trigger_count,
        ),
        "{{task}}": _format_task(turn.current_task) if turn.current_task else "",
        "{{pending_tasks}}": _format_pending_tasks(turn.agent.id, turn.current_task),
        "{{references}}": _format_references(turn.reference_materials),
        "{{action_contract}}": render_action_contract(),
    }

    # ─── Resolve template from settings ───
    template = config.get("system_prompt_template") or _FALLBACK_TEMPLATE
    system_prompt = template
    for key, value in variables.items():
        system_prompt = system_prompt.replace(key, value)

    messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "system",
        "content": render_action_contract(),
    })

    for msg in turn.conversation_history[-window_size:]:
        role = "assistant" if msg.get("from_agent") == turn.agent.id else "user"
        sender = msg.get("from_name", "Unknown")
        content = msg.get("content", "")

        if role == "user":
            messages.append({"role": "user", "content": f"[{sender}]: {content}"})
        else:
            messages.append({"role": "assistant", "content": content})

    # ─── Trigger event ───
    messages.append({"role": "user", "content": _format_trigger(turn.trigger)})

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
    current_task: dict[str, Any] | None = None,
    pending_trigger_count: int = 0,
) -> str:
    """Build the structured world status block."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)

    pending_count = pending_trigger_count
    pending_tasks = db.list_tasks(assigned_to=agent.id, status="pending")

    # Nearby agents
    nearby_str = "none"
    if nearby_agents:
        names = [a.get("name", "Unknown") for a in nearby_agents]
        nearby_str = ", ".join(names)

    # Current task
    task_str = "none"
    if current_task:
        task_str = f"{current_task.get('title', 'Untitled')} ({current_task.get('status', 'unknown')})"

    return (
        f"WORLD STATUS:\n"
        f"  location: {room_name}\n"
        f"  status: {status_label}\n"
        f"  nearby: {nearby_str}\n"
        f"  pendingTriggers: {pending_count}\n"
        f"  pendingTasks: {len(pending_tasks)}\n"
        f"  currentTask: {task_str}"
    )


def _format_memory(nodes: list[dict[str, Any]]) -> str:
    lines = []
    for node in nodes[:20]:
        entity = node.get("entity", "")
        attribute = node.get("attribute", "")
        value = node.get("value", "")
        confidence = node.get("confidence", 1.0)
        lines.append(f"- {entity}.{attribute} = {value} (confidence: {confidence:.0%})")
    return "\n".join(lines)


def _format_task(task: dict[str, Any]) -> str:
    title = task.get("title", "Untitled")
    desc = task.get("description", "No description")
    status = task.get("status", "unknown")
    summary = task.get("completion_summary") or task.get("status_note")
    details = [f"{title} (status: {status})", f"Task ID: {task.get('id', 'unknown')}", desc]
    if summary:
        details.append(f"Latest summary: {summary}")
    return "\n".join([part for part in details if part])


def _format_pending_tasks(agent_id: str, current_task: dict[str, Any] | None) -> str:
    current_task_id = current_task.get("id") if current_task else None
    pending = db.list_tasks(assigned_to=agent_id, status="pending")
    if not pending:
        return ""

    lines: list[str] = []
    for task in pending[-3:]:
        if task.id == current_task_id:
            continue
        line = f"- {task.title} (pending)"
        if task.status_note:
            line += f": {task.status_note}"
        lines.append(line)
    return "\n".join(lines)


def _format_references(reference_materials: list[str]) -> str:
    if not reference_materials:
        return ""
    lines = ["REFERENCE MATERIALS:"]
    for item in reference_materials:
        lines.append(item)
    return "\n".join(lines)


def _format_trigger(trigger: dict[str, Any]) -> str:
    """Format the trigger event. No 'respond with JSON' — the schema handles that."""
    trigger_type = trigger.get("type", "unknown")

    if trigger_type in ("message", "human_chat", "peer_message"):
        sender = trigger.get("from_name", "Someone")
        content = trigger.get("content", "")
        return f"CURRENT REQUEST FROM [{sender}]: {content}"

    if trigger_type in ("task_assigned", "task_resumed", "task_attention_required"):
        title = trigger.get("task_title", "a task")
        desc = trigger.get("task_description", "")
        extra = f"\nTask description: {desc}" if desc else ""
        if trigger_type == "task_resumed":
            return f'You arrived and should resume your active task: "{title}".{extra}'
        if trigger_type == "task_attention_required":
            room_name = trigger.get("room_name", "current location")
            return (
                f'You still have an active task: "{title}", but you are in the {room_name}. '
                f'You cannot produce work there.{extra}\nChoose the next valid step: walk to your desk, '
                'message the human operator with a status update, use "resumeTask" if the task was interrupted, '
                "or sign off with complete/blocked/delegated/abandoned."
            )
        return f"You have been assigned a new task: \"{title}\".{extra}"

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
