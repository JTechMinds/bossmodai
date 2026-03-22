"""BossMod AI — LLM context assembly.

Builds the full message list for an LLM call by resolving the
system_prompt_template from settings with runtime variables:

  {{personality}}        — agent's role prompt
  {{agent_name}}         — agent's display name
  {{role}}               — agent's role title
  {{memory}}             — relevant knowledge graph nodes
  {{worldStatus}}        — structured world status block
  {{task}}               — current task details (blank if none)
  {{available_actions}}  — action schema from settings

All prompts are editable via Settings. No hardcoded prompt text.
"""

from __future__ import annotations

import logging
from typing import Any

from core import config
from core.models import Agent, AgentState
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)

_FALLBACK_TEMPLATE = (
    "{{personality}}\n\n{{worldStatus}}\n\n{{task}}\n\n---\n\n{{available_actions}}"
)

_FALLBACK_ACTIONS = 'Respond with JSON: {"action":"idle","thought":"..."}'

_STATUS_LABELS = {
    "idle": "idle",
    "work_active": "working",
    "social_active": "socializing",
    "in_transit": "walking",
}


def build_context(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    messages_window: list[dict[str, Any]],
    memory_nodes: list[dict[str, Any]] | None = None,
    nearby_agents: list[dict[str, Any]] | None = None,
    current_task: dict[str, Any] | None = None,
    pending_message_count: int = 0,
) -> list[dict[str, str]]:
    """Assemble the full LLM message list for an agent turn."""
    window_size = config.get_int("context_window_messages") or 30
    messages: list[dict[str, str]] = []

    # ─── Build template variables ───
    personality = agent.prompt_template or _default_role_prompt(agent)
    personality = (
        personality
        .replace("{{agent_name}}", agent.name)
        .replace("{{role}}", agent.role or "AI Assistant")
    )

    variables = {
        "{{personality}}": personality,
        "{{agent_name}}": agent.name,
        "{{role}}": agent.role or "AI Assistant",
        "{{memory}}": _format_memory(memory_nodes) if memory_nodes else "",
        "{{worldStatus}}": _format_world_status(agent, state, nearby_agents, current_task, pending_message_count),
        "{{task}}": _format_task(current_task) if current_task else "",
        "{{available_actions}}": config.get("available_actions_schema") or _FALLBACK_ACTIONS,
    }

    # ─── Resolve template from settings ───
    template = config.get("system_prompt_template") or _FALLBACK_TEMPLATE
    system_prompt = template
    for key, value in variables.items():
        system_prompt = system_prompt.replace(key, value)

    messages.append({"role": "system", "content": system_prompt})

    # ─── Message history (rolling window) ───
    for msg in messages_window[-window_size:]:
        role = "assistant" if msg.get("from_agent") == agent.id else "user"
        sender = msg.get("from_name", "Unknown")
        content = msg.get("content", "")

        if role == "user":
            messages.append({"role": "user", "content": f"[{sender}]: {content}"})
        else:
            messages.append({"role": "assistant", "content": content})

    # ─── Trigger event ───
    messages.append({"role": "user", "content": _format_trigger(trigger)})

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
    pending_message_count: int = 0,
) -> str:
    """Build the structured world status block."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    status_label = _STATUS_LABELS.get(state.status, state.status)

    pending_count = pending_message_count

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
        f"  pendingMessages: {pending_count}\n"
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
    return f"{title} (status: {status})\n{desc}"


def _format_trigger(trigger: dict[str, Any]) -> str:
    """Format the trigger event. No 'respond with JSON' — the schema handles that."""
    trigger_type = trigger.get("type", "unknown")

    if trigger_type == "message":
        sender = trigger.get("from_name", "Someone")
        content = trigger.get("content", "")
        return f"[{sender}]: {content}"

    if trigger_type == "task_assigned":
        title = trigger.get("task_title", "a task")
        return f"You have been assigned a new task: \"{title}\""

    if trigger_type == "social":
        nearby = trigger.get("nearby_names", [])
        return f"You're idle and nearby: {', '.join(nearby)}. Consider a brief social interaction."

    if trigger_type == "schedule":
        desc = trigger.get("description", "scheduled task")
        return f"Scheduled trigger: {desc}"

    if trigger_type == "manual":
        content = trigger.get("content", "You have been manually activated.")
        return content

    return "You have been activated."
