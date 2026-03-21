"""BossMod AI — Main agent turn execution cycle.

Orchestrates one complete agent turn:
  1. Determine activation mode (social / work / reasoning)
  2. Select the LLM model — skip turn if none configured
  3. Assemble context (role + memory + messages + location)
  4. Call the LLM (temperature/max_tokens from settings)
  5. Parse the JSON action from the response
  6. Execute the action (walk, message, work, idle, sign_off)
  7. Broadcast results via WebSocket

Returns path data for walk_to actions so the caller (simulation)
can manage movement without circular imports.
"""

from __future__ import annotations

import logging
from typing import Any

from api.websocket import manager
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.llm import client, context_builder, routing
from core.models import Agent, AgentState
from core.models.message import HUMAN_SENDER_ID
import db

logger = logging.getLogger(__name__)


async def run_turn(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single agent turn and return the result.

    Returns a dict describing what happened, suitable for
    activity log broadcasting. If the action is ``walk_to``,
    the result includes ``"path"`` data for the simulation to use.
    """
    logger.info("Running turn for %s (trigger: %s)", agent.name, trigger.get("type"))

    # 1. Determine activation mode
    mode = _determine_mode(trigger)

    # 2. Select model — if none configured, skip turn
    model = routing.select_model(agent, mode)
    if model is None:
        logger.warning(
            "No model configured for %s (mode=%s) — skipping turn. "
            "Configure a model via the agent panel or global settings.",
            agent.name, mode,
        )
        result = {
            "event": "agent_updated",
            "detail": f"{agent.name}: no model configured for '{mode}' mode — turn skipped",
            "agent_name": agent.name,
        }
        await manager.broadcast_activity(**result)
        return result

    api_config = routing.get_api_config(agent)

    # 3. Build context
    messages_window = _get_message_window(agent.id)
    nearby = _get_nearby_agents(agent.id, state)
    current_task = _get_current_task(state)

    context = context_builder.build_context(
        agent=agent,
        state=state,
        trigger=trigger,
        messages_window=messages_window,
        nearby_agents=nearby,
        current_task=current_task,
    )

    # 4. Call LLM (temperature/max_tokens come from settings via client)
    try:
        response = await client.completion(
            model=model,
            messages=context,
            api_base=api_config.get("api_base"),
            api_key=api_config.get("api_key"),
        )
    except client.LLMError as exc:
        logger.error("LLM call failed for %s: %s", agent.name, exc)
        db.update_agent_state(agent.id, status="idle")
        result = {
            "event": "agent_updated",
            "detail": f"{agent.name} LLM call failed: {exc}",
            "agent_name": agent.name,
        }
        await manager.broadcast_activity(**result)
        return result

    logger.info(
        "LLM response for %s: model=%s, tokens=%d",
        agent.name, response.model, response.total_tokens,
    )

    # 5. Parse action
    action = parse_action(response.content)
    logger.info(
        "Agent %s action: %s (thought: %s)",
        agent.name, action["action"], action["thought"][:100],
    )

    # 6. Execute action
    result = await execute_action(action, agent, state)

    # 7. Broadcast
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event=result.get("event", "agent_updated"),
        detail=result.get("detail", ""),
        agent_name=result.get("agent_name"),
    )

    return result


def _determine_mode(trigger: dict[str, Any]) -> routing.ActivationMode:
    """Map a trigger to an activation mode for model selection."""
    trigger_type = trigger.get("type", "")

    if trigger_type == "social":
        return "social"

    msg_type = trigger.get("message_type", "")
    if msg_type == "social":
        return "social"

    return "work"


def _get_message_window(agent_id: str) -> list[dict[str, Any]]:
    """Fetch recent messages for the rolling context window."""
    limit = config.get_int("context_window_messages") or 30
    messages = db.get_messages_for_agent(agent_id, limit=limit)

    # Batch-fetch all sender agents in one query instead of N+1
    sender_ids = list({msg.from_agent for msg in messages})
    agents_map = db.get_agents_by_ids(sender_ids)

    result = []
    for msg in messages:
        if msg.from_agent == HUMAN_SENDER_ID:
            from_name = "Human Operator"
        else:
            sender = agents_map.get(msg.from_agent)
            from_name = sender.name if sender else "Unknown"
        result.append({
            "from_agent": msg.from_agent,
            "from_name": from_name,
            "to_agent": msg.to_agent,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        })

    return result


def _get_nearby_agents(
    agent_id: str,
    state: AgentState,
) -> list[dict[str, Any]]:
    """Find agents within proximity of the current agent."""
    radius = config.get_int("social_proximity_tiles") or 8
    return db.get_nearby_agents(agent_id, state.x, state.y, radius)


def _get_current_task(state: AgentState) -> dict[str, Any] | None:
    """Fetch the agent's current task if any."""
    if not state.current_task_id:
        return None

    task = db.get_task(state.current_task_id)
    if not task:
        return None

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "project": task.project,
    }
