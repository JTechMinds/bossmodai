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
    conversation_history = _get_conversation_history(agent.id, trigger)
    nearby = _get_nearby_agents(agent.id, state)
    current_task = _get_current_task(state)
    reference_materials = _get_reference_materials(agent.id)

    pending_count = max(db.count_queued_triggers(agent.id) - 1, 0)

    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger=trigger,
            conversation_history=conversation_history,
            reference_materials=reference_materials,
            nearby_agents=nearby,
            current_task=current_task,
            pending_trigger_count=pending_count,
        )
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
    diagnostic_error: str | None = None

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
            diagnostic_error = str(exc)
            if state.current_task_id:
                db.update_task(
                    state.current_task_id,
                    status="blocked",
                    status_note=f"System provider failure: {exc}",
                    watchdog_pinged_at=None,
                )
                state = db.update_agent_state(agent.id, current_task_id=None) or state
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

        if not state.current_task_id:
            state = _maybe_start_implicit_task(agent, state, trigger, action)

        # Handle parse failure
        if action_name == "_parse_failed":
            logger.warning("Parse failure for %s: %s", agent.name, action.get("_raw_snippet", ""))
            diagnostic_status = "error"
            diagnostic_error = f"Failed to parse action JSON: {action.get('_raw_snippet', '')}"
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

        chat_message = result.get("chat_message")
        if chat_message:
            await manager.broadcast_chat_message(
                agent_id=chat_message["agent_id"],
                content=chat_message["content"],
                from_type=chat_message["from_type"],
                from_name=chat_message["from_name"],
                message_id=chat_message.get("message_id"),
                created_at=chat_message.get("created_at"),
            )

        for queued in result.get("queued_triggers", []):
            from core.agent_loop.dispatcher import dispatcher

            dispatcher.enqueue_trigger(
                agent_id=queued["agent_id"],
                trigger_type=queued["trigger_type"],
                source_channel=queued["source_channel"],
                payload=queued["payload"],
                task_id=queued.get("task_id"),
            )

        if state.current_task_id:
            db.update_task(
                state.current_task_id,
                last_activity=datetime.now(timezone.utc),
                watchdog_pinged_at=None,
            )

        # Guardian hard-stop checks (token explosion, velocity, repetition)
        violation = check_post_action(agent, action, response.content)
        if violation:
            logger.warning("Guardian %s for %s: %s", violation.rule, agent.name, violation.detail)
            diagnostic_status = "error"
            diagnostic_error = f"Guardian [{violation.rule}]: {violation.detail}"
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
            diagnostic_status = "error"
            diagnostic_error = f"Guardian [{violation.rule}]: {violation.detail}"
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
        error=diagnostic_error,
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

    if trigger.get("task_id"):
        db.update_task(
            trigger["task_id"],
            status="blocked",
            status_note=f"No model configured for '{mode}' mode",
            watchdog_pinged_at=None,
        )
        db.update_agent_state(agent.id, current_task_id=None)

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

    return "work"


def _get_conversation_history(agent_id: str, trigger: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch the relevant direct conversation history for this trigger."""
    trigger_type = trigger.get("type")
    limit = 50

    if trigger_type in ("human_chat", "watchdog_status_ping", "task_resumed", "task_attention_required"):
        thread = db.get_human_chat_thread(agent_id, limit=limit)
        return db.get_formatted_messages(thread, human_label="Human Operator")

    if trigger_type == "peer_message" and trigger.get("from_agent"):
        thread = db.get_agent_direct_thread(agent_id, trigger["from_agent"], limit=limit)
        return db.get_formatted_messages(thread, human_label="Human Operator")

    return []


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
        "completion_summary": task.completion_summary,
        "status_note": task.status_note,
    }


def _get_reference_materials(agent_id: str) -> list[str]:
    """Build non-chat references for the turn."""
    materials: list[str] = []

    teammates = [agent for agent in db.list_agents() if agent.id != agent_id]
    if teammates:
        directory_lines = [
            "TEAM DIRECTORY: use exact agentId values for agent-targeted actions.",
        ]
        for teammate in teammates:
            role = f" ({teammate.role})" if teammate.role else ""
            directory_lines.append(f"- {teammate.name}{role} — agentId: {teammate.id}")
        materials.append("\n".join(directory_lines))

    for task in db.get_recent_completed_tasks(
        agent_id,
        limit=(config.get_int("context_recent_completed_tasks") or 3),
    ):
        summary = task.get("completion_summary") or task.get("status_note") or "No summary"
        materials.append(f"- Completed task: {task.get('title')} [{task.get('status')}]: {summary}")

    for artifact in db.get_recent_work_artifacts(
        agent_id,
        limit=(config.get_int("context_recent_work_artifacts") or 5),
    ):
        snippet = (artifact.content or "").strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        materials.append(f"- Work artifact ({artifact.created_at.isoformat()}): {snippet}")

    return materials


def _maybe_start_implicit_task(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    action: dict[str, Any],
) -> AgentState:
    """Promote substantive human requests into a durable task when work begins."""
    if trigger.get("type") != "human_chat":
        return state

    action_name = action.get("action", "")
    if action_name not in {"work", "walkTo", "remoteMeeting", "complete", "blocked", "delegated", "abandoned"}:
        return state

    tracking = (action.get("tracking") or "").strip().lower()
    if tracking != "task":
        return state

    content = (trigger.get("content") or "").strip()
    title = content or "Human request"
    if len(title) > 80:
        title = title[:77] + "..."

    task = db.create_task(
        title=title or "Human request",
        description=trigger.get("content"),
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(task.id, status="active")
    return db.update_agent_state(agent.id, current_task_id=task.id) or state
