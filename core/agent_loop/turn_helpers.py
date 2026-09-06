"""Shared turn helpers: repair prompts, traces, CLI results, skip/finalize."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.turn_context import _DECISION_TRIGGER_TYPES
from core.bm_cli.managed_writer import ManagedWriteProgress
from core.default_prompts import load_default_prompt, render_default_prompt
from core.models import Agent
from core.runtime.events import runtime_events as manager
import db

logger = logging.getLogger(__name__)

_LOOP_PROMPT_ALLOWED_PATHS = {"command", "reason", "parsed_error", "detail", "target"}


def _render_loop_prompt(template_key: str, **context: Any) -> str:
    """Render one centralized loop prompt block."""
    return render_default_prompt(
        template_key,
        context,
        allowed_paths=_LOOP_PROMPT_ALLOWED_PATHS,
    )

def _summarize_action_chain(executed_actions: list[str], default_label: str) -> str:
    """Render a concise diagnostic label for the turn's action flow."""
    chain = [action for action in executed_actions if action]
    if not chain:
        return default_label or ""
    if len(chain) <= 4:
        return " -> ".join(chain)
    return " -> ".join([*chain[:3], chain[-1]])

def _build_continuation_instruction(
    *,
    result: dict[str, Any],
    action_name: str,
    active_activity_kind: str | None,
) -> str:
    """Build the next-step instruction after a non-terminal execution action."""
    detail = result.get("detail", action_name)
    if active_activity_kind == "work":
        if result.get("event") == "world_feedback":
            expected_action = result.get("expected_action") or ""
            if not isinstance(expected_action, str):
                expected_action = ""
            expected_actions = result.get("expected_actions") or []
            if not isinstance(expected_actions, list):
                expected_actions = []
            expected = ", ".join([expected_action.strip(), *[str(item).strip() for item in expected_actions if str(item).strip()]])
            expected = expected.strip(", ").strip()
            return _render_loop_prompt(
                "internal_loop_execution_continue_work_world_feedback",
                detail=(detail + (f" Expected: {expected}." if expected else "")),
            )
        missing_deliverables = result.get("missing_deliverables") or []
        if missing_deliverables:
            first = missing_deliverables[0]
            target = first.get("path") or "the required deliverable"
            return _render_loop_prompt(
                "internal_loop_execution_continue_work_missing_deliverable",
                detail=detail,
                target=target,
            )
        if result.get("feedback_code") == "walk_to_desk_first":
            return _render_loop_prompt(
                "internal_loop_execution_continue_move_to_desk",
                detail=detail,
            )
        return _render_loop_prompt(
            "internal_loop_execution_continue_work_generic",
            detail=detail,
        )
    if active_activity_kind == "meeting":
        return _render_loop_prompt(
            "internal_loop_execution_continue_meeting",
            detail=detail,
        )
    if active_activity_kind == "conversation":
        return _render_loop_prompt(
            "internal_loop_execution_continue_conversation",
            detail=detail,
        )
    if active_activity_kind == "break":
        return _render_loop_prompt(
            "internal_loop_execution_continue_break",
            detail=detail,
        )
    return _render_loop_prompt(
        "internal_loop_execution_continue_generic",
        detail=detail,
    )

def _has_pending_interrupts(agent_id: str) -> bool:
    """Return whether queued interrupt-style triggers are waiting for the agent."""
    return db.has_queued_trigger_matching(
        agent_id,
        trigger_types=[
            "human_chat",
            "peer_message",
            "task_follow_up",
            "session_message",
            "session_response",
            "channel_message",
            "channel_response",
            "watchdog_status_ping",
        ],
    )

