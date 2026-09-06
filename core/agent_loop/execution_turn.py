"""Run one execution turn: LLM action loop, CLI, guardian, finalize."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.actions import TERMINAL_ACTIONS, execute_action, parse_action
from core.agent_loop.activity_scheduler import plan_post_turn_follow_up
from core.agent_loop.guardian import check_no_progress, check_post_action
from core.agent_loop.liveness import record_action_liveness
from core.agent_loop.notifications import emit_chat_notifications
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.turn_helpers import (
    _build_continuation_instruction,
    _build_execution_repair_messages,
    _build_managed_writer_progress_reporter,
    _build_step_trace,
    _cli_result_to_turn_result,
    _finalize_turn,
    _has_pending_interrupts,
    _render_loop_prompt,
    _serialize_trace_value,
    _summarize_action_chain,
)
from core.agent_loop.turn_rules import (
    should_end_turn_after_action,
    validate_action_for_turn,
)
from core.bm_cli.managed_writer import (
    is_managed_batch_write_request,
    is_managed_section_rewrite_request,
    is_managed_write_request,
    run_managed_batch_write,
    run_managed_section_rewrite,
    run_managed_write,
)
from core.bm_cli.results import cli_approval_result_messages, cli_continuation_messages
from core.default_prompts import load_default_prompt
from core.llm import client
from core.models import Agent, AgentState
from core.runtime.events import runtime_events as manager

logger = logging.getLogger(__name__)

_MAX_EXECUTION_REPAIR_ATTEMPTS = 2


async def _run_execution_turn(
    *,
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model: str,
    model_source: str,
    api_config: dict[str, Any],
    context: list[dict[str, str]],
    initial_context_json: str,
    initial_activity,
    policy,
    start: float,
) -> TurnOutcome:
    """Handle CLI-approval resume and the multi-step execution action loop."""
    # 3b. Handle cli_approval_resolved / host_path_consent_resolved resume
    if trigger_type == "cli_approval_resolved":
        approval_payload = trigger.get("payload") or {}
        if isinstance(approval_payload, str):
            try:
                approval_payload = json.loads(approval_payload)
            except (json.JSONDecodeError, TypeError):
                approval_payload = {}
        approval_status = approval_payload.get("status", "rejected")
        if approval_status == "approved":
            from core.bm_cli.runtime import execute_approved_command
            cli_result = execute_approved_command(
                agent,
                state,
                approval_payload.get("command", ""),
                approval_payload.get("content"),
                approval_request_id=approval_payload.get("approval_request_id", ""),
                cwd=approval_payload.get("cwd"),
                trigger_type=trigger_type,
            )
            approval_context_msg = cli_result.prompt_content
        else:
            note = approval_payload.get("decision_note") or "No reason given."
            cmd = approval_payload.get("command", "unknown")
            approval_context_msg = _render_loop_prompt(
                "internal_loop_approval_rejected_result",
                command=cmd,
                reason=note,
            )
        context.extend(
            cli_approval_result_messages(
                approval_context_msg=approval_context_msg,
                followup_content=load_default_prompt("internal_loop_approval_review_followup"),
            )
        )

    if trigger_type == "host_path_consent_resolved":
        consent_payload = trigger.get("payload") or {}
        if isinstance(consent_payload, str):
            try:
                consent_payload = json.loads(consent_payload)
            except (json.JSONDecodeError, TypeError):
                consent_payload = {}
        consent_status = consent_payload.get("status", "denied")
        if consent_status in {"allowed_once", "always_allowed"}:
            from core.bm_cli.host_path_consent import is_request_host_access_command
            from core.bm_cli.runtime import execute_bm_cli

            command = consent_payload.get("command", "")
            if is_request_host_access_command(command):
                path = consent_payload.get("path") or "the requested host path"
                approval_context_msg = (
                    f"Host-path access granted for {path}. Use cli on that path now."
                )
            else:
                cli_result = execute_bm_cli(
                    agent,
                    state,
                    command,
                    consent_payload.get("content"),
                    trigger_type=trigger_type,
                )
                approval_context_msg = cli_result.prompt_content
        else:
            note = consent_payload.get("decision_note") or "Host-path access denied."
            cmd = consent_payload.get("command", "unknown")
            approval_context_msg = _render_loop_prompt(
                "internal_loop_approval_rejected_result",
                command=cmd,
                reason=note,
            )
        context.extend(
            cli_approval_result_messages(
                approval_context_msg=approval_context_msg,
                followup_content=load_default_prompt("internal_loop_approval_review_followup"),
            )
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
    execution_repair_attempts = 0

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
        step_prompt_tokens = response.prompt_tokens
        step_completion_tokens = response.completion_tokens
        step_total_tokens = response.total_tokens

        # Parse action
        action = parse_action(response.content)
        action_name = action["action"]

        active_task_id = activity_runtime.get_active_task_id(agent.id)
        active_activity = activity_runtime.get_active_activity(agent.id)

        # Handle parse failure
        if action_name == "_parse_failed":
            logger.warning("Parse failure for %s: %s", agent.name, action.get("_raw_snippet", ""))
            if execution_repair_attempts < _MAX_EXECUTION_REPAIR_ATTEMPTS:
                execution_repair_attempts += 1
                continuation_messages = _build_execution_repair_messages(
                    parsed_error=action.get("_raw_snippet", ""),
                )
                result = {
                    "event": "execution_repair_requested",
                    "detail": "Execution action JSON was invalid; asked the model to correct it.",
                    "agent_name": agent.name,
                }
                step_traces.append(
                    _build_step_trace(
                        step_index=action_count,
                        context_snapshot=prompt_delta,
                        raw_response=last_response_content,
                        action=action,
                        result=result,
                        prompt_tokens=step_prompt_tokens,
                        completion_tokens=step_completion_tokens,
                        total_tokens=step_total_tokens,
                        duration_ms=int((time.monotonic() - step_started) * 1000),
                        error=f"Failed to parse action JSON: {action.get('_raw_snippet', '')}",
                    )
                )
                context.extend(
                    [{"role": "assistant", "content": response.content}, *continuation_messages]
                )
                next_step_delta = _serialize_trace_value(continuation_messages)
                continue

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
                            prompt_tokens=step_prompt_tokens,
                            completion_tokens=step_completion_tokens,
                            total_tokens=step_total_tokens,
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
                            prompt_tokens=step_prompt_tokens,
                            completion_tokens=step_completion_tokens,
                            total_tokens=step_total_tokens,
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

        active_activity_before_action = active_activity

        # Execute action
        if action_name == "bm_cli":
            cli_command = str(action.get("command") or "")
            cli_content = action.get("content") if isinstance(action.get("content"), str) else None
            managed_write = None
            progress_reporter = _build_managed_writer_progress_reporter(agent, task_id=active_task_id)
            if is_managed_write_request(cli_command, cli_content):
                managed_write = await run_managed_write(
                    agent=agent,
                    state=state,
                    command=cli_command,
                    model=model,
                    api_config=api_config,
                    base_context=context,
                    action_response=response.content,
                    trigger_type=trigger_type,
                    progress_callback=progress_reporter,
                )
            elif is_managed_batch_write_request(cli_command, cli_content):
                managed_write = await run_managed_batch_write(
                    agent=agent,
                    state=state,
                    command=cli_command,
                    content=cli_content or "",
                    model=model,
                    api_config=api_config,
                    base_context=context,
                    action_response=response.content,
                    trigger_type=trigger_type,
                    progress_callback=progress_reporter,
                )
            elif is_managed_section_rewrite_request(cli_command, cli_content):
                managed_write = await run_managed_section_rewrite(
                    agent=agent,
                    state=state,
                    command=cli_command,
                    content=cli_content or "",
                    model=model,
                    api_config=api_config,
                    base_context=context,
                    action_response=response.content,
                    trigger_type=trigger_type,
                    progress_callback=progress_reporter,
                )
            if managed_write is not None:
                total_prompt_tokens += managed_write.prompt_tokens
                total_completion_tokens += managed_write.completion_tokens
                total_tokens += managed_write.total_tokens
                step_prompt_tokens += managed_write.prompt_tokens
                step_completion_tokens += managed_write.completion_tokens
                step_total_tokens += managed_write.total_tokens
                result = _cli_result_to_turn_result(agent, managed_write.cli_result)
            else:
                result = await execute_action(action, agent, state, trigger, token_model=response.model)
        else:
            result = await execute_action(action, agent, state, trigger, token_model=response.model)
        active_task_id = activity_runtime.get_active_task_id(agent.id)
        if result.get("trigger_requests"):
            scheduled_triggers.extend(result["trigger_requests"])

        # Broadcast action result immediately when the world changed
        if not result.get("suppress_world_broadcast"):
            await manager.broadcast_world_state()

        thought_text = action.get("thought", "")
        if thought_text:
            await manager.broadcast_thought(
                agent_id=agent.id, thought=thought_text, action_name=action_name,
            )

        if not result.get("suppress_activity_broadcast"):
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
                message_type=chat_message.get("message_type"),
                message_id=chat_message.get("message_id"),
                created_at=chat_message.get("created_at"),
                desk_path=chat_message.get("desk_path"),
            )
        meeting_message = result.get("meeting_message")
        if meeting_message:
            await manager.broadcast_meeting_message(
                agent_id=meeting_message["agent_id"],
                session_id=meeting_message["session_id"],
                content=meeting_message["content"],
                author_type=meeting_message["author_type"],
                author_name=meeting_message["author_name"],
                message_id=meeting_message.get("message_id"),
                created_at=meeting_message.get("created_at"),
            )
        channel_message = result.get("channel_message")
        if channel_message:
            await manager.broadcast_channel_message(
                channel_id=channel_message["channel_id"],
                content=channel_message["content"],
                author_type=channel_message["author_type"],
                author_name=channel_message["author_name"],
                author_agent_id=channel_message.get("author_agent_id"),
                message_id=channel_message.get("message_id"),
                created_at=channel_message.get("created_at"),
            )

        await emit_chat_notifications(
            agent=agent,
            trigger=trigger,
            active_activity=active_activity_before_action,
            action=action,
            result=result,
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
                            prompt_tokens=step_prompt_tokens,
                            completion_tokens=step_completion_tokens,
                            total_tokens=step_total_tokens,
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
                            prompt_tokens=step_prompt_tokens,
                            completion_tokens=step_completion_tokens,
                            total_tokens=step_total_tokens,
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
                prompt_tokens=step_prompt_tokens,
                completion_tokens=step_completion_tokens,
                total_tokens=step_total_tokens,
                duration_ms=int((time.monotonic() - step_started) * 1000),
            )
        )

        # Terminal lifecycle actions only end the turn when they succeeded.
        # Validation-style feedback (for example missing deliverables before
        # `done`) should keep the same turn alive so the model can correct it.
        if action_name in TERMINAL_ACTIONS and result.get("event") not in {"world_feedback", "agent_error"}:
            break

        # Walk action — loop ends (movement is async via simulation)
        if action_name == "walkTo" and result.get("path"):
            break

        # Approval-required or host-path consent — turn ends, human decides
        if action_name in {"bm_cli", "request_host_access"} and (
            result.get("approval_required") or result.get("consent_required")
        ):
            break

        if should_end_turn_after_action(
            action,
            policy,
            active_activity.kind if active_activity else None,
            result,
        ):
            break

        if _has_pending_interrupts(agent.id):
            break

        # Non-terminal action — feed result back and continue
        if action_name == "bm_cli" and result.get("cli_prompt_content"):
            continuation_messages = cli_continuation_messages(
                assistant_content=response.content,
                cli_prompt_content=result["cli_prompt_content"],
                followup_content=load_default_prompt("internal_loop_execution_cli_followup"),
            )
        else:
            continuation_messages = [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": _build_continuation_instruction(
                        result=result,
                        action_name=action_name,
                        active_activity_kind=active_activity.kind if active_activity else None,
                    ),
                },
            ]
        context.extend(continuation_messages)
        next_step_delta = _serialize_trace_value(continuation_messages)

    final_activity = activity_runtime.get_active_activity(agent.id)
    if trigger_type == "host_path_consent_resolved":
        import db

        db.consume_turn_once_grants(agent.id)

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
