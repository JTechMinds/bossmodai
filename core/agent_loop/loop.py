"""BossMod AI — Multi-turn agent execution loop.

Orchestrates a complete agent activation:
  1. Determine activation mode (social / work)
  2. Select the LLM model — skip if none configured
  3. Loop: build context → call LLM → parse → Guardian → execute
  4. Continue until a terminal action or Guardian violation
  5. Persist diagnostics and leave visible status to the activity runtime
  6. Broadcast results via WebSocket after every action

Terminal actions: idle, complete, blocked, delegated, abandoned
Non-terminal actions: work, message, attendMeeting, remoteMeeting (loop continues)
Walk action: walkTo ends the loop (movement handled by simulation)

Returns structured action outcomes so the dispatcher can apply follow-up
planning without circular imports.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from api.websocket import manager
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import TERMINAL_ACTIONS, execute_action, parse_action
from core.agent_loop.activity_scheduler import plan_post_turn_follow_up
from core.agent_loop.guardian import check_no_progress, check_post_action
from core.agent_loop.liveness import record_action_liveness
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.policies import get_trigger_policy
from core.agent_loop.turn_rules import (
    should_end_turn_after_action,
    validate_action_for_turn,
)
from core.llm import client, context_builder, routing
from core.models import Agent, AgentState
import db

logger = logging.getLogger(__name__)


async def run_turn(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
) -> TurnOutcome:
    """Execute a multi-turn agent activation.

    Loops calling the LLM until the agent produces a terminal action
    (idle/complete/blocked/delegated/abandoned), walks somewhere, or
    the Guardian intervenes. Every exit path sets the agent to idle
    (unless in_transit) and updates last_active_at.
    """
    start = time.monotonic()
    logger.info("Running turn for %s (trigger: %s)", agent.name, trigger.get("type"))

    trigger_type = trigger.get("type", "unknown")
    policy = get_trigger_policy(trigger_type)

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
    initial_activity = activity_runtime.get_active_activity(agent.id)
    current_task = _get_current_task(agent.id)
    reference_materials = _get_reference_materials(agent.id)

    pending_count = max(db.count_queued_triggers(agent.id) - 1, 0)

    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger=trigger,
            conversation_history=conversation_history,
            reference_materials=reference_materials,
            current_activity=_get_current_activity(agent.id),
            nearby_agents=nearby,
            current_task=current_task,
            pending_trigger_count=pending_count,
        )
    )
    initial_context_json = json.dumps(context)
    initial_task_id = activity_runtime.get_active_task_id(agent.id)

    if policy.require_work_activity and not initial_task_id:
        result = {
            "event": "agent_error",
            "detail": f"{agent.name} could not find an active task for {trigger_type}",
            "agent_name": agent.name,
        }
        await manager.broadcast_activity(**result)
        return await _finalize_turn(
            agent=agent,
            trigger=trigger,
            trigger_type=trigger_type,
            mode=mode,
            model=model,
            model_source=model_source,
            initial_context_json=initial_context_json,
            outcome=TurnOutcome.failure(
                result=result,
                    error="Trigger requires active work activity",
                action=None,
                action_summary="",
                raw_response="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            ),
            start=start,
        )

    # 4. Multi-turn loop
    action_count = 0
    action: dict[str, Any] | None = None
    executed_actions: list[str] = []
    result: dict[str, Any] = {}
    last_response_content = ""
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    step_traces: list[dict[str, Any]] = []
    next_step_delta: str | None = None
    scheduled_triggers: list[dict[str, Any]] = []

    while True:
        action_count += 1
        step_started = time.monotonic()
        prompt_delta = next_step_delta

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
            result = {
                "event": "agent_error",
                "detail": f"{agent.name} LLM call failed: {exc}",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**result)
            return await _finalize_turn(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                outcome=TurnOutcome.failure(
                    result=result,
                    error=str(exc),
                    action=action,
                    action_summary=_summarize_action_chain(executed_actions, ""),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=action_count,
                            context_snapshot=prompt_delta,
                            raw_response=None,
                            action=None,
                            result=result,
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=str(exc),
                        ),
                    ],
                ),
                start=start,
            )

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

        active_task_id = activity_runtime.get_active_task_id(agent.id)
        active_activity = activity_runtime.get_active_activity(agent.id)

        # Handle parse failure
        if action_name == "_parse_failed":
            logger.warning("Parse failure for %s: %s", agent.name, action.get("_raw_snippet", ""))
            result = {
                "event": "agent_error",
                "detail": f"{agent.name} returned invalid action JSON",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**result)
            return await _finalize_turn(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                outcome=TurnOutcome.failure(
                    result=result,
                    error=f"Failed to parse action JSON: {action.get('_raw_snippet', '')}",
                    action=action,
                    action_summary=_summarize_action_chain(executed_actions, ""),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=action_count,
                            context_snapshot=prompt_delta,
                            raw_response=last_response_content,
                            action=action,
                            result=result,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            total_tokens=response.total_tokens,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=f"Failed to parse action JSON: {action.get('_raw_snippet', '')}",
                        ),
                    ],
                ),
                start=start,
            )

        validation_error = validate_action_for_turn(
            action,
            policy,
            active_activity.kind if active_activity else None,
            active_task_id,
        )
        if validation_error:
            logger.warning("Contextual action validation failed for %s: %s", agent.name, validation_error)
            result = {
                "event": "agent_error",
                "detail": f"{agent.name} returned an invalid task action",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**result)
            return await _finalize_turn(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                outcome=TurnOutcome.failure(
                    result=result,
                    error=validation_error,
                    action=action,
                    action_summary=_summarize_action_chain(executed_actions, action_name),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=action_count,
                            context_snapshot=prompt_delta,
                            raw_response=last_response_content,
                            action=action,
                            result=result,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            total_tokens=response.total_tokens,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=validation_error,
                        ),
                    ],
                ),
                start=start,
            )

        logger.info(
            "Agent %s action: %s (thought: %s)",
            agent.name, action_name, action.get("thought", "")[:100],
        )
        executed_actions.append(action_name)

        # Execute action
        result = await execute_action(action, agent, state, trigger, token_model=response.model)
        active_task_id = activity_runtime.get_active_task_id(agent.id)
        if result.get("trigger_requests"):
            scheduled_triggers.extend(result["trigger_requests"])

        # Broadcast action result immediately
        await manager.broadcast_world_state()

        thought_text = action.get("thought", "")
        if thought_text:
            await manager.broadcast_thought(
                agent_id=agent.id, thought=thought_text, action_name=action_name,
            )

        await manager.broadcast_activity(
            event=result.get("event", "agent_updated"),
            detail=result.get("detail", ""),
            agent_name=result.get("agent_name"),
            extra=result.get("activity_extra"),
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

        record_action_liveness(active_task_id, action, result, at=datetime.now(timezone.utc))

        # Guardian hard-stop checks (token explosion, velocity, repetition)
        violation = check_post_action(agent, action, response.content, model=response.model)
        if violation:
            logger.warning("Guardian %s for %s: %s", violation.rule, agent.name, violation.detail)
            result = {
                "event": "guardian_violation",
                "detail": f"Guardian [{violation.rule}]: {agent.name} — {violation.detail}",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**result)
            return await _finalize_turn(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                outcome=TurnOutcome.failure(
                    result=result,
                    error=f"Guardian [{violation.rule}]: {violation.detail}",
                    action=action,
                    action_summary=_summarize_action_chain(executed_actions, action_name),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=action_count,
                            context_snapshot=prompt_delta,
                            raw_response=last_response_content,
                            action=action,
                            result=result,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            total_tokens=response.total_tokens,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=f"Guardian [{violation.rule}]: {violation.detail}",
                        ),
                    ],
                ),
                start=start,
            )

        # Guardian no-progress check
        violation = check_no_progress(agent, action_count)
        if violation:
            logger.warning("Guardian %s for %s: %s", violation.rule, agent.name, violation.detail)
            result = {
                "event": "guardian_violation",
                "detail": f"Guardian [{violation.rule}]: {agent.name} — {violation.detail}",
                "agent_name": agent.name,
            }
            await manager.broadcast_activity(**result)
            return await _finalize_turn(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                outcome=TurnOutcome.failure(
                    result=result,
                    error=f"Guardian [{violation.rule}]: {violation.detail}",
                    action=action,
                    action_summary=_summarize_action_chain(executed_actions, action_name),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=action_count,
                            context_snapshot=prompt_delta,
                            raw_response=last_response_content,
                            action=action,
                            result=result,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            total_tokens=response.total_tokens,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=f"Guardian [{violation.rule}]: {violation.detail}",
                        ),
                    ],
                ),
                start=start,
            )

        step_traces.append(
            _build_step_trace(
                step_index=action_count,
                context_snapshot=prompt_delta,
                raw_response=last_response_content,
                action=action,
                result=result,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
                duration_ms=int((time.monotonic() - step_started) * 1000),
            )
        )

        # Terminal action — loop ends
        if action_name in TERMINAL_ACTIONS:
            break

        # Walk action — loop ends (movement is async via simulation)
        if action_name == "walkTo" and result.get("path"):
            break

        if should_end_turn_after_action(action, policy):
            break

        if _has_pending_interrupts(agent.id):
            break

        # Non-terminal action — feed result back and continue
        continuation_messages = [
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": (
                    f"Action executed: {result.get('detail', action_name)}. "
                    f"Continue working or sign off (complete/blocked/delegated/abandoned)."
                ),
            },
        ]
        context.extend(continuation_messages)
        next_step_delta = _serialize_trace_value(continuation_messages)

    final_activity = activity_runtime.get_active_activity(agent.id)
    final_result = dict(result)
    final_result["trigger_requests"] = plan_post_turn_follow_up(
        agent_id=agent.id,
        trigger=trigger,
        initial_activity=initial_activity,
        final_activity=final_activity,
        result={**final_result, "trigger_requests": scheduled_triggers},
        action=action,
    )

    return await _finalize_turn(
        agent=agent,
        trigger=trigger,
        trigger_type=trigger_type,
        mode=mode,
        model=model,
        model_source=model_source,
        initial_context_json=initial_context_json,
        outcome=TurnOutcome.success(
            result=final_result,
            action=action,
            action_summary=_summarize_action_chain(executed_actions, action["action"] if action else ""),
            raw_response=last_response_content,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            steps=step_traces,
        ),
        start=start,
    )


async def _skip_turn(
    agent: Agent,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model_source: str,
    start: float,
) -> TurnOutcome:
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
        active_work = activity_runtime.get_active_work_activity(agent.id)
        if active_work:
            activity_runtime.complete_activity(active_work.id, detail=f"No model configured for '{mode}' mode")

    return await _finalize_turn(
        agent=agent,
        trigger=trigger,
        trigger_type=trigger_type,
        mode=mode,
        model=None,
        model_source=model_source,
        initial_context_json=None,
        outcome=TurnOutcome.skipped(
            result=result,
            error=f"No model configured for '{mode}' mode",
            steps=[],
        ),
        start=start,
    )


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

    if trigger_type in ("human_chat", "watchdog_status_ping"):
        thread = db.get_human_chat_thread(agent_id, limit=limit)
        return db.get_formatted_messages(thread, human_label="Human Operator")

    if trigger_type == "peer_message" and trigger.get("from_agent"):
        thread = db.get_agent_direct_thread(agent_id, trigger["from_agent"], limit=limit)
        return db.get_formatted_messages(thread, human_label="Human Operator")

    if trigger_type == "activity_resumed":
        active = activity_runtime.get_active_activity(agent_id)
        if active and active.kind in {"conversation", "meeting"}:
            thread = db.get_human_chat_thread(agent_id, limit=limit)
            return db.get_formatted_messages(thread, human_label="Human Operator")

    return []


def _get_nearby_agents(
    agent_id: str,
    state: AgentState,
) -> list[dict[str, Any]]:
    """Find agents within proximity of the current agent."""
    radius = config.get_int("social_proximity_tiles") or 8
    return db.get_nearby_agents(agent_id, state.x, state.y, radius)


def _get_current_task(agent_id: str) -> dict[str, Any] | None:
    """Fetch the agent's current task if any."""
    active = activity_runtime.get_active_activity(agent_id)
    if not active or not active.task_id:
        return None

    task = db.get_task(active.task_id)
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


