"""Authorize every segment of an agent script, then run it once or pause it once.

A script (``a | b && c > out``) must be decided completely before any part
runs; otherwise ``a && rm x`` could run ``a`` and then stop at a card with
half the work applied. Each simple command goes through the same gates as
a lone command (:func:`~core.bm_cli.shell_authorization.authorize_shell_command`,
as a native command), and the outcome for the whole line is:

* any block: the first block's result (in source order); nothing runs;
* else any consent: that consent card; nothing runs;
* else any approval: one approval request for the full script text,
  through the approval gate, with each segment's tier on the card;
* else: the script runs.

Cards, consents and audit rows carry the full script text, so a resume
or an approval re-runs the whole script, which is decided again.
"""

from __future__ import annotations

import glob
import shlex
from dataclasses import dataclass, replace
from types import ModuleType

from core import config
from core.bm_cli.approval_gate.facts import GateSegment
from core.bm_cli.audit import record_bm_cli_event
from core.bm_cli.locked_clone_outcome import prepare_locked_clone_approved, rewrite_virtual_shell_paths
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.policy_strictness import strictest
from core.bm_cli.results import error_result, shell_result
from core.bm_cli.script_decision import (
    approval_policy,
    authorize_segments,
    first_stop,
    program_env_block,
    tier_note,
)
from core.bm_cli.script_prepare import PreparedScript, ScriptRefused, ScriptSegment, prepare_script
from core.bm_cli.shell_authorization import (
    ShellAuthorization,
    gh_cli_authorization,
    nest_git_authorization,
    project_env_authorization,
)
from core.bm_cli.shell_executor import PathJailError, assert_script_within_path_jail, execute_shell_script
from core.bm_cli.shell_script import ShellScript, SimpleCommand, Word
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models import Agent, AgentState

TIMEOUT_SETTING = "cli_shell_timeout_seconds"
MAX_OUTPUT_SETTING = "cli_shell_max_output_bytes"
_APPROVED_TIER = "approved"
_REFUSED_TIER = "parse"
_JAIL_TIER = "never_allowed"


@dataclass(frozen=True)
class ScriptApproval:
    """What the approval path needs to card or auto-approve one script.

    Attributes:
        segments: Every segment as the approval gate judges it (its facts
            are the union over these; every invariant applies to each).
        review_note: Each segment with its tier, for the operator's card.
    """

    segments: tuple[GateSegment, ...]
    review_note: str


def run_script(
    agent: Agent,
    state: AgentState,
    raw: str,
    *,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
) -> BossModCliResult:
    """Decide every segment of *raw*, then run the script or pause it once.

    Args:
        agent: The agent running the script.
        state: The agent's state (the leading ``cd`` handler needs it).
        raw: The script text (``is_compound(raw)`` is true).
        content: The CLI body, stored on cards and audit rows.
        cwd_before: The agent's virtual working directory.
        trigger_type: The turn trigger (a consent resume re-runs ungated).
        channel_id: The origin thread for cards, or None.

    Returns:
        The script's shell result, or the block, consent or approval result.
        Exactly one audit row is written for the script.

    Raises:
        ConfigError: A shell timeout or output-cap setting is missing.
        LookupError: No agent owns ``agent.storage_key``.
    """
    rt = _runtime()
    try:
        prepared = prepare_script(agent, raw, cwd_before, virtual_commands=rt.VIRTUAL_COMMANDS)
    except ScriptRefused as exc:
        return _refused(agent, raw, exc, content=content, cwd_before=cwd_before, trigger_type=trigger_type)
    auths = authorize_segments(agent, prepared, trigger_type)
    stopper = first_stop(auths)
    if stopper is not None:
        return _materialized(stopper, raw, content=content, channel_id=channel_id)
    if any(auth.kind == "approval" for auth in auths):
        return rt._handle_approval_required(
            agent=agent,
            parsed=_script_command(raw, prepared),
            content=content,
            cwd_before=cwd_before,
            policy=approval_policy(auths),
            trigger_type=trigger_type,
            channel_id=channel_id,
            script=ScriptApproval(
                tuple(
                    GateSegment(auth.parsed, prepared.cwd, segment.redirect_writes)
                    for segment, auth in zip(prepared.segments, auths, strict=True)
                ),
                tier_note(auths),
            ),
        )
    tier = strictest([rt._run_policy(auth) for auth in auths]).tier
    return _execute(
        agent, state, prepared, [auth.parsed for auth in auths], content=content, cwd_before=cwd_before,
        trigger_type=trigger_type, channel_id=channel_id, policy_tier=tier, approval_request_id=None,
    )


