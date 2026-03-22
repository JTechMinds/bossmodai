"""BossMod AI — Multi-turn agent execution loop.

Orchestrates a complete agent activation:
  1. Determine activation mode (social / work)
  2. Select the LLM model — skip if none configured
  3. Loop: build context → call LLM → parse → Guardian → execute
  4. Continue until a terminal action or Guardian violation
  5. Guarantee agent returns to idle (unless in_transit)
  6. Broadcast results via WebSocket after every action

Terminal actions: idle, complete, blocked, delegated, abandoned
Non-terminal actions: work, message, remoteMeeting (loop continues)
Walk action: walkTo ends the loop (movement handled by simulation)

Returns path data for walk_to actions so the caller (simulation)
can manage movement without circular imports.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from api.websocket import manager
from core import config
from core.agent_loop.actions import TERMINAL_ACTIONS, execute_action, parse_action
from core.agent_loop.guardian import check_no_progress, check_post_action
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
    """Execute a multi-turn agent activation.

    Loops calling the LLM until the agent produces a terminal action
    (idle/complete/blocked/delegated/abandoned), walks somewhere, or
    the Guardian intervenes. Every exit path sets the agent to idle
    (unless in_transit) and updates last_active_at.
    """
    start = time.monotonic()
    logger.info("Running turn for %s (trigger: %s)", agent.name, trigger.get("type"))

    trigger_type = trigger.get("type", "unknown")

    # 1. Determine activation mode
    mode = _determine_mode(trigger)

    # 2. Select model
    model, model_source = routing.select_model_with_source(agent, mode)
    if model is None:
        return await _skip_turn(agent, trigger, trigger_type, mode, model_source, start)

    api_config = routing.get_api_config(agent)

    # 3. Build initial context
    messages_window = _get_message_window(agent.id)
    nearby = _get_nearby_agents(agent.id, state)
    current_task = _get_current_task(state)

    # Count pending messages for world status (only meaningful on first turn)
    unread = db.get_unread_messages(agent.id, since=state.last_active_at)
    pending_count = len(unread) if unread else 0

    context = context_builder.build_context(
        agent=agent, state=state, trigger=trigger,
        messages_window=messages_window,
        nearby_agents=nearby, current_task=current_task,
        pending_message_count=pending_count,
    )
    initial_context_json = json.dumps(context)

    # 4. Multi-turn loop
    action_count = 0
    action: dict[str, Any] | None = None
    result: dict[str, Any] = {}
    diagnostic_status = "success"
    last_action_name = ""
    last_response_content = ""
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    while True:
        action_count += 1

        # Call LLM
        try:
            response = await client.completion(
                model=model, messages=context,
                api_base=api_config.get("api_base"),
                api_key=api_config.get("api_key"),
                extra_body=api_config.get("extra_body"),
            )
        except client.LLMError as exc:
            logger.error("LLM call failed for %s: %s", agent.name, exc)
            diagnostic_status = "error"
            result = {
                "event": "agent_updated",
                "detail": f"{agent.name} LLM call failed: {exc}",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**{k: result[k] for k in ("event", "detail", "agent_name")})
            break

        total_prompt_tokens += response.prompt_tokens
        total_completion_tokens += response.completion_tokens
        total_tokens += response.total_tokens
        last_response_content = response.content

        logger.info(
            "LLM response for %s (turn %d): model=%s, tokens=%d",
            agent.name, action_count, response.model, response.total_tokens,
        )

        # Parse action
        action = parse_action(response.content)
        action_name = action["action"]
        last_action_name = action_name

        # Handle parse failure
        if action_name == "_parse_failed":
            logger.warning("Parse failure for %s: %s", agent.name, action.get("_raw_snippet", ""))
            diagnostic_status = "parse_error"
            action["action"] = "idle"
            action_name = "idle"
            result = await execute_action(action, agent, state)
            break

        logger.info(
            "Agent %s action: %s (thought: %s)",
            agent.name, action_name, action.get("thought", "")[:100],
        )

        # Execute action
        result = await execute_action(action, agent, state)

        # Broadcast action result immediately
        await manager.broadcast_world_state()
        await manager.broadcast_activity(
            event=result.get("event", "agent_updated"),
            detail=result.get("detail", ""),
            agent_name=result.get("agent_name"),
        )

        # Persist and broadcast reply for human-facing content
        reply_content = result.get("content")
        if reply_content:
            agent_msg = db.create_message(
                from_agent=agent.id,
                to_agent=HUMAN_SENDER_ID,
                content=reply_content,
                message_type="work",
                location_x=state.x,
                location_y=state.y,
            )
            await manager.broadcast_chat_message(
                agent_id=agent.id,
                content=reply_content,
                from_type="agent",
                from_name=agent.name,
                message_id=agent_msg.id,
                created_at=agent_msg.created_at,
            )

        # Guardian hard-stop checks (token explosion, velocity, repetition)
        violation = check_post_action(agent, action, response.content)
        if violation:
            logger.warning("Guardian %s for %s: %s", violation.rule, agent.name, violation.detail)
            diagnostic_status = "guardian_blocked"
            await manager.broadcast_activity(
                event="guardian_violation",
                detail=f"Guardian [{violation.rule}]: {agent.name} — {violation.detail}",
                agent_name=agent.name,
            )
            break

        # Guardian no-progress check
        violation = check_no_progress(agent, action_count)
        if violation:
            logger.warning("Guardian %s for %s: %s", violation.rule, agent.name, violation.detail)
            diagnostic_status = "guardian_blocked"
            await manager.broadcast_activity(
                event="guardian_violation",
                detail=f"Guardian [{violation.rule}]: {agent.name} — {violation.detail}",
                agent_name=agent.name,
            )
            break

        # Terminal action — loop ends
        if action_name in TERMINAL_ACTIONS:
            break

        # Walk action — loop ends (movement is async via simulation)
        if action_name == "walkTo":
            break

        # Non-terminal action — feed result back and continue
        context.append({"role": "assistant", "content": response.content})
        context.append({
            "role": "user",
            "content": (
                f"Action executed: {result.get('detail', action_name)}. "
                f"Continue working or sign off (complete/blocked/delegated/abandoned)."
            ),
        })

    # 5. Guarantee agent returns to idle (unless walking)
    final_state = db.get_agent_state(agent.id)
    if final_state and final_state.status not in ("idle", "in_transit"):
        db.update_agent_state(agent.id, status="idle")

    # Always update last_active_at to prevent re-triggering
    db.update_agent_state(agent.id, last_active_at=datetime.now(timezone.utc))

    # 6. Diagnostic for the full activation
    diag = db.create_diagnostic(
        agent_id=agent.id, agent_name=agent.name,
        trigger_type=trigger_type, trigger_data=json.dumps(trigger),
        status=diagnostic_status, mode=mode, model=model, model_source=model_source,
        context=initial_context_json, raw_response=last_response_content,
        action_name=last_action_name, parsed_action=json.dumps(action) if action else None,
        result=json.dumps(result, default=str),
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    await manager.broadcast_diagnostic(diag)

    logger.info(
        "Turn complete for %s: %d actions, %d total tokens, %dms",
        agent.name, action_count, total_tokens,
        int((time.monotonic() - start) * 1000),
    )

    return result


async def _skip_turn(
    agent: Agent,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model_source: str,
    start: float,
) -> dict[str, Any]:
    """Handle the case where no model is configured for the activation mode."""
    logger.warning(
        "No model configured for %s (mode=%s) — skipping turn.",
        agent.name, mode,
    )
    result = {
        "event": "agent_updated",
        "detail": f"{agent.name}: no model configured for '{mode}' mode — turn skipped",
        "agent_name": agent.name,
    }
    await manager.broadcast_activity(**result)

    db.update_agent_state(agent.id, last_active_at=datetime.now(timezone.utc))

    diag = db.create_diagnostic(
        agent_id=agent.id, agent_name=agent.name,
        trigger_type=trigger_type, trigger_data=json.dumps(trigger),
        status="skipped", mode=mode, model=None, model_source=model_source,
        error=f"No model configured for '{mode}' mode",
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    await manager.broadcast_diagnostic(diag)
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
    return db.get_formatted_messages(agent_id, limit=limit, human_label="Human Operator")


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
