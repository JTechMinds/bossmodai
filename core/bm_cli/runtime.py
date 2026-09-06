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
from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope
from core.bm_cli.host_roots import PathOutsideRootsError, looks_like_named_absolute_path
from core.bm_cli.host_path_consent import handle_named_path_consent
from core.bm_cli.results import approval_required_result, error_result, shell_result, success_result
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.shell_executor import allowed_shell_roots, execute_shell_command
from core.bm_cli.state_commands import (
    handle_activity,
    handle_current_task,
    handle_delegated_tasks,
    handle_location,
    handle_my_board,
    handle_owned_tasks,
    handle_recent_work,
    handle_runtime,
    handle_status,
    handle_task_detail,
    handle_tasks,
    handle_waiting_on_me,
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
    "ol": handle_outline,
    "rr": handle_read_range,
    "mkdir": handle_mkdir,
    "write": handle_write,
    "append": handle_append,
    "bwrite": handle_batch_write,
    "repsect": handle_replace_section,
    "rewsect": handle_rewrite_section,
    "git": handle_git,
    "status": handle_status,
    "runtime": handle_runtime,
    "activity": handle_activity,
    "current-task": handle_current_task,
    "tasks": handle_tasks,
    "recent-work": handle_recent_work,
    "location": handle_location,
    "my-board": handle_my_board,
    "owned-tasks": handle_owned_tasks,
    "delegated-tasks": handle_delegated_tasks,
    "waiting-on-me": handle_waiting_on_me,
    "task": handle_task_detail,
    "help": handle_help,
    "categories": handle_commands,
    "fsearch": handle_fsearch,
    "learn": handle_learn,
}

VIRTUAL_COMMANDS: frozenset[str] = frozenset(_HANDLERS.keys())


def preview_bm_cli(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
) -> BossModCliResult:
    """Parse and evaluate policy without writing files or running shell.

    Used by the CLI simulator dry-run default (HA-SEC-P1-06). ``content`` is
    accepted so the request shape matches execute, but it is never applied.
    """
    del state, content
    cwd_before = get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="virtual")

    policy = evaluate_parsed_command_policy(parsed, VIRTUAL_COMMANDS, agent_id=agent.id)

    if policy.approval_required:
        return approval_required_result(
            parsed.raw,
            policy.message or f"Command requires approval: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
            matched_rule_id=policy.matched_rule_id,
        )

    if not policy.allowed:
        denied = error_result(
            parsed.raw,
            policy.message or f"Command not permitted: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
        )
        return BossModCliResult(
            command=denied.command,
            ok=False,
            detail=denied.detail,
            prompt_content=denied.prompt_content,
            kind=denied.kind,
            data=denied.data,
            cwd=denied.cwd,
            executor=denied.executor,
            exit_code=denied.exit_code,
            matched_rule_id=policy.matched_rule_id,
        )

    preview = success_result(
        command=parsed.raw,
        detail=(
            f"Dry-run: {parsed.name} would run via {policy.executor} "
            "(parse + policy only; no writes or shell)."
        ),
        kind="dry_run",
        data={
            "dry_run": True,
            "would_executor": policy.executor,
            "policy_tier": policy.tier,
        },
        sections=[
            (
                "DRY RUN",
                [
                    "No files were written and no shell command ran.",
                    f"executor: {policy.executor}",
                    f"tier: {policy.tier}",
                    "Send execute=true to run this command for real.",
                ],
            )
        ],
        cwd=cwd_before,
        executor=policy.executor,
    )
    return BossModCliResult(
        command=preview.command,
        ok=preview.ok,
        detail=preview.detail,
        prompt_content=preview.prompt_content,
        kind=preview.kind,
        data=preview.data,
        cwd=preview.cwd,
        executor=preview.executor,
        exit_code=preview.exit_code,
        matched_rule_id=policy.matched_rule_id,
    )