def _serialize_trace_value(value: Any) -> str | None:
    """Serialize structured trace content for persistence."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)

def _build_decision_repair_messages(*, parsed_error: str) -> list[dict[str, str]]:
    """Build one strict repair prompt after invalid conversation JSON."""
    messages = [
        {
            "role": "system",
            "content": _render_loop_prompt(
                "internal_loop_decision_repair_primary",
                parsed_error=parsed_error,
            ),
        },
        {
            "role": "system",
            "content": load_default_prompt("internal_loop_decision_repair_preserve_intent"),
        },
        {
            "role": "system",
            "content": load_default_prompt("internal_loop_decision_repair_keys"),
        },
    ]
    return messages

def _build_execution_repair_messages(*, parsed_error: str) -> list[dict[str, str]]:
    """Build one strict repair prompt after invalid execution JSON."""
    return [
        {
            "role": "system",
            "content": _render_loop_prompt(
                "internal_loop_execution_repair_primary",
                parsed_error=parsed_error,
            ),
        },
        {
            "role": "system",
            "content": load_default_prompt("internal_loop_execution_repair_keys"),
        },
        {
            "role": "user",
            "content": load_default_prompt("internal_loop_execution_repair_followup"),
        },
    ]

def _diagnostic_mode(trigger_type: str) -> str:
    """Return the user-facing diagnostics label for the completed turn."""
    if trigger_type in _DECISION_TRIGGER_TYPES:
        return "decision"
    if trigger_type == "social":
        return "social"
    return "execution"

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
        action_name = str(action.get("action") or action.get("decision") or "")

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

def _cli_result_to_turn_result(agent: Agent, cli_result) -> dict[str, Any]:
    """Convert one BossMod CLI result into the standard turn-local action result."""
    result = {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "agent_name": agent.name,
        "cli_prompt_content": cli_result.prompt_content,
        "suppress_world_broadcast": True,
        "suppress_activity_broadcast": True,
    }
    cli_data = cli_result.data or {}
    if cli_data.get("managed_writer_attempted") or cli_data.get("managed_writer_used"):
        call_count = int(cli_data.get("managed_calls") or cli_data.get("managed_chunks") or 0)
        completed = bool(cli_data.get("managed_writer_completed") or cli_data.get("managed_writer_used"))
        strategy = str(cli_data.get("managed_strategy") or "managed")
        strategy_label = strategy.replace("_", "-")
        section_count = int(cli_data.get("managed_sections") or 0)
        batch_file_count = int(cli_data.get("batch_file_count") or 0)
        section_suffix = (
            f", {section_count} section{'s' if section_count != 1 else ''}"
            if section_count > 0 and strategy == "sectioned"
            else ""
        )
        if batch_file_count > 0:
            suffix = (
                f"via batch writer ({batch_file_count} file{'s' if batch_file_count != 1 else ''}, "
                f"{call_count} call{'s' if call_count != 1 else ''}, {strategy_label}{section_suffix})"
                if completed
                else (
                    f"after batch writer attempt ({batch_file_count} file{'s' if batch_file_count != 1 else ''}, "
                    f"{call_count} call{'s' if call_count != 1 else ''}, {strategy_label}{section_suffix})"
                )
            )
        else:
            suffix = (
                f"via managed writer ({strategy_label}, {call_count} call{'s' if call_count != 1 else ''}{section_suffix})"
                if completed
                else f"after managed writer attempt ({strategy_label}, {call_count} call{'s' if call_count != 1 else ''}{section_suffix})"
            )
        result["detail"] = f"{cli_result.detail} {suffix}"
        result["managed_writer"] = {
            "attempted": True,
            "used": bool(cli_data.get("managed_writer_used")),
            "completed": completed,
            "strategy": strategy,
            "calls": call_count,
            "chunks": call_count,
            "sections": section_count,
            "bytes": int(cli_data.get("managed_bytes") or 0),
            "prompt_tokens": int(cli_data.get("managed_prompt_tokens") or 0),
            "completion_tokens": int(cli_data.get("managed_completion_tokens") or 0),
            "total_tokens": int(cli_data.get("managed_total_tokens") or 0),
        }
    if cli_data.get("batch_writer_attempted") or cli_data.get("batch_writer_used"):
        result["batch_writer"] = {
            "attempted": True,
            "used": bool(cli_data.get("batch_writer_used")),
            "completed": bool(cli_data.get("batch_writer_completed") or cli_data.get("batch_writer_used")),
            "file_count": int(cli_data.get("batch_file_count") or 0),
            "files": cli_data.get("batch_files") or [],
        }
    if getattr(cli_result, "approval_required", False):
        result["approval_required"] = True
        result["approval_request_id"] = getattr(cli_result, "approval_request_id", None)
        result["event"] = "cli_approval_required"
        result["suppress_activity_broadcast"] = False
    if getattr(cli_result, "consent_required", False):
        card = cli_data.get("host_path_consent") if isinstance(cli_data.get("host_path_consent"), dict) else {}
        path = card.get("path") or "host path"
        result["consent_required"] = True
        result["consent_request_id"] = getattr(cli_result, "consent_request_id", None)
        result["consent_reused"] = bool(cli_data.get("consent_reused"))
        result["host_path_consent"] = card
        result["event"] = "host_path_consent_required"
        result["detail"] = f"{agent.name} requests host-path access: {path}"
        result["suppress_activity_broadcast"] = False
    return result

def _build_managed_writer_progress_reporter(
    agent: Agent,
    *,
    task_id: str | None,
):
    """Return a runtime-owned reporter for managed-writer progress updates."""
    last_detail: str | None = None

    async def _report(update: ManagedWriteProgress) -> None:
        nonlocal last_detail

        detail = (update.detail or "").strip()
        if not detail:
            return

        now = datetime.now(timezone.utc)
        active = activity_runtime.get_active_work_activity(agent.id)
        if active and (task_id is None or active.task_id == task_id):
            db.update_activity(active.id, detail=detail)

        if task_id:
            fields: dict[str, Any] = {
                "status_note": detail,
                "watchdog_pinged_at": None,
                "last_heartbeat_at": now,
                "last_activity": now,
            }
            if update.counts_as_progress:
                fields["last_progress_at"] = now
            db.update_task(task_id, **fields)

        if detail == last_detail:
            return
        last_detail = detail

        await manager.broadcast_activity(
            event="managed_writer_progress",
            detail=f"{agent.name}: {detail}",
            agent_name=agent.name,
            extra={
                "stage": update.stage,
                "path": update.path,
                "file_index": update.file_index,
                "file_count": update.file_count,
                "section_index": update.section_index,
                "section_count": update.section_count,
                "strategy": update.strategy,
                "counts_as_progress": update.counts_as_progress,
            },
        )

    return _report

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

    # Do not block/stall the task or tear down work. A missing model is a
    # settings gap; the dispatcher treats skipped as completed-without-retry
    # so the trigger is not exhausted (HA-CORR-P0-03).

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
        mode=_diagnostic_mode(trigger_type),
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
