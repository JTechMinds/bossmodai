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
from core.agent_loop.notifications import emit_chat_notifications
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.turn_context import _DECISION_TRIGGER_TYPES
from core.agent_loop.turn_helpers import (
    _build_decision_repair_messages,
    _build_managed_writer_progress_reporter,
    _build_step_trace,
    _cli_result_to_turn_result,
    _finalize_turn,
    _serialize_trace_value,
    _summarize_action_chain,
)
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

_MAX_DECISION_REPAIR_ATTEMPTS = 2


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
            error = f"Failed to parse decision JSON: {parsed.get('_raw_snippet', '')}"
            if decision_repair_attempts < _MAX_DECISION_REPAIR_ATTEMPTS:
                decision_repair_attempts += 1
                continuation_messages = _build_decision_repair_messages(
                    parsed_error=parsed.get("_raw_snippet", ""),
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

            result = {
                "event": "agent_error",
                "detail": f"{agent.name} returned invalid decision JSON",
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
                    error=error,
                    action=parsed,
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
                            action=parsed,
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
                    channel_id=trigger.get("channel_id") if isinstance(trigger.get("channel_id"), str) else None,
                )
            if cli_call.thought:
                await manager.broadcast_thought(
                    agent_id=agent.id,
                    thought=cli_call.thought,
                    action_name="bm_cli",
                )

            cli_turn_result = {
                **_cli_result_to_turn_result(agent, cli_result),
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
            if cli_turn_result.get("consent_required"):
                return await _finalize_host_path_consent_pause(
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
                return await _finalize_host_path_consent_pause(
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


async def _finalize_host_path_consent_pause(
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
    """Project the consent card and end the decision turn for the operator."""
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
            action_summary=_summarize_action_chain(executed_actions, "request_host_access"),
            raw_response=last_response_content,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            steps=step_traces,
        ),
        start=start,
    )