def execute_bm_cli(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
    *,
    trigger_type: str | None = None,
) -> BossModCliResult:
    """Execute a bounded shell-like BossMod CLI command for the given agent."""
    from core.agent_loop.activity_runtime import get_active_task_id

    cwd_before = get_cli_cwd(agent.id)
    token = host_path_consent_scope.set(
        ConsentScope(agent_id=agent.id, task_id=get_active_task_id(agent.id))
    )
    try:
        return _execute_bm_cli_inner(
            agent,
            state,
            command,
            content,
            trigger_type=trigger_type,
            cwd_before=cwd_before,
        )
    finally:
        host_path_consent_scope.reset(token)


def _execute_bm_cli_inner(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
    *,
    trigger_type: str | None = None,
    cwd_before: str,
) -> BossModCliResult:
    """Parse, authorize, and execute one CLI command inside the consent scope."""
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
    """Execute a previously-approved shell command.

    Command-tier policy is not re-evaluated (the operator already approved
    this argv), but the path jail still applies. Approval is not a jailbreak.
    """
    cwd_before = cwd or get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")

    prepared = _prepare_native_shell(agent, parsed.raw, cwd_before)
    if isinstance(prepared, BossModCliResult):
        return prepared
    shell_cwd, roots, timeout, max_output = prepared

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
    )
    if shell_exec.denied_by_path_jail:
        result = error_result(
            parsed.raw,
            shell_exec.stderr,
            cwd=cwd_before,
            executor="shell",
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor="shell",
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier="approved",
            decision="denied",
            result=result,
            trigger_type=trigger_type,
            approval_request_id=approval_request_id,
        )
        return result

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
    except PathOutsideRootsError as exc:
        raw_path = exc.raw_path or _named_path_from_command(parsed)
        if raw_path:
            from core.agent_loop.activity_runtime import get_active_task_id

            result = handle_named_path_consent(
                agent=agent,
                raw_path=raw_path,
                command=parsed.raw,
                content=content,
                cwd=cwd_before,
                task_id=get_active_task_id(agent.id),
            )
        else:
            result = error_result(parsed.raw, str(exc), cwd=cwd_before, executor=policy.executor)
    except ValueError as exc:
        result = error_result(parsed.raw, str(exc), cwd=cwd_before, executor=policy.executor)

    if result.consent_required:
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
        )
        return result

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
            consent_required=result.consent_required,
            executor=result.executor,
            exit_code=result.exit_code,
            consent_request_id=result.consent_request_id,
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


def _named_path_from_command(parsed: ParsedCliCommand) -> str | None:
    """Return the first user-named absolute path in a parsed CLI command."""
    for arg in parsed.args:
        token = str(arg).strip()
        if looks_like_named_absolute_path(token) or (
            token.startswith("/")
            and not token.startswith("/me")
            and not token.startswith("/projects")
        ):
            return token
    return None


def _prepare_native_shell(
    agent: Agent,
    command: str,
    cwd_before: str,
) -> tuple[Path, tuple[Path, ...], int, int] | BossModCliResult:
    """Resolve a real workspace cwd and path-jail roots, or return an error."""
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd_before, ".")
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")
    if resolved is None or resolved.real_path is None:
        return error_result(
            command,
            "Shell cwd is not a real workspace path",
            cwd=cwd_before,
            executor="shell",
        )
    timeout = config.get_int("cli_shell_timeout_seconds") or 30
    max_output = config.get_int("cli_shell_max_output_bytes") or 65536
    return (
        Path(resolved.real_path),
        allowed_shell_roots(agent.storage_key),
        timeout,
        max_output,
    )


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
    prepared = _prepare_native_shell(agent, parsed.raw, cwd_before)
    if isinstance(prepared, BossModCliResult):
        return prepared
    shell_cwd, roots, timeout, max_output = prepared

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
    )
    if shell_exec.denied_by_path_jail:
        result = error_result(
            parsed.raw,
            shell_exec.stderr,
            cwd=cwd_before,
            executor="shell",
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor="shell",
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

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
