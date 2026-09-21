"""Run one structured decision turn: parse, optional CLI lookup, apply."""

from __future__ import annotations

import time
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action
from core.agent_loop.decision_contract import (
    ConversationDecision,
    parse_direct_turn_response,
    validate_decision_for_trigger,
)
from core.agent_loop.decision_peek import DecisionPeekBudget
from core.agent_loop.decision_runtime import apply_decision, summarize_decision
from core.agent_loop.next_owner import NUDGE_FEEDBACK_CODE, next_owner_nudge_continuation
from core.agent_loop.notifications import broadcast_origin_status_messages, emit_chat_notifications
from core.agent_loop.decision_parse_fail import (
    decision_repair_attempt_limit,
    surface_decision_parse_failure,
    surface_llm_timeout_failure,
)
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.turn_context import _DECISION_TRIGGER_TYPES
from core.agent_loop.parse_steer import (
    classify_json_parse_failure,
    parse_failure_should_repair,
    parse_failure_steer,
)
from core.agent_loop.turn_helpers import (
    _build_decision_repair_messages,
    _build_decision_timeout_repair_messages,
    _build_managed_writer_progress_reporter,
    _build_step_trace,
    _cli_result_to_turn_result,
    _finalize_turn,
    _serialize_trace_value,
    _summarize_action_chain,
)
from core.agent_loop.task_origins import consent_origin_channel_id
from core.bm_cli import BossModCliCall, execute_bm_cli
from core.bm_cli.host_path_consent import HostAccessCall
from core.bm_cli.managed_writer import (
    is_managed_batch_write_request,
    is_managed_section_rewrite_request,
    is_managed_write_request,
    run_managed_batch_write,
    run_managed_section_rewrite,
    run_managed_write,
)
from core.bm_cli.results import cli_continuation_messages
from core.default_prompts import load_default_prompt
from core.llm import client
from core.models import Agent, AgentState
from core.runtime.events import runtime_events as manager

def _is_decision_turn(trigger: dict[str, Any]) -> bool:
    """Return whether the trigger should use the direct-request decision contract."""
    return trigger.get("type") in _DECISION_TRIGGER_TYPES

