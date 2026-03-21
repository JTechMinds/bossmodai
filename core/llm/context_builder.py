"""BossMod AI — LLM context assembly.

Builds the full message list for an LLM call by combining:
  1. System prompt (role + rules + available actions)
  2. Memory context (relevant knowledge graph nodes)
  3. Location context (current room, nearby agents)
  4. Rolling message window (recent conversation history)
  5. Trigger event (what activated this turn)

Window size is read from the ``context_window_messages`` setting.
"""

from __future__ import annotations

import logging
from typing import Any

from core import config
from core.models import Agent, AgentState
from core.world.tilemap import get_room_at

logger = logging.getLogger(__name__)

# Available actions an agent can take
AVAILABLE_ACTIONS = """
You can respond with ONE of these actions as a JSON object:

1. **work** — Produce work output (analysis, code, writing, etc.)
   {"thought": "...", "action": "work", "params": {"output": "your work product here"}}

2. **send_message** — Send a message to another agent
   {"thought": "...", "action": "send_message", "params": {"to": "agent_name", "content": "message text"}}

3. **walk_to** — Move to a different location
   {"thought": "...", "action": "walk_to", "params": {"room": "meeting_room|break_room|workspace_main|workspace_south|hallway_main"}}

4. **idle** — Do nothing this turn (wait for new input)
   {"thought": "...", "action": "idle", "params": {}}

5. **sign_off** — Complete or abandon current work
   {"thought": "...", "action": "sign_off", "params": {"status": "complete|blocked|delegated|abandoned", "summary": "brief summary"}}

IMPORTANT:
- Always respond with valid JSON only — no markdown, no extra text.
- The "thought" field is your internal reasoning (visible to admins).
- Choose the SINGLE most appropriate action for this turn.
- If you have nothing to do, use "idle".
""".strip()


def build_context(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    messages_window: list[dict[str, Any]],
    memory_nodes: list[dict[str, Any]] | None = None,
    nearby_agents: list[dict[str, Any]] | None = None,
    current_task: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Assemble the full LLM message list for an agent turn."""
    window_size = config.get_int("context_window_messages") or 30
    messages: list[dict[str, str]] = []

    # ─── System prompt ───
    system_parts: list[str] = []

    # Role identity
    role_text = agent.prompt_template or _default_role_prompt(agent)
    system_parts.append(role_text)

    # Available actions
    system_parts.append(AVAILABLE_ACTIONS)

    # Memory context
    if memory_nodes:
        system_parts.append(_format_memory(memory_nodes))

    # Location context
    system_parts.append(_format_location(state, nearby_agents))

    # Current task
    if current_task:
        system_parts.append(_format_task(current_task))

    messages.append({
        "role": "system",
        "content": "\n\n---\n\n".join(system_parts),
    })

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
        f"You are {agent.name}, a {role} at BossMod AI.\n"
        f"You work in a virtual office with other AI agents. "
        f"You communicate professionally, stay focused on your tasks, "
        f"and collaborate effectively with your team.\n"
        f"Your responses should be concise and actionable."
    )


def _format_memory(nodes: list[dict[str, Any]]) -> str:
    lines = ["## Your Knowledge (from memory)"]
    for node in nodes[:20]:
        entity = node.get("entity", "")
        attribute = node.get("attribute", "")
        value = node.get("value", "")
        confidence = node.get("confidence", 1.0)
        lines.append(f"- {entity}.{attribute} = {value} (confidence: {confidence:.0%})")
    return "\n".join(lines)


def _format_location(
    state: AgentState,
    nearby_agents: list[dict[str, Any]] | None = None,
) -> str:
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown area"

    parts = [f"## Current Location\nYou are at tile ({state.x}, {state.y}) in the {room_name}."]

    if nearby_agents:
        names = [a.get("name", "Unknown") for a in nearby_agents]
        parts.append(f"Nearby agents: {', '.join(names)}")
    else:
        parts.append("No other agents are nearby.")

    return "\n".join(parts)


def _format_task(task: dict[str, Any]) -> str:
    title = task.get("title", "Untitled")
    desc = task.get("description", "No description")
    status = task.get("status", "unknown")
    return f"## Current Task\n**{title}** (status: {status})\n{desc}"


def _format_trigger(trigger: dict[str, Any]) -> str:
    trigger_type = trigger.get("type", "unknown")

    if trigger_type == "message":
        sender = trigger.get("from_name", "Someone")
        content = trigger.get("content", "")
        return f"[{sender}]: {content}\n\nRespond with your action as JSON."

    if trigger_type == "task_assigned":
        title = trigger.get("task_title", "a task")
        return (
            f"You have been assigned a new task: \"{title}\"\n"
            f"Review the task details above and decide your next action. "
            f"Respond with JSON."
        )

    if trigger_type == "social":
        nearby = trigger.get("nearby_names", [])
        return (
            f"You're idle and nearby: {', '.join(nearby)}. "
            f"Consider having a brief social interaction, or idle if you prefer. "
            f"Respond with JSON."
        )

    if trigger_type == "schedule":
        desc = trigger.get("description", "scheduled task")
        return f"Scheduled trigger: {desc}\nRespond with JSON."

    if trigger_type == "manual":
        content = trigger.get("content", "You have been manually activated.")
        return f"{content}\nRespond with JSON."

    return "You have been activated. Decide your next action. Respond with JSON."