def run_approved_script(
    agent: Agent,
    state: AgentState,
    raw: str,
    content: str | None,
    *,
    approval_request_id: str,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
    system_audit: str | None,
) -> BossModCliResult:
    """Run an approved script after re-checking each segment as an approved command is.

    Approval does not re-decide command tiers, but per segment: the
    locked-clone rewrite, never_allowed (rules and program-selecting
    assignments), the project-env gate, nest git auth and gh auth still
    apply, and the path jail is checked again before anything runs.

    Args:
        agent: The agent running the script.
        state: The agent's state.
        raw: The approved script text.
        content: The CLI body.
        approval_request_id: The approved request, linked on the audit row.
        cwd_before: The working directory the request was made in.
        trigger_type: The turn trigger.
        channel_id: The origin thread, or None.
        system_audit: System AI's approval reason when it auto-approved.

    Returns:
        The script's shell result, or the refusal or pause that stopped it.

    Raises:
        ConfigError: A shell timeout or output-cap setting is missing.
        LookupError: No agent owns ``agent.storage_key``.
    """
    rt = _runtime()
    try:
        prepared = prepare_script(agent, raw, cwd_before, virtual_commands=rt.VIRTUAL_COMMANDS)
    except ScriptRefused as exc:
        return _refused(
            agent, raw, exc, content=content, cwd_before=cwd_before, trigger_type=trigger_type,
            approval_request_id=approval_request_id,
        )
    decided: list[ParsedCliCommand] = []
    for segment in prepared.segments:
        checked = _recheck_approved(agent, segment, prepared.cwd, raw, content, trigger_type, channel_id)
        if isinstance(checked, BossModCliResult):
            return checked
        decided.append(checked)
    return _execute(
        agent, state, prepared, decided,
        content=content, cwd_before=cwd_before, trigger_type=trigger_type, channel_id=channel_id,
        policy_tier=_APPROVED_TIER, approval_request_id=approval_request_id, system_audit=system_audit,
    )


def _recheck_approved(
    agent: Agent, segment: ScriptSegment, cwd: str, raw: str,
    content: str | None, trigger_type: str | None, channel_id: str | None,
) -> ParsedCliCommand | BossModCliResult:
    """The approved-command checks for one segment, in ``execute_approved_command`` order."""
    from core.agent_loop.work_binding import bound_task_id

    rt = _runtime()
    task_id = bound_task_id(agent.id)
    parsed = prepare_locked_clone_approved(agent, segment.parsed, cwd, task_id=task_id)
    if isinstance(parsed, BossModCliResult):
        return parsed
    peek = program_env_block(segment) or policy_engine.evaluate(
        parsed.raw, frozenset(), agent_id=agent.id, assume_shell=True, cwd=cwd,
    )
    if peek.tier == "never_allowed":
        return rt._deny_policy_never_allowed(
            agent=agent, parsed=replace(parsed, raw=raw), content=content, cwd_before=cwd,
            policy=peek, trigger_type=trigger_type, channel_id=channel_id,
        )
    gated = project_env_authorization(agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type)
    if isinstance(gated, ShellAuthorization):
        # As for a lone approved command, the project-env refusal posts nowhere.
        return _materialized(gated, raw, content=content, channel_id=None)
    for gate in (nest_git_authorization, gh_cli_authorization):
        paused = gate(agent, gated, cwd, task_id=task_id, trigger_type=trigger_type)
        if paused is not None:
            return _materialized(paused, raw, content=content, channel_id=channel_id)
    return gated


