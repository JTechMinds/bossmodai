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
from core.bm_cli.host_path_consent import handle_named_path_consent, looks_like_command_flag
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

# Virtual git is the agent /me workspace repo only. Commit/push/add and any
# git run inside a nested host-work clone must use the shell policy path.
_VIRTUAL_GIT_SUBCOMMANDS = frozenset({"status", "log", "diff", "show", "restore"})


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

    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.locked_clone_outcome import decide_locked_clone_shell_outcome
    from core.bm_cli.nest_git_consent import maybe_block_gh_cli

    gh_preview = maybe_block_gh_cli(
        agent=agent,
        parsed=parsed,
        content=None,
        cwd=cwd_before,
        task_id=get_active_task_id(agent.id),
        channel_id=None,
        persist_chrome=False,
    )
    if gh_preview is not None:
        return gh_preview

    preview_outcome = decide_locked_clone_shell_outcome(
        agent,
        parsed,
        cwd_before,
        task_id=get_active_task_id(agent.id),
        virtual_commands=VIRTUAL_COMMANDS,
    )
    if preview_outcome is not None:
        return _preview_locked_clone_outcome(preview_outcome, cwd_before)

    gated = _apply_project_env_gate(agent, parsed, cwd_before)
    if isinstance(gated, BossModCliResult):
        return gated
    parsed = gated
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
    channel_id: str | None = None,
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
            channel_id=channel_id,
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
    channel_id: str | None = None,
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

    paused = _maybe_gh_cli_block(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if paused is not None:
        return paused

    paused = _maybe_shell_executor_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if paused is not None:
        return paused

    paused = _maybe_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if paused is not None:
        return paused

    locked = _apply_locked_clone_shell_outcome(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if locked is not None:
        return locked

    gated = _gate_locked_clone_project_env(
        agent,
        parsed,
        cwd_before,
        content=content,
        trigger_type=trigger_type,
    )
    if isinstance(gated, BossModCliResult):
        return gated
    parsed = gated

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
            channel_id=channel_id,
        )

    # --- Denied: return error ---
    if not policy.allowed:
        if policy.tier == "never_allowed":
            return _deny_policy_never_allowed(
                agent=agent,
                parsed=parsed,
                content=content,
                cwd_before=cwd_before,
                policy=policy,
                trigger_type=trigger_type,
                channel_id=channel_id,
            )
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

    # --- Virtual git that belongs on the clone or is not a virtual subcommand ---
    if policy.executor == "virtual" and _use_shell_git(agent, parsed, cwd_before):
        return _execute_shell_policy(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            trigger_type=trigger_type,
            channel_id=channel_id,
        )

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
            channel_id=channel_id,
        )
        # If virtual handler returned an "unsupported" error and shell is enabled,
        # fall through to the policy engine for shell execution.
        if not result.ok and result.kind == "error" and config.get_live("cli_shell_enabled") == "true":
            data = result.data or {}
            error_msg = str(data.get("error", ""))
            if "unsupported" in error_msg.lower():
                return _execute_shell_policy(
                    agent=agent,
                    parsed=parsed,
                    content=content,
                    cwd_before=cwd_before,
                    trigger_type=trigger_type,
                    channel_id=channel_id,
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
            channel_id=channel_id,
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
    channel_id: str | None = None,
) -> BossModCliResult:
    """Execute a previously-approved shell command.

    Command-tier policy is not re-evaluated (the operator already approved
    this argv), but the path jail still applies. Approval is not a jailbreak.
    Host pip on a locked clone is also not an approval bypass — rewrite to
    the clone uv/venv or deny.
    """
    cwd_before = cwd or get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")

    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.locked_clone_outcome import prepare_locked_clone_approved

    prepared_clone = prepare_locked_clone_approved(
        agent,
        parsed,
        cwd_before,
        task_id=get_active_task_id(agent.id),
    )
    if isinstance(prepared_clone, BossModCliResult):
        return prepared_clone
    parsed = prepared_clone

    peek = policy_engine.evaluate(
        parsed.raw,
        VIRTUAL_COMMANDS,
        agent_id=agent.id,
        assume_shell=True,
        cwd=cwd_before,
    )
    if peek.tier == "never_allowed":
        return _deny_policy_never_allowed(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            policy=peek,
            trigger_type=trigger_type,
            channel_id=channel_id,
        )

    gated = _gate_locked_clone_project_env(
        agent,
        parsed,
        cwd_before,
        content=content,
        trigger_type=trigger_type,
    )
    if isinstance(gated, BossModCliResult):
        return gated
    parsed = gated

    paused = _maybe_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if paused is not None:
        return paused

    blocked = _maybe_gh_cli_block(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if blocked is not None:
        return blocked

    prepared = _prepare_native_shell(agent, parsed, cwd_before)
    if isinstance(prepared, BossModCliResult):
        return prepared
    parsed, shell_cwd, roots, timeout, max_output = prepared

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
        extra_env=_shell_extra_env(agent, parsed, cwd_before),
    )
    if shell_exec.denied_by_path_jail:
        result = _path_jail_cli_result(agent, parsed, cwd_before, shell_exec.stderr)
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

    auth_failed = _maybe_nest_git_auth_failure(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
        shell_exec=shell_exec,
    )
    if auth_failed is not None:
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor="shell",
            cwd_before=cwd_before,
            cwd_after=auth_failed.cwd,
            policy_tier="approved",
            decision="denied",
            result=auth_failed,
            trigger_type=trigger_type,
            approval_request_id=approval_request_id,
        )
        return auth_failed

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

