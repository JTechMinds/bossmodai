"""BossMod AI — Execution/runtime support for BossMod CLI calls."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import db
from core import config
from core.bm_cli.artifacts import register_cli_artifacts
from core.bm_cli.audit import record_bm_cli_event
from core.bm_cli.fs_commands import (
    handle_append,
    handle_batch_write,
    handle_cat,
    handle_cd,
    handle_outline,
    handle_ls,
    handle_mkdir,
    handle_pwd,
    handle_read_range,
    handle_replace_section,
    handle_rewrite_section,
    handle_write,
)
from core.bm_cli.git_commands import handle_git
from core.bm_cli.help_commands import (
    handle_commands,
    handle_fsearch,
    handle_help,
    handle_learn,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policies import evaluate_parsed_command_policy
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.results import approval_required_result, error_result, shell_result
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.shell_executor import execute_shell_command
from core.bm_cli.state_commands import (
    handle_activity,
    handle_current_task,
    handle_location,
    handle_recent_work,
    handle_runtime,
    handle_status,
    handle_tasks,
)
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent, AgentState

logger = logging.getLogger(__name__)

CliHandler = Callable[[CliExecutionContext, ParsedCliCommand, str | None], BossModCliResult]

_HANDLERS: dict[str, CliHandler] = {
    "pwd": handle_pwd,
    "cd": handle_cd,
    "ls": handle_ls,
    "cat": handle_cat,
    "outline": handle_outline,
    "read-range": handle_read_range,
    "mkdir": handle_mkdir,
    "write": handle_write,
    "append": handle_append,
    "batch-write": handle_batch_write,
    "replace-section": handle_replace_section,
    "rewrite-section": handle_rewrite_section,
    "git": handle_git,
    "status": handle_status,
    "runtime": handle_runtime,
    "activity": handle_activity,
    "current-task": handle_current_task,
    "tasks": handle_tasks,
    "recent-work": handle_recent_work,
    "location": handle_location,
    "help": handle_help,
    "categories": handle_commands,
    "fsearch": handle_fsearch,
    "learn": handle_learn,
}

VIRTUAL_COMMANDS: frozenset[str] = frozenset(_HANDLERS.keys())


def execute_bm_cli(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
    *,
    trigger_type: str | None = None,
) -> BossModCliResult:
    """Execute a bounded shell-like BossMod CLI command for the given agent."""
    cwd_before = get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        result = error_result(command, str(exc), cwd=cwd_before, executor="virtual")
        record_bm_cli_event(
            agent_id=agent.id,
            command=command,
            content=content,
            executor=result.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier="parse",
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    # Evaluate policy (DB-driven, with agent-specific rules)
    policy = evaluate_parsed_command_policy(parsed, VIRTUAL_COMMANDS, agent_id=agent.id)

    # --- Approval required: create request, pause turn ---
    if policy.approval_required:
        return _handle_approval_required(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            policy=policy,
            trigger_type=trigger_type,
        )

    # --- Denied: return error ---
    if not policy.allowed:
        result = error_result(
            parsed.raw,
            policy.message or f"Command not permitted: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    # --- Virtual handler ---
    if policy.executor == "virtual":
        result = _execute_virtual(
            agent=agent,
            state=state,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            policy=policy,
            trigger_type=trigger_type,
        )
        # If virtual handler returned an "unsupported" error and shell is enabled,
        # fall through to the policy engine for shell execution.
        if not result.ok and result.kind == "error" and config.get("cli_shell_enabled") == "true":
            data = result.data or {}
            error_msg = str(data.get("error", ""))
            if "unsupported" in error_msg.lower():
                shell_policy = policy_engine.evaluate(parsed.raw, frozenset(), agent_id=agent.id)
                if shell_policy.allowed:
                    return _execute_shell(
                        agent=agent, parsed=parsed, content=content,
                        cwd_before=cwd_before, policy=shell_policy, trigger_type=trigger_type,
                    )
                if shell_policy.approval_required:
                    return _handle_approval_required(
                        agent=agent, parsed=parsed, content=content,
                        cwd_before=cwd_before, policy=shell_policy, trigger_type=trigger_type,
                    )
        return result

    # --- Shell executor ---
    if policy.executor == "shell":
        return _execute_shell(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            policy=policy,
            trigger_type=trigger_type,
        )

    # Unreachable in practice but defensive
    result = error_result(parsed.raw, f"Unknown executor: {policy.executor}", cwd=cwd_before)
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=policy.executor,
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=policy.tier,
        decision="denied",
        result=result,
        trigger_type=trigger_type,
    )
    return result


def execute_approved_command(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
    *,
    approval_request_id: str,
    cwd: str | None = None,
    trigger_type: str | None = None,
) -> BossModCliResult:
    """Execute a previously-approved shell command (bypasses policy)."""
    cwd_before = cwd or get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")

    try:
        resolved = resolve_cli_path(agent.storage_key, cwd_before, ".")
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")
    shell_cwd = Path(resolved.real_path) if resolved and resolved.real_path else Path.cwd()
    timeout = config.get_int("cli_shell_timeout_seconds") or 30
    max_output = config.get_int("cli_shell_max_output_bytes") or 65536

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
    )
    result = shell_result(
        command=parsed.raw,
        exit_code=shell_exec.exit_code,
        stdout=shell_exec.stdout,
        stderr=shell_exec.stderr,
        timed_out=shell_exec.timed_out,
        duration_ms=shell_exec.duration_ms,
        cwd=cwd_before,
    )
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor="shell",
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier="approved",
        decision="allowed",
        result=result,
        trigger_type=trigger_type,
        approval_request_id=approval_request_id,
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _handle_approval_required(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
) -> BossModCliResult:
    """Create an approval request and return the pausing result."""
    timeout_minutes = config.get_int("cli_approval_timeout_minutes") or 60
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)

    approval = db.create_cli_approval_request(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        cwd=cwd_before,
        matched_rule_id=policy.matched_rule_id,
        expires_at=expires_at,
    )

    result = approval_required_result(
        parsed.raw,
        policy.message or "Approval required.",
        cwd=cwd_before,
        executor=policy.executor,
        matched_rule_id=policy.matched_rule_id,
        approval_request_id=approval.id,
    )
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=policy.executor,
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=policy.tier,
        decision="approval_required",
        result=result,
        trigger_type=trigger_type,
        approval_request_id=approval.id,
    )
    return result


def _execute_virtual(
    *,
    agent: Agent,
    state: AgentState,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
) -> BossModCliResult:
    """Route to the virtual handler and record the audit event."""
    handler = _HANDLERS.get(parsed.name)
    if handler is None:
        result = error_result(
            parsed.raw,
            f"Unsupported command: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    try:
        result = handler(CliExecutionContext(agent=agent, state=state, cwd=cwd_before), parsed, content)
    except ValueError as exc:
        result = error_result(parsed.raw, str(exc), cwd=cwd_before, executor=policy.executor)

    artifact_ids = register_cli_artifacts(agent, result)
    if artifact_ids:
        data = dict(result.data or {})
        data["artifact_ids"] = artifact_ids
        result = BossModCliResult(
            command=result.command,
            ok=result.ok,
            detail=result.detail,
            prompt_content=result.prompt_content,
            kind=result.kind,
            data=data,
            cwd=result.cwd,
            approval_required=result.approval_required,
            executor=result.executor,
            exit_code=result.exit_code,
        )

    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=policy.executor,
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=policy.tier,
        decision="allowed",
        result=result,
        trigger_type=trigger_type,
    )
    return result


def _execute_shell(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
) -> BossModCliResult:
    """Run a native shell command and record the audit event."""
    resolved = resolve_cli_path(agent.storage_key, cwd_before, ".")
    shell_cwd = Path(resolved.real_path) if resolved and resolved.real_path else Path.cwd()
    timeout = config.get_int("cli_shell_timeout_seconds") or 30
    max_output = config.get_int("cli_shell_max_output_bytes") or 65536

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
    )
    result = shell_result(
        command=parsed.raw,
        exit_code=shell_exec.exit_code,
        stdout=shell_exec.stdout,
        stderr=shell_exec.stderr,
        timed_out=shell_exec.timed_out,
        duration_ms=shell_exec.duration_ms,
        cwd=cwd_before,
        matched_rule_id=policy.matched_rule_id,
    )
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor="shell",
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=policy.tier,
        decision="allowed",
        result=result,
        trigger_type=trigger_type,
    )
    return result