async def _run_decision_turn(
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
    initial_task_id: str | None,
    start: float,
) -> TurnOutcome:
    """Handle a single-turn direct request by producing a structured decision."""
    step_traces: list[dict[str, Any]] = []
    executed_actions: list[str] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    current_context = list(context)
    next_context_snapshot: str | None = None
    peek_budget = DecisionPeekBudget()
    last_response_content = ""
    decision_repair_attempts = 0

    while True:
        step_started = time.monotonic()

        try:
            response = await client.completion(
                model=model,
                messages=current_context,
                api_base=api_config.get("api_base"),
                api_key=api_config.get("api_key"),
                extra_body=api_config.get("extra_body"),
            )
        except client.LLMTimeoutError as exc:
            if parse_failure_should_repair(
                kind="llm_timeout",
                repair_attempts=decision_repair_attempts,
                max_repairs=decision_repair_attempt_limit(),
                decision=True,
            ):
                decision_repair_attempts += 1
                continuation_messages = _build_decision_timeout_repair_messages(
                    timeout_seconds=exc.timeout_seconds,
                )
                step_traces.append(
                    _build_step_trace(
                        step_index=len(step_traces) + 1,
                        context_snapshot=next_context_snapshot,
                        raw_response=None,
                        action=None,
                        result={
                            "event": "decision_repair_requested",
                            "detail": "LLM call timed out; asked the model to retry with one JSON envelope.",
                        },
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        duration_ms=int((time.monotonic() - step_started) * 1000),
                        error=str(exc),
                    )
                )
                current_context.extend(continuation_messages)
                next_context_snapshot = _serialize_trace_value(continuation_messages)
                continue

            return await _finish_decision_recovery(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                executed_actions=executed_actions,
                raw_response=last_response_content,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                step_traces=step_traces,
                next_context_snapshot=next_context_snapshot,
                step_started=step_started,
                step_prompt_tokens=0,
                step_completion_tokens=0,
                step_total_tokens=0,
                error=_timeout_recovery_error(exc),
                surfaced=surface_llm_timeout_failure(agent=agent, trigger=trigger),
                action=None,
                flags={"llm_timeout": True},
                start=start,
            )
        except client.LLMError as exc:
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
                    action=None,
                    action_summary=_summarize_action_chain(executed_actions, ""),
                    raw_response=last_response_content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=len(step_traces) + 1,
                            context_snapshot=next_context_snapshot,
                            raw_response=None,
                            action=None,
                            result=result,
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=str(exc),
                        )
                    ],
                ),
                start=start,
            )

        total_prompt_tokens += response.prompt_tokens
        total_completion_tokens += response.completion_tokens
        total_tokens += response.total_tokens
        last_response_content = response.content
        step_prompt_tokens = response.prompt_tokens
        step_completion_tokens = response.completion_tokens
        step_total_tokens = response.total_tokens

        parsed = parse_direct_turn_response(response.content)
        if parsed.get("decision") == "_parse_failed":
            parse_kind = parsed.get("_parse_kind") or classify_json_parse_failure(
                response.content
            )
            error = parse_failure_steer(parse_kind, parsed.get("_raw_snippet", ""))
            if parse_failure_should_repair(
                kind=parse_kind,
                repair_attempts=decision_repair_attempts,
                max_repairs=decision_repair_attempt_limit(),
                decision=True,
            ):
                decision_repair_attempts += 1
                continuation_messages = _build_decision_repair_messages(
                    parsed_error=parsed.get("_raw_snippet", ""),
                    kind=str(parse_kind or ""),
                )
                step_traces.append(
                    _build_step_trace(
                        step_index=len(step_traces) + 1,
                        context_snapshot=next_context_snapshot,
                        raw_response=response.content,
                        action=parsed,
                        result={
                            "event": "decision_repair_requested",
                            "detail": "Conversation decision JSON was invalid; asked the model to correct it.",
                        },
                        prompt_tokens=step_prompt_tokens,
                        completion_tokens=step_completion_tokens,
                        total_tokens=step_total_tokens,
                        duration_ms=int((time.monotonic() - step_started) * 1000),
                    )
                )
                current_context.extend(
                    [{"role": "assistant", "content": response.content}, *continuation_messages]
                )
                next_context_snapshot = _serialize_trace_value(continuation_messages)
                continue

            return await _finish_decision_recovery(
                agent=agent,
                trigger=trigger,
                trigger_type=trigger_type,
                mode=mode,
                model=model,
                model_source=model_source,
                initial_context_json=initial_context_json,
                executed_actions=executed_actions,
                raw_response=response.content,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                step_traces=step_traces,
                next_context_snapshot=next_context_snapshot,
                step_started=step_started,
                step_prompt_tokens=step_prompt_tokens,
                step_completion_tokens=step_completion_tokens,
                step_total_tokens=step_total_tokens,
                error=error,
                surfaced=surface_decision_parse_failure(agent=agent, trigger=trigger),
                action=parsed,
                flags={"parse_steer": True},
                start=start,
            )

        cli_call = BossModCliCall.model_validate(parsed) if parsed.get("action") == "bm_cli" else None
        if cli_call is not None:
            executed_actions.append("bm_cli")
            peek_verdict = peek_budget.consider(cli_call.command, cli_call.content)
            if not peek_verdict.allowed:
                result = {
                    "event": "agent_error",
                    "detail": f"{agent.name} {peek_verdict.steer}",
                    "agent_name": agent.name,
                    "peek_budget": peek_verdict.reason,
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
                        error=peek_verdict.steer,
                        action=cli_call.model_dump(),
                        action_summary=_summarize_action_chain(executed_actions, ""),
                        raw_response=response.content,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        steps=step_traces,
                    ),
                    start=start,
                )

            managed_write = None
            progress_reporter = _build_managed_writer_progress_reporter(
                agent,
                task_id=activity_runtime.get_active_task_id(agent.id),
            )
            if is_managed_write_request(cli_call.command, cli_call.content):
                managed_write = await run_managed_write(
                    agent=agent,
                    state=state,
                    command=cli_call.command,
                    model=model,
                    api_config=api_config,
                    base_context=current_context,
                    action_response=response.content,
                    trigger_type=trigger_type,
                    channel_id=consent_origin_channel_id(trigger),
                    progress_callback=progress_reporter,
                )
            elif is_managed_batch_write_request(cli_call.command, cli_call.content):
                managed_write = await run_managed_batch_write(
                    agent=agent,
                    state=state,
                    command=cli_call.command,
                    content=cli_call.content or "",
                    model=model,
                    api_config=api_config,
                    base_context=current_context,
                    action_response=response.content,
                    trigger_type=trigger_type,
                    progress_callback=progress_reporter,
                )
            elif is_managed_section_rewrite_request(cli_call.command, cli_call.content):
                managed_write = await run_managed_section_rewrite(
                    agent=agent,
                    state=state,
                    command=cli_call.command,
                    content=cli_call.content or "",
                    model=model,
                    api_config=api_config,
                    base_context=current_context,
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
                cli_result = managed_write.cli_result
            else:
                cli_result = execute_bm_cli(
                    agent,
                    state,
                    cli_call.command,
                    cli_call.content,
                    trigger_type=trigger_type,
                    channel_id=consent_origin_channel_id(trigger),
                )
            if cli_call.thought:
                await manager.broadcast_thought(
                    agent_id=agent.id,
                    thought=cli_call.thought,
                    action_name="bm_cli",
                )

            cli_turn_result = {
                **_cli_result_to_turn_result(agent, cli_result, trigger=trigger),
                "command": cli_result.command,
            }
            step_traces.append(
                _build_step_trace(
                    step_index=len(step_traces) + 1,
                    context_snapshot=next_context_snapshot,
                    raw_response=response.content,
                    action=cli_call.model_dump(),
                    result=cli_turn_result,
                    prompt_tokens=step_prompt_tokens,
                    completion_tokens=step_completion_tokens,
                    total_tokens=step_total_tokens,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
            )
            if cli_turn_result.get("consent_required") or cli_turn_result.get(
                "approval_required"
            ):
                return await _finalize_origin_chrome_pause(
                    agent=agent,
                    state=state,
                    trigger=trigger,
                    trigger_type=trigger_type,
                    mode=mode,
                    model=model,
                    model_source=model_source,
                    initial_context_json=initial_context_json,
                    executed_actions=executed_actions,
                    last_response_content=response.content,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    step_traces=step_traces,
                    action=cli_call.model_dump(),
                    result=cli_turn_result,
                    start=start,
                )

            continuation_messages = cli_continuation_messages(
                assistant_content=response.content,
                cli_prompt_content=cli_result.prompt_content,
                followup_content=load_default_prompt("internal_loop_decision_cli_followup"),
                followup_role="system",
            )
            current_context.extend(continuation_messages)
            next_context_snapshot = _serialize_trace_value(continuation_messages)
            continue

        if parsed.get("action") == "request_host_access":
            host_call = HostAccessCall.model_validate(parsed)
            executed_actions.append("request_host_access")
            if host_call.thought:
                await manager.broadcast_thought(
                    agent_id=agent.id,
                    thought=host_call.thought,
                    action_name="request_host_access",
                )
            host_result = await execute_action(host_call.model_dump(), agent, state, trigger)
            step_traces.append(
                _build_step_trace(
                    step_index=len(step_traces) + 1,
                    context_snapshot=next_context_snapshot,
                    raw_response=response.content,
                    action=host_call.model_dump(),
                    result=host_result,
                    prompt_tokens=step_prompt_tokens,
                    completion_tokens=step_completion_tokens,
                    total_tokens=step_total_tokens,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
            )
            if host_result.get("consent_required"):
                return await _finalize_origin_chrome_pause(
                    agent=agent,
                    state=state,
                    trigger=trigger,
                    trigger_type=trigger_type,
                    mode=mode,
                    model=model,
                    model_source=model_source,
                    initial_context_json=initial_context_json,
                    executed_actions=executed_actions,
                    last_response_content=response.content,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    step_traces=step_traces,
                    action=host_call.model_dump(),
                    result=host_result,
                    start=start,
                )
            continuation_messages = cli_continuation_messages(
                assistant_content=response.content,
                cli_prompt_content=str(host_result.get("cli_prompt_content") or host_result.get("detail") or ""),
                followup_content=load_default_prompt("internal_loop_decision_cli_followup"),
                followup_role="system",
            )
            current_context.extend(continuation_messages)
            next_context_snapshot = _serialize_trace_value(continuation_messages)
            continue

        decision = ConversationDecision.model_validate(parsed)
        validation_error = validate_decision_for_trigger(
            decision,
            trigger_type=trigger_type,
            active_task_id=initial_task_id,
            trigger=trigger,
        )
        if validation_error:
            result = {
                "event": "agent_error",
                "detail": f"{agent.name} returned an invalid direct-request decision",
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
                    action=decision.model_dump(),
                    action_summary=_summarize_action_chain(executed_actions, ""),
                    raw_response=response.content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    steps=step_traces + [
                        _build_step_trace(
                            step_index=len(step_traces) + 1,
                            context_snapshot=next_context_snapshot,
                            raw_response=response.content,
                            action=decision.model_dump(),
                            result=result,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            total_tokens=response.total_tokens,
                            duration_ms=int((time.monotonic() - step_started) * 1000),
                            error=validation_error,
                        )
                    ],
                ),
                start=start,
            )

        result = apply_decision(decision.model_dump(), agent, state, trigger)
        executed_actions.append(summarize_decision(decision.model_dump()))

        if result.get("feedback_code") == NUDGE_FEEDBACK_CODE:
            if decision.thought:
                await manager.broadcast_thought(
                    agent_id=agent.id,
                    thought=decision.thought,
                    action_name=decision.decision,
                )
            await manager.broadcast_activity(
                event=result.get("event", "world_feedback"),
                detail=result.get("detail", ""),
                agent_name=result.get("agent_name"),
            )
            step_traces.append(
                _build_step_trace(
                    step_index=len(step_traces) + 1,
                    context_snapshot=next_context_snapshot,
                    raw_response=response.content,
                    action=decision.model_dump(),
                    result=result,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
            )
            continuation_messages = [
                {"role": "assistant", "content": response.content},
                *next_owner_nudge_continuation(member_names=result.get("nudge_members") or []),
            ]
            current_context.extend(continuation_messages)
            next_context_snapshot = _serialize_trace_value(continuation_messages)
            continue

        await manager.broadcast_world_state()

        if decision.thought:
            await manager.broadcast_thought(
                agent_id=agent.id,
                thought=decision.thought,
                action_name=decision.decision,
            )

        await manager.broadcast_activity(
            event=result.get("event", "decision_applied"),
            detail=result.get("detail", ""),
            agent_name=result.get("agent_name"),
            extra=result.get("activity_extra"),
        )

        if result.get("chat_message"):
            chat_message = result["chat_message"]
            await manager.broadcast_chat_message(
                agent_id=chat_message["agent_id"],
                content=chat_message["content"],
                from_type=chat_message["from_type"],
                from_name=chat_message["from_name"],
                message_type=chat_message.get("message_type"),
                message_id=chat_message.get("message_id"),
                created_at=chat_message.get("created_at"),
                notification_kind=chat_message.get("notification_kind"),
                desk_path=chat_message.get("desk_path"),
                task_id=chat_message.get("task_id"),
            )
        if result.get("meeting_message"):
            meeting_message = result["meeting_message"]
            await manager.broadcast_meeting_message(
                agent_id=meeting_message["agent_id"],
                session_id=meeting_message["session_id"],
                content=meeting_message["content"],
                author_type=meeting_message["author_type"],
                author_name=meeting_message["author_name"],
                message_id=meeting_message.get("message_id"),
                created_at=meeting_message.get("created_at"),
            )
        if result.get("round_marker"):
            round_marker = result["round_marker"]
            await manager.broadcast_channel_message(
                channel_id=round_marker["channel_id"],
                content=round_marker["content"],
                author_type=round_marker.get("author_type") or "system",
                author_name=round_marker.get("author_name") or "BossMod",
                message_id=round_marker.get("message_id"),
                created_at=round_marker.get("created_at"),
                notification_kind=round_marker.get("notification_kind"),
            )
        if result.get("channel_message"):
            channel_message = result["channel_message"]
            await manager.broadcast_channel_message(
                channel_id=channel_message["channel_id"],
                content=channel_message["content"],
                author_type=channel_message["author_type"],
                author_name=channel_message["author_name"],
                author_agent_id=channel_message.get("author_agent_id"),
                message_id=channel_message.get("message_id"),
                created_at=channel_message.get("created_at"),
                notification_kind=channel_message.get("notification_kind"),
                desk_path=channel_message.get("desk_path"),
                task_id=channel_message.get("task_id"),
            )
        await broadcast_origin_status_messages(result, agent=agent)

        step_traces.append(
            _build_step_trace(
                step_index=len(step_traces) + 1,
                context_snapshot=next_context_snapshot,
                raw_response=response.content,
                action=decision.model_dump(),
                result=result,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
                duration_ms=int((time.monotonic() - step_started) * 1000),
            )
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
                result=result,
                action=decision.model_dump(),
                action_summary=_summarize_action_chain(executed_actions, summarize_decision(decision.model_dump())),
                raw_response=response.content,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                steps=step_traces,
            ),
            start=start,
        )


def _timeout_recovery_error(exc: client.LLMTimeoutError) -> str:
    """Operator-visible steer after the timeout repair budget is spent."""
    return (
        f"The model call timed out after {exc.timeout_seconds:g}s before one JSON envelope. "
        "Retry this turn with one JSON object. "
        "Do not invent Board status or mark the task Done."
    )


async def _finish_decision_recovery(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model: str,
    model_source: str,
    initial_context_json: str,
    executed_actions: list[str],
    raw_response: str,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    total_tokens: int,
    step_traces: list[dict[str, Any]],
    next_context_snapshot: str | None,
    step_started: float,
    step_prompt_tokens: int,
    step_completion_tokens: int,
    step_total_tokens: int,
    error: str,
    surfaced: dict[str, Any],
    action: dict[str, Any] | None,
    flags: dict[str, Any],
    start: float,
) -> TurnOutcome:
    """Complete the trigger after one note and one commitment wake.

    Completing fail-closes once. The dispatcher persists the wake and does
    not retry the turn into a stall.
    """
    result: dict[str, Any] = {
        "event": "agent_error",
        "detail": error,
        "agent_name": agent.name,
        "trigger_requests": surfaced.get("trigger_requests") or [],
    }
    if surfaced.get("chat_message"):
        result["chat_message"] = surfaced["chat_message"]
    if surfaced.get("channel_message"):
        result["channel_message"] = surfaced["channel_message"]
    await manager.broadcast_activity(
        event=result["event"],
        detail=result["detail"],
        agent_name=result["agent_name"],
    )
    await broadcast_recovery_note(agent, result)
    result.update(flags)
    return await _finalize_turn(
        agent=agent,
        trigger=trigger,
        trigger_type=trigger_type,
        mode=mode,
        model=model,
        model_source=model_source,
        initial_context_json=initial_context_json,
        outcome=TurnOutcome(
            result=result,
            trigger_status="completed",
            diagnostic_status="error",
            diagnostic_error=error,
            action=action,
            action_summary=_summarize_action_chain(executed_actions, ""),
            raw_response=raw_response,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            steps=step_traces + [
                _build_step_trace(
                    step_index=len(step_traces) + 1,
                    context_snapshot=next_context_snapshot,
                    raw_response=raw_response or None,
                    action=action,
                    result=result,
                    prompt_tokens=step_prompt_tokens,
                    completion_tokens=step_completion_tokens,
                    total_tokens=step_total_tokens,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                    error=error,
                )
            ],
        ),
        start=start,
    )


async def broadcast_recovery_note(agent: Agent, result: dict[str, Any]) -> None:
    """Broadcast the unbound origin note. Task-thread events are already persisted."""
    chat_message = result.get("chat_message")
    if isinstance(chat_message, dict):
        await manager.broadcast_chat_message(
            agent_id=chat_message.get("agent_id") or agent.id,
            content=chat_message.get("content") or "",
            from_type=chat_message.get("from_type") or "system",
            from_name=chat_message.get("from_name") or agent.name,
            message_type=chat_message.get("message_type"),
            message_id=chat_message.get("message_id"),
            created_at=chat_message.get("created_at"),
            notification_kind=chat_message.get("notification_kind"),
            desk_path=chat_message.get("desk_path"),
            task_id=chat_message.get("task_id"),
        )
    channel_message = result.get("channel_message")
    if isinstance(channel_message, dict) and channel_message.get("channel_id"):
        await manager.broadcast_channel_message(
            channel_id=channel_message["channel_id"],
            content=channel_message.get("content") or "",
            author_type=channel_message.get("author_type") or "system",
            author_name=channel_message.get("author_name") or agent.name,
            author_agent_id=channel_message.get("author_agent_id"),
            message_id=channel_message.get("message_id"),
            created_at=channel_message.get("created_at"),
            notification_kind=channel_message.get("notification_kind"),
            desk_path=channel_message.get("desk_path"),
            task_id=channel_message.get("task_id"),
        )


async def _finalize_origin_chrome_pause(
    *,
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
    trigger_type: str,
    mode: str,
    model: str,
    model_source: str,
    initial_context_json: str,
    executed_actions: list[str],
    last_response_content: str,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    total_tokens: int,
    step_traces: list[dict[str, Any]],
    action: dict[str, Any],
    result: dict[str, Any],
    start: float,
) -> TurnOutcome:
    """Project origin chrome (Approve or consent) and end the decision turn."""
    del state
    await emit_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=activity_runtime.get_active_activity(agent.id),
        action=action,
        result=result,
    )
    await manager.broadcast_activity(
        event=result.get("event", "host_path_consent_required"),
        detail=result.get("detail", ""),
        agent_name=result.get("agent_name"),
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
            result=result,
            action=action,
            action_summary=_summarize_action_chain(
                executed_actions,
                str(action.get("action") or "bm_cli"),
            ),
            raw_response=last_response_content,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            steps=step_traces,
        ),
        start=start,
    )