def _execute(
    agent: Agent,
    state: AgentState,
    prepared: PreparedScript,
    decided: list[ParsedCliCommand],
    *,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    channel_id: str | None,
    policy_tier: str,
    approval_request_id: str | None,
    system_audit: str | None = None,
) -> BossModCliResult:
    """Persist the leading ``cd``, run the decided script, and write its one audit row."""
    rt = _runtime()
    shown = _script_command(prepared.raw, prepared)
    script, envs = _executable(agent, prepared, decided)

    def record(result: BossModCliResult, decision: str) -> BossModCliResult:
        record_bm_cli_event(
            agent_id=agent.id, command=prepared.raw, content=content, executor="shell",
            cwd_before=cwd_before, cwd_after=result.cwd, policy_tier=policy_tier, decision=decision,
            result=result, trigger_type=trigger_type, approval_request_id=approval_request_id,
        )
        return result

    try:
        assert_script_within_path_jail(script, cwd=prepared.cwd_real, allowed_roots=prepared.roots)
    except PathJailError as exc:
        return record(rt._path_jail_cli_result(agent, shown, cwd_before, str(exc)), "denied")
    timeout = config.require_int(TIMEOUT_SETTING)
    max_output = config.require_int(MAX_OUTPUT_SETTING)
    listed = any(rt._on_no_retry_list(parsed) for parsed in decided)
    if prepared.cd is not None:
        moved = rt._HANDLERS["cd"](CliExecutionContext(agent=agent, state=state, cwd=cwd_before), prepared.cd, None)
        if not moved.ok:
            return record(replace(moved, command=prepared.raw), "denied")
    shell_exec = execute_shell_script(
        script, cwd=prepared.cwd_real, timeout_seconds=timeout, max_output_bytes=max_output,
        allowed_roots=prepared.roots, extra_env=envs,
    )
    if shell_exec.denied_by_path_jail:
        return record(rt._path_jail_cli_result(agent, shown, cwd_before, shell_exec.stderr), "denied")
    remote = next((parsed for parsed in decided if _is_git_or_gh(parsed)), None)
    if remote is not None:
        bounced = rt._maybe_nest_git_auth_failure(
            agent=agent, parsed=replace(remote, raw=prepared.raw), content=content,
            cwd_before=prepared.cwd, trigger_type=trigger_type, channel_id=channel_id, shell_exec=shell_exec,
        )
        if bounced is not None:
            return record(bounced, "denied")
    result = shell_result(
        command=prepared.raw, exit_code=shell_exec.exit_code, stdout=shell_exec.stdout, stderr=shell_exec.stderr,
        timed_out=shell_exec.timed_out, duration_ms=shell_exec.duration_ms, cwd=prepared.cwd,
    )
    if system_audit:
        from core.bm_cli.approval_gate import attach_system_audit

        result = attach_system_audit(result, system_audit)
    return rt._mark_retry(record(result, "allowed"), listed)


def _executable(
    agent: Agent, prepared: PreparedScript, decided: list[ParsedCliCommand]
) -> tuple[ShellScript, list[dict[str, str]]]:
    """The decided argv with real paths, and each command's extra env, for the executor."""
    rt = _runtime()
    commands: list[SimpleCommand] = []
    envs: list[dict[str, str]] = []
    for segment, parsed in zip(prepared.segments, decided, strict=True):
        real = rewrite_virtual_shell_paths(agent, parsed, prepared.cwd)
        argv = tuple(Word(token, False, glob.escape(token)) for token in shlex.split(real.raw))
        commands.append(SimpleCommand(segment.command.assignments, argv, segment.command.redirects))
        envs.append(rt._shell_extra_env(agent, parsed, prepared.cwd))
    return prepared.body.with_commands(commands), envs


def _materialized(auth: ShellAuthorization, raw: str, *, content: str | None, channel_id: str | None) -> BossModCliResult:
    """Materialize a non-run decision under the full script text."""
    if auth.materialize is None:
        raise ValueError(f"{auth.kind} authorization for {auth.parsed.raw!r} cannot materialize")
    return auth.materialize(content=content, channel_id=channel_id, command=raw)


def _refused(
    agent: Agent,
    raw: str,
    refusal: ScriptRefused,
    *,
    content: str | None,
    cwd_before: str,
    trigger_type: str | None,
    approval_request_id: str | None = None,
) -> BossModCliResult:
    """A steer or a jail block before any segment was decided; one audit row."""
    rt = _runtime()
    if refusal.jail:
        result = rt._path_jail_cli_result(agent, ParsedCliCommand(raw, "", ()), cwd_before, refusal.message)
    else:
        result = error_result(raw, refusal.message, cwd=cwd_before, executor="shell")
    record_bm_cli_event(
        agent_id=agent.id, command=raw, content=content, executor="shell", cwd_before=cwd_before,
        cwd_after=result.cwd, policy_tier=_JAIL_TIER if refusal.jail else _REFUSED_TIER, decision="denied",
        result=result, trigger_type=trigger_type, approval_request_id=approval_request_id,
    )
    return result


def _script_command(raw: str, prepared: PreparedScript) -> ParsedCliCommand:
    """The script as one command for cards and records: its text, named by its first segment."""
    first = prepared.segments[0].parsed
    return ParsedCliCommand(raw=raw, name=first.name, args=first.args)


def _is_git_or_gh(parsed: ParsedCliCommand) -> bool:
    from core.bm_cli.nest_git import is_gh_cli, is_git_cli

    return is_git_cli(parsed) or is_gh_cli(parsed)


def _runtime() -> ModuleType:
    """The runtime module (effect halves, handlers); it imports this one lazily."""
    from core.bm_cli import runtime

    return runtime