def _maybe_shell_executor_consent(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Pause for Shell Executor Enable/Deny when a locked clone needs shell."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.shell_executor_consent import maybe_pause_for_shell_executor

    paused = maybe_pause_for_shell_executor(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd_before,
        task_id=get_active_task_id(agent.id),
        channel_id=channel_id,
        trigger_type=trigger_type,
    )
    if paused is None:
        return None
    data = paused.data or {}
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=paused.executor,
        cwd_before=cwd_before,
        cwd_after=paused.cwd,
        policy_tier=str(data.get("policy_tier") or "disabled"),
        decision="approval_required" if paused.consent_required else "denied",
        result=paused,
        trigger_type=trigger_type,
    )
    return paused


def _maybe_nest_git_auth_failure(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
    shell_exec: object,
) -> BossModCliResult | None:
    """Map interactive git auth / rejected PAT to Blocked + Nest git card bounce."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.nest_git import (
        classify_git_auth_failure,
        is_gh_cli,
        is_git_cli,
        shell_output_looks_like_git_auth_failure,
    )
    from core.bm_cli.nest_git_consent import bounce_nest_git_after_auth_failure

    del trigger_type
    if not is_git_cli(parsed) and not is_gh_cli(parsed):
        return None
    stdout = str(getattr(shell_exec, "stdout", "") or "")
    stderr = str(getattr(shell_exec, "stderr", "") or "")
    if not shell_output_looks_like_git_auth_failure(stdout, stderr):
        return None
    return bounce_nest_git_after_auth_failure(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd_before,
        task_id=get_active_task_id(agent.id),
        channel_id=channel_id,
        auth_kind=classify_git_auth_failure(stdout, stderr),
    )


def _maybe_nest_git_consent(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Pause or fail-closed for nest git auth. Always-allow does not skip this."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.nest_git_consent import maybe_pause_for_nest_git

    paused = maybe_pause_for_nest_git(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd_before,
        task_id=get_active_task_id(agent.id),
        channel_id=channel_id,
        trigger_type=trigger_type,
    )
    if paused is None:
        return None
    data = paused.data or {}
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=paused.executor,
        cwd_before=cwd_before,
        cwd_after=paused.cwd,
        policy_tier=str(data.get("policy_tier") or "nest_git"),
        decision="approval_required" if paused.consent_required else "denied",
        result=paused,
        trigger_type=trigger_type,
    )
    return paused


def _maybe_gh_cli_block(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Fail-closed one Nest git / compare-URL card for gh. No Approve spam."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.nest_git_consent import maybe_block_gh_cli

    blocked = maybe_block_gh_cli(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd_before,
        task_id=get_active_task_id(agent.id),
        channel_id=channel_id,
        trigger_type=trigger_type,
    )
    if blocked is None:
        return None
    data = blocked.data or {}
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=blocked.executor,
        cwd_before=cwd_before,
        cwd_after=blocked.cwd,
        policy_tier=str(data.get("policy_tier") or "nest_git"),
        decision="approval_required" if blocked.consent_required else "denied",
        result=blocked,
        trigger_type=trigger_type,
    )
    return blocked


def _deny_policy_never_allowed(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult:
    """Fail-closed never_allowed: operator note + agent Blocked+steer. No Approve."""
    from core.bm_cli.locked_clone_outcome import never_allowed_cli_result
    from core.bm_cli.policy_engine import CommandPolicyDecision

    decision = policy if isinstance(policy, CommandPolicyDecision) else CommandPolicyDecision(
        allowed=False,
        tier="never_allowed",
        executor="shell",
        message=getattr(policy, "message", None),
        matched_rule_id=getattr(policy, "matched_rule_id", None),
    )
    result = never_allowed_cli_result(
        agent,
        parsed,
        cwd_before,
        decision,
        channel_id=channel_id,
    )
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor="shell",
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier="never_allowed",
        decision="denied",
        result=result,
        trigger_type=trigger_type,
    )
    return result


def _apply_locked_clone_shell_outcome(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Apply the shared locked-clone shell outcome, or None for the desk path."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.locked_clone_outcome import decide_locked_clone_shell_outcome

    outcome = decide_locked_clone_shell_outcome(
        agent,
        parsed,
        cwd_before,
        task_id=get_active_task_id(agent.id),
        virtual_commands=VIRTUAL_COMMANDS,
    )
    if outcome is None:
        return None
    if outcome.kind == "never_allowed":
        if (
            outcome.policy is not None
            and outcome.policy.tier == "never_allowed"
        ):
            return _deny_policy_never_allowed(
                agent=agent,
                parsed=outcome.parsed,
                content=content,
                cwd_before=cwd_before,
                policy=outcome.policy,
                trigger_type=trigger_type,
                channel_id=channel_id,
            )
        result = error_result(
            outcome.parsed.raw,
            outcome.message or outcome.blocked_why or "Command not permitted",
            cwd=cwd_before,
            executor="shell",
            kind="host_deny" if outcome.blocked_why and "host path" in (outcome.blocked_why or "").lower() else "error",
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor="shell",
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier="never_allowed",
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result
    if outcome.kind == "approval_required":
        policy = outcome.policy
        if policy is None:
            from core.bm_cli.policy_engine import CommandPolicyDecision

            policy = CommandPolicyDecision(
                allowed=False,
                tier="approval_required",
                executor="shell",
                approval_required=True,
                message=outcome.message,
            )
        return _handle_approval_required(
            agent=agent,
            parsed=outcome.parsed,
            content=content,
            cwd_before=cwd_before,
            policy=policy,
            trigger_type=trigger_type,
            channel_id=channel_id,
        )
    policy = outcome.policy
    if policy is None:
        return None
    return _execute_shell(
        agent=agent,
        parsed=outcome.parsed,
        content=content,
        cwd_before=cwd_before,
        policy=policy,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )


def _preview_locked_clone_outcome(outcome: object, cwd_before: str) -> BossModCliResult:
    """Dry-run one locked-clone outcome without creating chrome."""
    from core.bm_cli.locked_clone_outcome import LockedCloneShellOutcome

    assert isinstance(outcome, LockedCloneShellOutcome)
    if outcome.kind == "never_allowed":
        denied = error_result(
            outcome.parsed.raw,
            outcome.message or outcome.blocked_why or "Command not permitted",
            cwd=cwd_before,
            executor="shell",
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
            matched_rule_id=getattr(outcome.policy, "matched_rule_id", None),
        )
    if outcome.kind == "approval_required":
        return approval_required_result(
            outcome.parsed.raw,
            outcome.message or "Approval required.",
            cwd=cwd_before,
            executor="shell",
            matched_rule_id=getattr(outcome.policy, "matched_rule_id", None),
        )
    policy = outcome.policy
    return success_result(
        command=outcome.parsed.raw,
        detail=(
            f"Dry-run: {outcome.parsed.name} would run via shell "
            f"({outcome.kind}; parse + policy only; no writes or shell)."
        ),
        kind="dry_run",
        data={
            "dry_run": True,
            "would_executor": "shell",
            "policy_tier": getattr(policy, "tier", outcome.kind),
            "locked_clone_outcome": outcome.kind,
        },
        sections=[
            (
                "DRY RUN",
                [
                    "No files were written and no shell command ran.",
                    "executor: shell",
                    f"outcome: {outcome.kind}",
                    f"tier: {getattr(policy, 'tier', outcome.kind)}",
                    "Send execute=true to run this command for real.",
                ],
            )
        ],
        cwd=cwd_before,
        executor="shell",
    )


def _path_jail_cli_result(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    jail_message: str,
) -> BossModCliResult:
    """Path jail is Blocked {why} with a rewrite steer, never a quiet drop."""
    from core.bm_cli.locked_clone_outcome import path_jail_blocked_result

    del agent
    return path_jail_blocked_result(parsed.raw, cwd_before, jail_message)


def _apply_project_env_gate(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
) -> ParsedCliCommand | BossModCliResult:
    """Rewrite or deny host pip on a locked clone; prefer uv/venv pytest."""
    from core.agent_loop.activity_runtime import get_active_task_id
    from core.bm_cli.project_env import gate_locked_clone_command

    return gate_locked_clone_command(
        agent,
        parsed,
        cwd_before,
        task_id=get_active_task_id(agent.id),
    )


def _gate_locked_clone_project_env(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    *,
    content: str | None = None,
    trigger_type: str | None = None,
) -> ParsedCliCommand | BossModCliResult:
    """Same as :func:`_apply_project_env_gate`, recording a deny audit event."""
    gated = _apply_project_env_gate(agent, parsed, cwd_before)
    if not isinstance(gated, BossModCliResult):
        return gated
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=gated.executor,
        cwd_before=cwd_before,
        cwd_after=gated.cwd,
        policy_tier="never_allowed",
        decision="denied",
        result=gated,
        trigger_type=trigger_type,
    )
    return gated


def _use_shell_git(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> bool:
    """Return True when this git command should use shell policy, not virtual git."""
    from core.bm_cli.nest_git import git_subcommand, is_git_cli

    if not is_git_cli(parsed):
        return False
    if config.get_live("cli_shell_enabled") != "true":
        return False
    from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo

    subcommand = git_subcommand(parsed.args)
    if subcommand not in _VIRTUAL_GIT_SUBCOMMANDS:
        return True
    return cwd_is_nested_clone_repo(agent, cwd)


def _agent_git_identity_env(agent: Agent) -> dict[str, str]:
    """Identity so shell ``git commit`` on a clone does not fail closed."""
    author_name = (agent.name or "").strip() or agent.storage_key
    author_email = f"{agent.storage_key}@bossmod.local"
    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }


def _shell_extra_env(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> dict[str, str]:
    """Agent git identity, plus nest-git auth env on the shared git/gh Shell path.

    PAT/askpass is applied for every git argv so a saved token reaches push
    even when the gate's cwd/subcommand check missed. A matching PAT is
    copied into ``GH_TOKEN`` / ``GITHUB_TOKEN`` only for a gh argv. Values
    are never logged and never returned in tool output.
    """
    extra = _agent_git_identity_env(agent)
    from core.bm_cli.nest_git import is_gh_cli, is_git_cli, nest_git_shell_env

    if is_gh_cli(parsed) or is_git_cli(parsed):
        extra.update(nest_git_shell_env(agent, parsed, cwd))
    return extra


def _execute_shell_policy(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None = None,
) -> BossModCliResult:
    """Evaluate shell policy for a command that left the virtual handler."""
    shell_policy = policy_engine.evaluate(
        parsed.raw, frozenset(), agent_id=agent.id, cwd=cwd_before,
    )
    if shell_policy.approval_required:
        return _handle_approval_required(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd_before=cwd_before,
            policy=shell_policy,
            trigger_type=trigger_type,
            channel_id=channel_id,
        )
    if not shell_policy.allowed:
        if shell_policy.tier == "never_allowed":
            return _deny_policy_never_allowed(
                agent=agent,
                parsed=parsed,
                content=content,
                cwd_before=cwd_before,
                policy=shell_policy,
                trigger_type=trigger_type,
                channel_id=channel_id,
            )
        result = error_result(
            parsed.raw,
            shell_policy.message or f"Command not permitted: {parsed.name}",
            cwd=cwd_before,
            executor=shell_policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=shell_policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=shell_policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result
    return _execute_shell(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        policy=shell_policy,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )


def _handle_approval_required(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
    channel_id: str | None = None,
) -> BossModCliResult:
    """Create an approval request and return the pausing result."""
    from core.bm_cli.host_path_consent import _clean_channel_id
    from core.models.channel import THREAD_ARCHIVED_CONSENT_DENY

    gated = _gate_locked_clone_project_env(
        agent,
        parsed,
        cwd_before,
        content=content,
        trigger_type=trigger_type,
    )
    if isinstance(gated, BossModCliResult):
        return gated
    if gated.raw != parsed.raw:
        return _execute_shell_policy(
            agent=agent,
            parsed=gated,
            content=content,
            cwd_before=cwd_before,
            trigger_type=trigger_type,
            channel_id=channel_id,
        )

    origin_channel = _clean_channel_id(channel_id)
    if origin_channel and db.is_channel_archived(origin_channel):
        return error_result(
            parsed.raw,
            THREAD_ARCHIVED_CONSENT_DENY,
            cwd=cwd_before,
            executor=getattr(policy, "executor", "shell"),
        )

    timeout_minutes = config.get_int("cli_approval_timeout_minutes") or 60
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)

    try:
        approval = db.create_cli_approval_request(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            cwd=cwd_before,
            matched_rule_id=policy.matched_rule_id,
            expires_at=expires_at,
            channel_id=origin_channel,
        )
    except Exception:
        logger.exception("CLI approval create failed for %s", parsed.raw)
        from core.agent_loop.notifications import CLI_APPROVAL_CREATE_FAIL

        return error_result(
            parsed.raw,
            CLI_APPROVAL_CREATE_FAIL,
            cwd=cwd_before,
            executor=getattr(policy, "executor", "shell"),
        )

    from core.agent_loop.notifications import (
        CLI_APPROVAL_CHROME_FAIL,
        ensure_cli_approval_chrome,
    )

    posted = ensure_cli_approval_chrome(
        agent,
        approval,
        channel_id=origin_channel,
        source_channel="channel" if origin_channel else "chat",
    )
    if not posted:
        db.reject_cli_approval_request(
            approval.id,
            decision_by="system",
            decision_note="Approval card could not be posted.",
        )
        return error_result(
            parsed.raw,
            CLI_APPROVAL_CHROME_FAIL,
            cwd=cwd_before,
            executor=getattr(policy, "executor", "shell"),
        )

    result = approval_required_result(
        parsed.raw,
        policy.message or "Approval required.",
        cwd=cwd_before,
        executor=policy.executor,
        matched_rule_id=policy.matched_rule_id,
        approval_request_id=approval.id,
        approval_request=approval,
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
    channel_id: str | None = None,
) -> BossModCliResult:
    """Route to the virtual handler and record the audit event."""
    from core.agent_loop.activity_runtime import get_active_task_id

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
        from core.bm_cli.workspace_preference import maybe_pause_for_workspace_preference

        paused = maybe_pause_for_workspace_preference(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd=cwd_before,
            task_id=get_active_task_id(agent.id),
            channel_id=channel_id,
        )
        if paused is not None:
            result = paused
        else:
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
                channel_id=channel_id,
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
        if looks_like_command_flag(token):
            continue
        if looks_like_named_absolute_path(token) or (
            token.startswith("/")
            and not token.startswith("/me")
            and not token.startswith("/projects")
        ):
            return token
    return None


def _prepare_native_shell(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
) -> tuple[ParsedCliCommand, Path, tuple[Path, ...], int, int] | BossModCliResult:
    """Rewrite virtual mounts, then resolve cwd and path-jail roots."""
    from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths

    parsed = rewrite_virtual_shell_paths(agent, parsed, cwd_before)
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd_before, ".")
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=cwd_before, executor="shell")
    if resolved is None or resolved.real_path is None:
        return error_result(
            parsed.raw,
            "Shell cwd is not a real workspace path",
            cwd=cwd_before,
            executor="shell",
        )
    timeout = config.get_int("cli_shell_timeout_seconds") or 30
    max_output = config.get_int("cli_shell_max_output_bytes") or 65536
    return (
        parsed,
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
    channel_id: str | None = None,
) -> BossModCliResult:
    """Run a native shell command and record the audit event."""
    paused = _maybe_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if paused is not None:
        return paused
    blocked = _maybe_gh_cli_block(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
    )
    if blocked is not None:
        return blocked
    prepared = _prepare_native_shell(agent, parsed, cwd_before)
    if isinstance(prepared, BossModCliResult):
        return prepared
    parsed, shell_cwd, roots, timeout, max_output = prepared

    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
        extra_env=_shell_extra_env(agent, parsed, cwd_before),
    )
    if shell_exec.denied_by_path_jail:
        result = _path_jail_cli_result(agent, parsed, cwd_before, shell_exec.stderr)
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

    auth_failed = _maybe_nest_git_auth_failure(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
        shell_exec=shell_exec,
    )
    if auth_failed is not None:
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor="shell",
            cwd_before=cwd_before,
            cwd_after=auth_failed.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=auth_failed,
            trigger_type=trigger_type,
        )
        return auth_failed

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
