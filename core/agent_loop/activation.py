"""BossMod AI — Agent activation triggers.

Determines when an idle agent should be activated for a turn.
All thresholds are read from the settings table via ``core.config``.

Work triggers (immediate):
  - Incoming message addressed to this agent
  - Task assigned or status change

Social triggers (4-gate check):
  1. Proximity — another agent is within ``social_proximity_tiles``
  2. Idle time — both agents idle for ``social_idle_threshold_minutes``
  3. Social cooldown — ``social_cooldown_minutes`` since last social chat
  4. Nearby idle agent exists
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core import config
from core.models import Agent, AgentState
import db

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime | str) -> datetime:
    """Normalize a datetime or ISO string to timezone-aware UTC."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def check_activation(
    agent: Agent,
    state: AgentState,
) -> dict[str, Any] | None:
    """Check all activation triggers for an idle agent.

    Returns a trigger dict if the agent should activate, or None.
    Work triggers take priority over social triggers.
    """
    trigger = await _check_message_trigger(agent, state)
    if trigger:
        return trigger

    trigger = await _check_task_trigger(agent, state)
    if trigger:
        return trigger

    trigger = await _check_social_trigger(agent, state)
    if trigger:
        return trigger

    return None


async def _check_message_trigger(
    agent: Agent,
    state: AgentState,
) -> dict[str, Any] | None:
    """Check for unread messages addressed to this agent.

    Only triggers on agent-to-agent messages. Human messages are
    handled synchronously by the activate endpoint — never here.
    """
    since = state.last_active_at or state.idle_since
    if not since:
        return None

    messages = db.get_unread_messages(agent.id, since=since)
    if not messages:
        return None

    # Filter out human messages — those are handled by the activate endpoint
    from core.models.message import HUMAN_SENDER_ID
    agent_messages = [m for m in messages if m.from_agent != HUMAN_SENDER_ID]
    if not agent_messages:
        return None

    msg = agent_messages[0]
    sender = db.get_agent(msg.from_agent)
    sender_name = sender.name if sender else "Unknown"

    logger.info("Message trigger for %s from %s", agent.name, sender_name)
    return {
        "type": "message",
        "from_agent": msg.from_agent,
        "from_name": sender_name,
        "content": msg.content,
        "message_type": msg.message_type,
    }


async def _check_task_trigger(
    agent: Agent,
    state: AgentState,
) -> dict[str, Any] | None:
    """Check for pending tasks assigned to this agent."""
    tasks = db.list_tasks(assigned_to=agent.id, status="pending")
    if not tasks:
        return None

    task = tasks[0]
    logger.info("Task trigger for %s: %s", agent.name, task.title)

    db.update_task(task.id, status="active")
    db.update_agent_state(agent.id, current_task_id=task.id)

    return {
        "type": "task_assigned",
        "task_id": task.id,
        "task_title": task.title,
        "task_description": task.description or "",
    }


async def _check_social_trigger(
    agent: Agent,
    state: AgentState,
) -> dict[str, Any] | None:
    """Check the 4-gate social activation trigger.

    All thresholds read from settings via ``core.config``.
    """
    now = datetime.now(timezone.utc)

    idle_threshold_min = config.get_int("social_idle_threshold_minutes")
    cooldown_min = config.get_int("social_cooldown_minutes")
    proximity = config.get_int("social_proximity_tiles")

    # If any social setting is missing, social triggers are disabled
    if not idle_threshold_min or not cooldown_min or not proximity:
        return None

    idle_threshold = timedelta(minutes=idle_threshold_min)
    cooldown = timedelta(minutes=cooldown_min)

    # Gate 1: This agent has been idle long enough
    if not state.idle_since:
        return None

    idle_since = _ensure_utc(state.idle_since)

    if now - idle_since < idle_threshold:
        return None

    # Gate 2: Find nearby idle agents (within proximity, also idle long enough)
    world = db.get_world_state()
    nearby_idle = []
    for w in world:
        if w["id"] == agent.id:
            continue
        if w.get("status") != "idle":
            continue

        dx = abs((w.get("x") or 0) - state.x)
        dy = abs((w.get("y") or 0) - state.y)
        if dx + dy > proximity:
            continue

        their_idle = w.get("idle_since")
        if their_idle:
            their_idle = _ensure_utc(their_idle)
            if now - their_idle >= idle_threshold:
                nearby_idle.append(w)

    if not nearby_idle:
        return None

    # Gate 3: Social cooldown — enough time since last social message
    messages = db.get_messages_for_agent(agent.id, limit=10)
    recent_social = [
        m for m in messages
        if m.message_type == "social" and m.from_agent == agent.id
    ]
    if recent_social:
        last_social = _ensure_utc(recent_social[-1].created_at)
        if now - last_social < cooldown:
            return None

    nearby_names = [w.get("name", "Unknown") for w in nearby_idle]
    logger.info("Social trigger for %s near %s", agent.name, nearby_names)

    return {
        "type": "social",
        "nearby_agents": nearby_idle,
        "nearby_names": nearby_names,
    }
