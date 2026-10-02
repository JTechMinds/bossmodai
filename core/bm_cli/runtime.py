"""BossMod AI — Execution/runtime support for BossMod CLI calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

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
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine
from core.bm_cli.pref_commands import handle_pref
from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope
from core.bm_cli.host_roots import PathOutsideRootsError, looks_like_named_absolute_path
from core.bm_cli.host_path_consent import handle_named_path_consent, looks_like_command_flag
from core.bm_cli.results import approval_required_result, error_result, shell_result, success_result
from core.bm_cli.retry_policy import NoRetryListError, blocks_retry, load_no_retry_list
from core.bm_cli.schedule_commands import handle_schedules
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.shell_authorization import (
    ShellAuthorization,
    authorize_shell_command,
    authorize_shell_policy,
    gh_cli_authorization,
    nest_git_authorization,
    project_env_authorization,
    scoped_policy,
)
from core.bm_cli.shell_executor import allowed_shell_roots, execute_shell_command
from core.bm_cli.shell_script import is_compound
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
from core.extensions.cli_bridge import extension_handlers
from core.models import Agent, AgentState

if TYPE_CHECKING:
    from core.bm_cli.approval_gate.facts import GateSegment
    from core.bm_cli.script_runner import ScriptApproval

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
    "schedules": handle_schedules,
    "recent-work": handle_recent_work,
    "location": handle_location,
    "my-board": handle_my_board,
    "owned-tasks": handle_owned_tasks,
    "delegated-tasks": handle_delegated_tasks,
    "waiting-on-me": handle_waiting_on_me,
    "task": handle_task_detail,
    "pref": handle_pref,
    "help": handle_help,
    "categories": handle_commands,
    "fsearch": handle_fsearch,
    "learn": handle_learn,
    # One command per discovered extension; each checks at call time whether
    # its extension is enabled. Discovery collisions with the names above
    # make an extension invalid, so these never overwrite a core handler.
    **extension_handlers(),
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
    A script is decided segment by segment exactly as a real run decides it
    (:func:`core.bm_cli.script_decision.preview_script`).
    """
    del state, content
    cwd_before = get_cli_cwd(agent.id)
    if is_compound(command):
        from core.bm_cli.script_decision import preview_script

        return preview_script(agent, command, cwd_before, virtual_commands=VIRTUAL_COMMANDS)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="virtual")

    from core.agent_loop.work_binding import bound_task_id
    from core.bm_cli.locked_clone_outcome import decide_locked_clone_shell_outcome
    from core.bm_cli.nest_git_consent import maybe_block_gh_cli
    from core.bm_cli.project_env import gate_locked_clone_command

    gh_preview = maybe_block_gh_cli(
        agent=agent,
        parsed=parsed,
        content=None,
        cwd=cwd_before,
        task_id=bound_task_id(agent.id),
        channel_id=None,
        persist_chrome=False,
    )
    if gh_preview is not None:
        return gh_preview

    preview_outcome = decide_locked_clone_shell_outcome(
        agent,
        parsed,
        cwd_before,
        task_id=bound_task_id(agent.id),
        virtual_commands=VIRTUAL_COMMANDS,
    )
    if preview_outcome is not None:
        return _preview_locked_clone_outcome(preview_outcome, cwd_before)

    gated = gate_locked_clone_command(
        agent, parsed, cwd_before, task_id=bound_task_id(agent.id),
    )
    if isinstance(gated, BossModCliResult):
        return gated
    parsed = gated
    # Same evaluation as _execute_bm_cli_inner, so a dry run matches a real run.
    policy = scoped_policy(agent, parsed, cwd_before)

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
    from core.agent_loop.work_binding import bound_task_id

    cwd_before = get_cli_cwd(agent.id)
    token = host_path_consent_scope.set(
        ConsentScope(agent_id=agent.id, task_id=bound_task_id(agent.id))
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
    """Parse, authorize, and execute one CLI command inside the consent scope.

    A compound line (pipes, connectors, redirects, assignments, globs) is a
    script: :mod:`core.bm_cli.script_runner` decides every segment first.
    """
    if is_compound(command):
        from core.bm_cli.script_runner import run_script

        return run_script(
            agent, state, command,
            content=content, cwd_before=cwd_before, trigger_type=trigger_type, channel_id=channel_id,
        )
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

    # Every gate decides first; only a non-run decision creates a card,
    # chrome or record, and only when materialized here.
    auth = authorize_shell_command(agent, parsed, cwd_before, trigger_type=trigger_type)
    if auth.materialize is not None:
        return auth.materialize(content=content, channel_id=channel_id)
    parsed, policy = auth.parsed, _run_policy(auth)

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
    system_audit: str | None = None,
) -> BossModCliResult:
    """Execute a previously-approved shell command.

    Command-tier policy is not re-evaluated (the operator already approved
    this argv), but the path jail still applies. Approval is not a jailbreak.
    Host pip on a locked clone is also not an approval bypass — rewrite to
    the clone uv/venv or deny. An approved script re-checks each segment
    the same way (:func:`core.bm_cli.script_runner.run_approved_script`).
    """
    cwd_before = cwd or get_cli_cwd(agent.id)
    if is_compound(command):
        from core.bm_cli.script_runner import run_approved_script

        return run_approved_script(
            agent, state, command, content,
            approval_request_id=approval_request_id, cwd_before=cwd_before,
            trigger_type=trigger_type, channel_id=channel_id, system_audit=system_audit,
        )
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        return error_result(command, str(exc), cwd=cwd_before, executor="shell")
    # The agent's results name the command it asked for, in its /me and
    # /projects world; the rewrites below change only what runs and what the
    # audit row records.
    submitted = parsed.raw

    from core.agent_loop.work_binding import bound_task_id
    from core.bm_cli.locked_clone_outcome import prepare_locked_clone_approved

    prepared_clone = prepare_locked_clone_approved(
        agent,
        parsed,
        cwd_before,
        task_id=bound_task_id(agent.id),
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

    prepared = _prepare_native_shell(agent, parsed, cwd_before, submitted=submitted)
    if isinstance(prepared, BossModCliResult):
        return prepared
    parsed, shell_cwd, roots, timeout, max_output = prepared

    listed = _on_no_retry_list(parsed)
    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
        extra_env=_shell_extra_env(agent, parsed, cwd_before),
    )
    if shell_exec.denied_by_path_jail:
        result = _path_jail_cli_result(agent, replace(parsed, raw=submitted), cwd_before, shell_exec.stderr)
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
        command=submitted,
        exit_code=shell_exec.exit_code,
        stdout=shell_exec.stdout,
        stderr=shell_exec.stderr,
        timed_out=shell_exec.timed_out,
        duration_ms=shell_exec.duration_ms,
        cwd=cwd_before,
    )
    if system_audit:
        from core.bm_cli.approval_gate import attach_system_audit

        result = attach_system_audit(result, system_audit)
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
    return _mark_retry(result, listed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _on_no_retry_list(parsed: ParsedCliCommand) -> bool:
    """Whether ``parsed`` is on the no-retry list, decided before it runs.

    Called after every gate and immediately before the handler (virtual,
    extension bridge or shell) runs, so a ``NoRetryListError`` (the settings
    row is missing) surfaces while nothing has run yet, never after an
    outside effect.
    """
    return blocks_retry(parsed, load_no_retry_list())


def _mark_retry(result: BossModCliResult, listed: bool) -> BossModCliResult:
    """Set ``blocks_retry`` on the result of a listed command whose handler ran.

    Pure. Applied only where the handler ran, never on a parse error, deny or
    approval/consent pause. A handler error still marks it: whether an
    outside effect happened cannot be known once the handler ran.
    """
    return replace(result, blocks_retry=True) if listed else result


def _run_policy(auth: ShellAuthorization) -> CommandPolicyDecision:
    """The policy a ``run`` authorization executes under.

    Raises:
        ValueError: *auth* carries no policy (not a run decision).
    """
    if auth.policy is None:
        raise ValueError(f"{auth.kind} authorization for {auth.parsed.raw!r} has no policy")
    return auth.policy


def _paused(
    auth: ShellAuthorization | None,
    *,
    content: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Materialize a gate's non-run decision, or None when the gate passed."""
    if auth is None or auth.materialize is None:
        return None
    return auth.materialize(content=content, channel_id=channel_id)


def _record_gate_pause(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    result: BossModCliResult,
    *,
    content: str | None,
    trigger_type: str | None,
    default_tier: str,
) -> BossModCliResult:
    """Record a consent gate's card or refusal, then return it unchanged."""
    data = result.data or {}
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=result.executor,
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=str(data.get("policy_tier") or default_tier),
        decision="approval_required" if result.consent_required else "denied",
        result=result,
        trigger_type=trigger_type,
    )
    return result


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
    from core.agent_loop.work_binding import bound_task_id
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
        task_id=bound_task_id(agent.id),
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
    from core.agent_loop.work_binding import bound_task_id

    auth = nest_git_authorization(
        agent, parsed, cwd_before, task_id=bound_task_id(agent.id), trigger_type=trigger_type,
    )
    return _paused(auth, content=content, channel_id=channel_id)


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
    from core.agent_loop.work_binding import bound_task_id

    auth = gh_cli_authorization(
        agent, parsed, cwd_before, task_id=bound_task_id(agent.id), trigger_type=trigger_type,
    )
    return _paused(auth, content=content, channel_id=channel_id)


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


def _deny_locked_clone(
    agent: Agent,
    parsed: ParsedCliCommand,
    refused: ParsedCliCommand,
    cwd_before: str,
    message: str,
    *,
    result_kind: str,
    content: str | None,
    trigger_type: str | None,
) -> BossModCliResult:
    """Refuse a locked-clone command outside policy (host path, host pip).

    The result names the rewritten command (*refused*); the audit record
    keeps the command the agent sent (*parsed*).
    """
    result = error_result(
        refused.raw,
        message,
        cwd=cwd_before,
        executor="shell",
        kind=result_kind,
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


def _deny_policy(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    policy: CommandPolicyDecision,
    message: str,
    *,
    content: str | None,
    trigger_type: str | None,
) -> BossModCliResult:
    """Refuse a command policy does not permit (not never_allowed) and record it."""
    result = error_result(
        parsed.raw,
        message,
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


def _gate_locked_clone_project_env(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    *,
    content: str | None = None,
    trigger_type: str | None = None,
) -> ParsedCliCommand | BossModCliResult:
    """Rewrite or deny host pip on a locked clone, recording a deny audit event."""
    from core.agent_loop.work_binding import bound_task_id

    gated = project_env_authorization(
        agent, parsed, cwd_before, task_id=bound_task_id(agent.id), trigger_type=trigger_type,
    )
    if isinstance(gated, ParsedCliCommand):
        return gated
    paused = _paused(gated, content=content, channel_id=None)
    if paused is None:
        raise ValueError(f"project-env gate for {parsed.raw!r} blocked without a result")
    return paused


def _record_project_env_deny(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd_before: str,
    denied: BossModCliResult,
    *,
    content: str | None,
    trigger_type: str | None,
) -> BossModCliResult:
    """Record a project-env deny (host pip on a locked clone), then return it."""
    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=denied.executor,
        cwd_before=cwd_before,
        cwd_after=denied.cwd,
        policy_tier="never_allowed",
        decision="denied",
        result=denied,
        trigger_type=trigger_type,
    )
    return denied


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
    from core.bm_cli.git_argv import git_has_location_override

    # ``git -C /projects/<slug> status`` follows that project. Virtual git
    # only understands a bare subcommand and would ignore ``-C``.
    if git_has_location_override(parsed.args):
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
    auth = authorize_shell_policy(agent, parsed, cwd_before, trigger_type=trigger_type)
    if auth.materialize is not None:
        return auth.materialize(content=content, channel_id=channel_id)
    return _execute_shell(
        agent=agent,
        parsed=auth.parsed,
        content=content,
        cwd_before=cwd_before,
        policy=_run_policy(auth),
        trigger_type=trigger_type,
        channel_id=channel_id,
    )


@dataclass(frozen=True, slots=True)
class _ThreadAutoGate:
    """A finished auto-approve, or the reason a card must still be shown."""

    result: BossModCliResult | None = None
    card_why: str = ""


def _maybe_auto_approve(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
    channel_id: str | None,
    gate_segments: tuple[GateSegment, ...] = (),
) -> _ThreadAutoGate:
    """Auto-approve one approval_required command through the gate, or leave a card.

    A host-guardrail refusal is a path-jail block. System AI never sees it.
    A card keeps ``card_why`` when auto-approve is on, so the operator
    sees why it did not run the command. A script passes its
    ``gate_segments`` so the gate judges every segment.
    """
    from core.bm_cli.approval_gate import (
        audit_line,
        log_system_auto_approve,
        plan_auto_approve,
    )

    plan = plan_auto_approve(
        agent,
        parsed,
        cwd_before,
        policy_tier=str(getattr(policy, "tier", "") or ""),
        channel_id=channel_id,
        segments=gate_segments,
    )
    if plan.action == "card":
        return _ThreadAutoGate(card_why=plan.card_why)
    if plan.action == "block":
        result = _path_jail_cli_result(agent, parsed, cwd_before, plan.jail_message)
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=getattr(policy, "executor", "shell"),
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=getattr(policy, "tier", "approval_required"),
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return _ThreadAutoGate(result=result)

    from core.agent_loop.work_binding import current_turn_detached

    timeout_minutes = config.require_int("cli_approval_timeout_minutes")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    try:
        approval = db.create_cli_approval_request(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            cwd=cwd_before,
            matched_rule_id=getattr(policy, "matched_rule_id", None),
            expires_at=expires_at,
            channel_id=channel_id,
            detached_origin=current_turn_detached(agent.id),
        )
    except Exception:
        logger.exception("CLI auto-approve create failed for %s", parsed.raw)
        return _ThreadAutoGate(card_why=plan.card_why)
    line = audit_line(plan.why)
    approved = db.approve_cli_approval_request(
        approval.id,
        decision_by="system",
        decision_note=line,
    )
    if approved is None:
        return _ThreadAutoGate(card_why=plan.card_why)
    log_system_auto_approve(agent.name, f"{line} — {parsed.raw}")
    state = db.get_agent_state(agent.id)
    if state is None:
        return _ThreadAutoGate(card_why=plan.card_why)
    return _ThreadAutoGate(result=execute_approved_command(
        agent,
        state,
        parsed.raw,
        content,
        approval_request_id=approved.id,
        cwd=cwd_before,
        trigger_type=trigger_type,
        channel_id=channel_id,
        system_audit=plan.why,
    ))


def _reuse_pending_twin(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
    channel_id: str | None,
    detached_origin: bool,
) -> BossModCliResult | None:
    """Pause on an identical card the operator has not decided yet, if there is one.

    Args:
        agent: The asking agent.
        parsed: The command (or full script) as the card would show it.
        content: The CLI body, kept on the audit row.
        cwd_before: The virtual cwd the command was asked from.
        policy: The approval_required policy decision.
        trigger_type: The turn trigger, kept on the audit row.
        channel_id: The cleaned origin thread, or ``None``.
        detached_origin: Whether the asking turn is detached.

    Returns:
        The approval_required result naming the existing request, with
        ``data["reused_pending"]`` set, after one audit row for the re-ask;
        or ``None`` when no live twin exists and a card must be decided.
    """
    twin = db.find_pending_cli_approval_request(
        agent.id,
        command=parsed.raw,
        cwd=cwd_before,
        channel_id=channel_id,
        detached_origin=detached_origin,
    )
    if twin is None:
        return None
    paused = approval_required_result(
        parsed.raw,
        policy.message or "Approval required.",
        cwd=cwd_before,
        executor=policy.executor,
        matched_rule_id=policy.matched_rule_id,
        approval_request_id=twin.id,
        approval_request=twin,
    )
    result = replace(paused, data={**(paused.data or {}), "reused_pending": True})
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
        approval_request_id=twin.id,
    )
    return result


def _handle_approval_required(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd_before: str,
    policy: object,
    trigger_type: str | None,
    channel_id: str | None = None,
    script: ScriptApproval | None = None,
) -> BossModCliResult:
    """Create an approval request and return the pausing result.

    A live pending twin (same command, cwd, origin thread and detached
    origin) is reused before the auto-approve gate: the agent pauses on that
    card and nothing new is created, posted or reviewed.

    For a *script*, ``parsed.raw`` is the full script text: its segments
    already passed the project-env gate, the gate judges every segment, and
    the card's review note lists each segment's tier.
    """
    from core.bm_cli.host_path_consent import _clean_channel_id
    from core.models.channel import THREAD_ARCHIVED_CONSENT_DENY

    if script is None:
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

    from core.agent_loop.work_binding import current_turn_detached

    detached = current_turn_detached(agent.id)
    # Before the gate: while the operator is still deciding the same card,
    # System AI is not asked again and no second card is posted.
    reused = _reuse_pending_twin(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        policy=policy,
        trigger_type=trigger_type,
        channel_id=origin_channel,
        detached_origin=detached,
    )
    if reused is not None:
        return reused

    auto = _maybe_auto_approve(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd_before=cwd_before,
        policy=policy,
        trigger_type=trigger_type,
        channel_id=origin_channel,
        gate_segments=script.segments if script is not None else (),
    )
    if auto.result is not None:
        return auto.result
    notes = [script.review_note if script is not None else "", auto.card_why]
    review_note = "\n".join(note for note in notes if note)

    timeout_minutes = config.require_int("cli_approval_timeout_minutes")
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
            review_note=review_note or None,
            detached_origin=detached,
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
    from core.agent_loop.work_binding import bound_task_id

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

    # Flipped just before the handler call, so a handler that raises still counts as run.
    handler_ran = False
    listed = False
    try:
        from core.bm_cli.workspace_preference import maybe_pause_for_workspace_preference

        paused = maybe_pause_for_workspace_preference(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd=cwd_before,
            task_id=bound_task_id(agent.id),
            channel_id=channel_id,
        )
        if paused is not None:
            result = paused
        else:
            listed = _on_no_retry_list(parsed)
            handler_ran = True
            result = handler(CliExecutionContext(agent=agent, state=state, cwd=cwd_before), parsed, content)
    except NoRetryListError:
        # A ValueError, but a settings fault, not a command error: surface it
        # instead of handing it to the agent as this command's result.
        raise
    except PathOutsideRootsError as exc:
        raw_path = exc.raw_path or _named_path_from_command(parsed)
        if raw_path:
            from core.agent_loop.work_binding import bound_task_id

            result = handle_named_path_consent(
                agent=agent,
                raw_path=raw_path,
                command=parsed.raw,
                content=content,
                cwd=cwd_before,
                task_id=bound_task_id(agent.id),
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
    # A workspace-preference gate answered without running the handler.
    return _mark_retry(result, listed) if handler_ran else result


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
    *,
    submitted: str,
) -> tuple[ParsedCliCommand, Path, tuple[Path, ...], int, int] | BossModCliResult:
    """Rewrite virtual mounts, then resolve cwd, path-jail roots and shell limits.

    Args:
        agent: The agent running the command.
        parsed: The decided command; its ``/me`` and ``/projects`` tokens
            are rewritten to real paths for the executor.
        cwd_before: The virtual cwd the command runs from.
        submitted: The command as the agent asked for it, echoed by the
            error results here so they never show host paths.

    Returns:
        The rewritten command, the real cwd, the path-jail roots, and the
        timeout and output cap; or an error result when the cwd has no
        real workspace path.

    Raises:
        ConfigError: ``cli_shell_timeout_seconds`` or
            ``cli_shell_max_output_bytes`` is missing or not an int (both
            are seeded; a missing one is a bug, not a default).
    """
    from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths

    parsed = rewrite_virtual_shell_paths(agent, parsed, cwd_before)
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd_before, ".")
    except ValueError as exc:
        return error_result(submitted, str(exc), cwd=cwd_before, executor="shell")
    if resolved is None or resolved.real_path is None:
        return error_result(
            submitted,
            "Shell cwd is not a real workspace path",
            cwd=cwd_before,
            executor="shell",
        )
    timeout = config.require_int("cli_shell_timeout_seconds")
    max_output = config.require_int("cli_shell_max_output_bytes")
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
    # The result echoes the command as decided, before the /me and
    # /projects rewrite; the audit row keeps what actually ran.
    submitted = parsed.raw
    prepared = _prepare_native_shell(agent, parsed, cwd_before, submitted=submitted)
    if isinstance(prepared, BossModCliResult):
        return prepared
    parsed, shell_cwd, roots, timeout, max_output = prepared

    listed = _on_no_retry_list(parsed)
    shell_exec = execute_shell_command(
        parsed.raw,
        cwd=shell_cwd,
        timeout_seconds=timeout,
        max_output_bytes=max_output,
        allowed_roots=roots,
        extra_env=_shell_extra_env(agent, parsed, cwd_before),
    )
    if shell_exec.denied_by_path_jail:
        result = _path_jail_cli_result(agent, replace(parsed, raw=submitted), cwd_before, shell_exec.stderr)
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
        command=submitted,
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
    return _mark_retry(result, listed)