def _get_current_activity(agent_id: str) -> dict[str, Any] | None:
    """Fetch the current runtime activity for prompt rendering."""
    activity = activity_runtime.get_active_activity(agent_id)
    if not activity:
        return None
    return {
        "id": activity.id,
        "kind": activity.kind,
        "status": activity.status,
        "title": activity.title,
        "detail": activity.detail,
        "destination": activity.destination,
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

    pending = db.list_tasks(assigned_to=agent_id, status="pending")
    for task in pending[-3:]:
        note = task.status_note or "Pending work"
        materials.append(f"- Pending task: {task.title} [{task.status}]: {note}")

    return materials

def _summarize_action_chain(executed_actions: list[str], default_label: str) -> str:
    """Render a concise diagnostic label for the turn's action flow."""
    chain = [action for action in executed_actions if action]
    if not chain:
        return default_label or ""
    if len(chain) <= 4:
        return " -> ".join(chain)
    return " -> ".join([*chain[:3], chain[-1]])

def _has_pending_interrupts(agent_id: str) -> bool:
    """Return whether queued interrupt-style triggers are waiting for the agent."""
    return db.has_queued_trigger_matching(
        agent_id,
        trigger_types=["human_chat", "peer_message", "watchdog_status_ping"],
    )


def _serialize_trace_value(value: Any) -> str | None:
    """Serialize structured trace content for persistence."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _build_step_trace(
    *,
    step_index: int,
    context_snapshot: str | None,
    raw_response: str | None,
    action: dict[str, Any] | None,
    result: dict[str, Any] | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    duration_ms: int,
    error: str | None = None,
) -> dict[str, Any]:
    """Create a normalized per-step execution trace record."""
    action_name = ""
    if action:
        action_name = str(action.get("action") or "")

    return {
        "step_index": step_index,
        "action_name": action_name,
        "context_snapshot": context_snapshot,
        "raw_response": raw_response,
        "parsed_action": _serialize_trace_value(action),
        "result": _serialize_trace_value(result),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "error": error,
    }


async def _finalize_turn(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model: str | None,
    model_source: str,
    initial_context_json: str | None,
    outcome: TurnOutcome,
    start: float,
) -> TurnOutcome:
    """Normalize diagnostics, refresh visible status, and return the final turn outcome."""
    activity_runtime.refresh_agent_status(agent.id)
    db.update_agent_state(agent.id, last_active_at=datetime.now(timezone.utc))

    diag = db.create_diagnostic(
        agent_id=agent.id,
        agent_name=agent.name,
        trigger_type=trigger_type,
        trigger_data=json.dumps(trigger),
        status=outcome.diagnostic_status,
        mode=mode,
        model=model,
        model_source=model_source,
        context=initial_context_json,
        raw_response=outcome.raw_response,
        action_name=outcome.action_summary,
        parsed_action=json.dumps(outcome.action) if outcome.action else None,
        result=json.dumps(outcome.result, default=str),
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        total_tokens=outcome.total_tokens,
        error=outcome.diagnostic_error,
        duration_ms=int((time.monotonic() - start) * 1000),
        steps=outcome.steps,
    )
    await manager.broadcast_diagnostic(diag)

    logger.info(
        "Turn complete for %s: trigger_status=%s, tokens=%d, %dms",
        agent.name,
        outcome.trigger_status,
        outcome.total_tokens,
        int((time.monotonic() - start) * 1000),
    )
    return outcome
