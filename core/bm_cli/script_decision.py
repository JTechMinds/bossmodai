"""Decide a prepared agent script without side effects; also its dry run.

The decide half of :mod:`core.bm_cli.script_runner`: every segment goes
through the same gates as a lone command (as a native command), and the
whole line's outcome is the first block, else the first consent, else
one approval, else run. :func:`preview_script` reports that outcome for the
CLI simulator's dry run exactly as a real run would decide it, with no
card, consent, record, cd or execution.
"""

from __future__ import annotations

from core.bm_cli.effective_commands import program_selecting_env_message, selects_program
from core.bm_cli.locked_clone_outcome import path_jail_blocked_result
from core.bm_cli.policy_engine import CommandPolicyDecision
from core.bm_cli.policy_strictness import strictest
from core.bm_cli.results import render_sections
from core.bm_cli.script_prepare import PreparedScript, ScriptRefused, ScriptSegment, prepare_script
from core.bm_cli.shell_authorization import (
    ShellAuthorization,
    authorize_shell_command,
    policy_authorization,
)
from core.bm_cli.types import BossModCliResult
from core.models import Agent


def preview_script(
    agent: Agent, raw: str, cwd_before: str, *, virtual_commands: frozenset[str],
) -> BossModCliResult:
    """Dry-run a script: decide every segment as :func:`~core.bm_cli.script_runner.run_script` does, change nothing.

    Args:
        agent: The agent the simulator runs as.
        raw: The script text (``is_compound(raw)`` is true).
        cwd_before: The agent's virtual working directory.
        virtual_commands: Every virtual command name.

    Returns:
        The combined outcome with each segment's tier in the sections:
        ``ok`` only when the script would run; ``approval_required`` /
        ``consent_required`` set as a real run would pause; a refused
        construct or jail escape returns its steer, as a real run would.

    Raises:
        LookupError: No agent owns ``agent.storage_key``.
    """
    try:
        prepared = prepare_script(agent, raw, cwd_before, virtual_commands=virtual_commands)
    except ScriptRefused as exc:
        if exc.jail:
            return path_jail_blocked_result(raw, cwd_before, exc.message)
        return _preview(raw, cwd_before, "error", exc.message, [], ok=False)
    auths = authorize_segments(agent, prepared, None)
    lines = [tier_note(auths)]
    stopper = first_stop(auths)
    if stopper is not None and stopper.kind == "block":
        why = stopper.message or "Command not permitted"
        return _preview(raw, prepared.cwd, "error", why, lines, ok=False)
    if stopper is not None:
        why = stopper.message or "Consent required before this script can run"
        return _preview(raw, prepared.cwd, "consent_required", why, lines, ok=False, consent=True)
    if any(auth.kind == "approval" for auth in auths):
        policy = approval_policy(auths)
        return _preview(raw, prepared.cwd, "approval_required", policy.message or "", lines, ok=False, approval=True)
    tier = strictest([policy_of(auth) for auth in auths]).tier
    return _preview(raw, prepared.cwd, "dry_run", f"Dry-run: script would run via shell (tier {tier})", lines, ok=True)


def _preview(
    raw: str, cwd: str, kind: str, message: str, lines: list[str], *,
    ok: bool, approval: bool = False, consent: bool = False,
) -> BossModCliResult:
    sections = [("DRY RUN", [
        "No files were written and no shell command ran.", f"outcome: {kind}", message, *lines,
        "Send execute=true to run this command for real.",
    ])]
    data = {"dry_run": True, "would_executor": "shell", "outcome": kind, "message": message, "segments": lines}
    if not ok and not approval and not consent:
        data["error"] = message
    return BossModCliResult(
        command=raw, ok=ok, detail=message, prompt_content=render_sections(raw, sections), kind=kind,
        data=data, cwd=cwd, approval_required=approval, consent_required=consent, executor="shell",
        exit_code=0 if ok else (126 if approval or consent else 1),
    )


def authorize_segments(
    agent: Agent, prepared: PreparedScript, trigger_type: str | None
) -> list[ShellAuthorization]:
    """Decide every segment, in order, without side effects (the dry run shares this)."""
    return [_authorize(agent, segment, prepared.cwd, trigger_type) for segment in prepared.segments]


def first_stop(auths: list[ShellAuthorization]) -> ShellAuthorization | None:
    """The decision that stops the script: the first block, else the first consent."""
    for kind in ("block", "consent"):
        first = next((auth for auth in auths if auth.kind == kind), None)
        if first is not None:
            return first
    return None


def _authorize(agent: Agent, segment: ScriptSegment, cwd: str, trigger_type: str | None) -> ShellAuthorization:
    """One segment's decision; a program-selecting assignment is never_allowed first."""
    blocked = program_env_block(segment)
    if blocked is not None:
        return policy_authorization(agent, segment.parsed, cwd, blocked, trigger_type=trigger_type)
    return authorize_shell_command(agent, segment.parsed, cwd, trigger_type=trigger_type, native=True)


def program_env_block(segment: ScriptSegment) -> CommandPolicyDecision | None:
    """The same invariant policy applies to ``NAME=value`` through a wrapper."""
    for name, _value in segment.command.assignments:
        if selects_program(name):
            return CommandPolicyDecision(
                allowed=False,
                tier="never_allowed",
                executor="shell",
                message=f"Command blocked by policy rule: {program_selecting_env_message(name)}",
            )
    return None


def approval_policy(auths: list[ShellAuthorization]) -> CommandPolicyDecision:
    """One approval decision for the script, as strict as its strictest segment."""
    pending = [auth for auth in auths if auth.kind == "approval"]
    reasons = "; ".join(
        f"{auth.parsed.raw}: {auth.message or policy_of(auth).message or policy_of(auth).tier}"
        for auth in pending
    )
    return CommandPolicyDecision(
        allowed=False,
        tier=strictest([policy_of(auth) for auth in pending]).tier,
        executor="shell",
        approval_required=True,
        message=f"Script requires approval — {reasons}",
    )


def tier_note(auths: list[ShellAuthorization]) -> str:
    """Each segment with its tier: ``Script segments: 1) cmd [tier]; …``.

    A decision made before policy (a consent or gate block) shows its kind.
    """
    parts = [
        f"{index}) {auth.parsed.raw} [{auth.policy.tier if auth.policy is not None else auth.kind}]"
        for index, auth in enumerate(auths, start=1)
    ]
    return "Script segments: " + "; ".join(parts)


def policy_of(auth: ShellAuthorization) -> CommandPolicyDecision:
    """The decision's policy; run and approval decisions always carry one.

    Raises:
        ValueError: *auth* was decided before policy (a consent or gate block).
    """
    if auth.policy is None:
        raise ValueError(f"{auth.kind} authorization for {auth.parsed.raw!r} has no policy")
    return auth.policy
