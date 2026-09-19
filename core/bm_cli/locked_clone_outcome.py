"""One shared shell outcome path for locked-clone commands.

Every locked-clone shell command maps to exactly one of:

- ``allow`` — policy ``always_allowed``
- ``rewrite`` — clone-local form (virtual ``/me`` paths, uv/venv pip/pytest)
- ``approval_required`` — real request id + in-thread chrome
- ``never_allowed`` — explicit ``Blocked — {why}`` plus a steer

Silent deny is a bug. Unmatched default-deny on a locked clone becomes
approval_required so the operator gets a card, not a quiet drop.
Host writes outside the nest stay ``never_allowed``.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import db
from core.bm_cli.filesystem import agent_artifact_dir, projects_artifact_root
from core.bm_cli.host_roots import (
    PathOutsideRootsError,
    is_within_roots,
    looks_like_named_absolute_path,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine
from core.bm_cli.project_env import gate_locked_clone_command
from core.bm_cli.shell_executor import path_candidates_from_token
from core.bm_cli.shell_executor_consent import command_needs_shell_executor
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo
from core.models import Agent

OutcomeKind = Literal["allow", "rewrite", "approval_required", "never_allowed"]

LOCKED_CLONE_DEFAULT_STEER = (
    "Stay on the locked clone under /me/host-work. "
    "Use uv run pytest, .venv/bin/pytest, local git, or wait for an in-thread Approve card. "
    "Do not invent a desk deny. Do not park @Operator as an enablement switch."
)

HOST_OUTSIDE_NEST_WHY = (
    "Blocked — host path is outside the locked clone. "
    "Stay on /me/host-work. Host writes stay denied. "
    "Do not invent a desk deny. Do not park @Operator as an enablement switch."
)

PATH_JAIL_BLOCKED_WHY = (
    "Blocked — path jail. Nest paths must stay under /me/host-work after rewrite. "
    "Host writes outside the clone stay denied. "
    "Do not invent a desk deny. Do not park @Operator as an enablement switch."
)

DEFAULT_APPROVAL_MESSAGE = (
    "No matching rule on a locked clone — approval required. "
    "Wait for the in-thread Approve card. "
    "Do not invent a desk deny. Do not park @Operator as an enablement switch."
)


@dataclass(frozen=True, slots=True)
class LockedCloneShellOutcome:
    """One of the four locked-clone shell outcomes."""

    kind: OutcomeKind
    parsed: ParsedCliCommand
    policy: CommandPolicyDecision | None = None
    message: str | None = None
    blocked_why: str | None = None


def is_locked_clone_context(
    agent: Agent,
    cwd: str,
    *,
    task_id: str | None = None,
) -> bool:
    """Return True when cwd is a nest clone or the turn has a locked copy."""
    if cwd_is_nested_clone_repo(agent, cwd):
        return True
    from core.agent_loop.runtime_core import locked_workspace_copies_for_turn

    return bool(locked_workspace_copies_for_turn(agent.id, task_id))


def is_virtual_cli_path(token: str) -> bool:
    """Return True for virtual ``/me`` or ``/projects`` CLI paths."""
    cleaned = (token or "").replace("\\", "/").strip()
    return (
        cleaned == "/me"
        or cleaned.startswith("/me/")
        or cleaned == "/projects"
        or cleaned.startswith("/projects/")
    )


def rewrite_virtual_shell_paths(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
) -> ParsedCliCommand:
    """Rewrite ``/me`` and ``/projects`` argv tokens to real workspace paths."""
    tokens = [parsed.name, *parsed.args]
    changed = False
    rewritten: list[str] = []
    for token in tokens:
        mapped = _rewrite_token(agent, token, cwd)
        if mapped != token:
            changed = True
        rewritten.append(mapped)
    if not changed:
        return parsed
    return parse_cli_command(shlex.join(rewritten))


def decide_locked_clone_shell_outcome(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    task_id: str | None,
    virtual_commands: frozenset[str],
) -> LockedCloneShellOutcome | None:
    """Return the shared outcome for one locked-clone shell command.

    ``None`` means this is not a locked-clone shell command — callers keep
    the existing desk / virtual path.
    """
    if not is_locked_clone_context(agent, cwd, task_id=task_id):
        return None
    if not command_needs_shell_executor(agent, parsed, cwd):
        return None

    rewritten = False
    gated = gate_locked_clone_command(agent, parsed, cwd, task_id=task_id)
    if isinstance(gated, BossModCliResult):
        why = _blocked_line(_result_why(gated) or HOST_OUTSIDE_NEST_WHY)
        return LockedCloneShellOutcome(
            kind="never_allowed",
            parsed=parsed,
            message=why,
            blocked_why=why,
        )
    if gated.raw != parsed.raw:
        rewritten = True
    parsed = gated

    path_mapped = rewrite_virtual_shell_paths(agent, parsed, cwd)
    if path_mapped.raw != parsed.raw:
        rewritten = True
    parsed = path_mapped

    host = first_host_path_outside_nest(agent, parsed, cwd)
    if host is not None:
        return LockedCloneShellOutcome(
            kind="never_allowed",
            parsed=parsed,
            message=HOST_OUTSIDE_NEST_WHY,
            blocked_why=HOST_OUTSIDE_NEST_WHY,
        )

    policy = policy_engine.evaluate(parsed.raw, frozenset(), agent_id=agent.id, cwd=cwd)
    if policy.tier == "disabled":
        return None

    if policy.tier == "never_allowed" or (
        not policy.allowed and not policy.approval_required and policy.tier != "default"
    ):
        why = blocked_never_allowed_message(policy, parsed)
        return LockedCloneShellOutcome(
            kind="never_allowed",
            parsed=parsed,
            policy=policy,
            message=why,
            blocked_why=why,
        )

    if policy.approval_required:
        return LockedCloneShellOutcome(
            kind="approval_required",
            parsed=parsed,
            policy=policy,
            message=policy.message,
        )

    if policy.allowed:
        return LockedCloneShellOutcome(
            kind="rewrite" if rewritten else "allow",
            parsed=parsed,
            policy=policy,
        )

    del virtual_commands
    return LockedCloneShellOutcome(
        kind="approval_required",
        parsed=parsed,
        policy=_approval_from_default(parsed, policy),
        message=DEFAULT_APPROVAL_MESSAGE,
    )


def prepare_locked_clone_approved(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    task_id: str | None,
) -> ParsedCliCommand | BossModCliResult:
    """Rewrite clone-local form after approval. Approval is not a jailbreak."""
    if not is_locked_clone_context(agent, cwd, task_id=task_id):
        return parsed
    gated = gate_locked_clone_command(agent, parsed, cwd, task_id=task_id)
    if isinstance(gated, BossModCliResult):
        return gated
    parsed = rewrite_virtual_shell_paths(agent, gated, cwd)
    if first_host_path_outside_nest(agent, parsed, cwd) is not None:
        from core.bm_cli.results import error_result

        return error_result(
            parsed.raw,
            HOST_OUTSIDE_NEST_WHY,
            cwd=cwd,
            executor="shell",
            kind="host_deny",
        )
    return parsed


def path_jail_blocked_result(command: str, cwd: str | None) -> BossModCliResult:
    """Convert a quiet path-jail drop into an explicit Blocked {why}."""
    from core.bm_cli.results import error_result

    return error_result(
        command,
        PATH_JAIL_BLOCKED_WHY,
        cwd=cwd,
        executor="shell",
        kind="host_deny",
    )


def first_host_path_outside_nest(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
) -> str | None:
    """Return the first named host path that is not inside the nest/workspace."""
    roots = _workspace_roots(agent)
    for token in parsed.args:
        for candidate in path_candidates_from_token(token):
            if is_virtual_cli_path(candidate):
                continue
            try:
                path = Path(candidate).expanduser()
            except OSError:
                continue
            if not path.is_absolute() or not roots:
                if looks_like_named_absolute_path(candidate):
                    return candidate
                continue
            try:
                resolved = path.resolve()
            except OSError:
                if looks_like_named_absolute_path(candidate):
                    return candidate
                continue
            if not is_within_roots(resolved, roots):
                return candidate
    del cwd
    return None


def blocked_never_allowed_message(
    policy: CommandPolicyDecision,
    parsed: ParsedCliCommand,
) -> str:
    """Name the gate and steer to the allowed form. No desk myth."""
    gate = (policy.message or f"Command not permitted: {parsed.name}").strip()
    line = gate if gate.lower().startswith("blocked") else f"Blocked — {gate}"
    parts = [line]
    rule_steer = _steer_from_policy(policy)
    if rule_steer and rule_steer not in line:
        parts.append(rule_steer)
    if LOCKED_CLONE_DEFAULT_STEER not in " ".join(parts):
        parts.append(LOCKED_CLONE_DEFAULT_STEER)
    return " ".join(parts)


def _approval_from_default(
    parsed: ParsedCliCommand,
    policy: CommandPolicyDecision,
) -> CommandPolicyDecision:
    return CommandPolicyDecision(
        allowed=False,
        tier="approval_required",
        executor="shell",
        approval_required=True,
        message=DEFAULT_APPROVAL_MESSAGE,
        matched_rule_id=policy.matched_rule_id,
    )


def _steer_from_policy(policy: CommandPolicyDecision) -> str:
    rule_id = getattr(policy, "matched_rule_id", None)
    if not rule_id:
        return ""
    rule = db.get_cli_policy_rule(rule_id)
    help_text = (getattr(rule, "help_text", None) or "").strip()
    return help_text.split("\n", 1)[0].strip() if help_text else ""


def _rewrite_token(agent: Agent, token: str, cwd: str) -> str:
    if not token or token == "-":
        return token
    if token.startswith("-") and "=" in token:
        key, value = token.split("=", 1)
        return f"{key}={_rewrite_one_path(agent, value, cwd)}"
    if token.startswith("-"):
        for index, char in enumerate(token):
            if char in "/~":
                return token[:index] + _rewrite_one_path(agent, token[index:], cwd)
        return token
    return _rewrite_one_path(agent, token, cwd)


def _rewrite_one_path(agent: Agent, token: str, cwd: str) -> str:
    if not is_virtual_cli_path(token):
        return token
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd, token)
    except (OSError, ValueError, PathOutsideRootsError):
        return token
    if resolved is None or resolved.real_path is None:
        return token
    return str(resolved.real_path)


def _workspace_roots(agent: Agent) -> tuple[Path, ...]:
    roots: list[Path] = []
    try:
        roots.append(agent_artifact_dir(agent.storage_key).resolve())
    except OSError:
        pass
    try:
        roots.append(projects_artifact_root().resolve())
    except OSError:
        pass
    return tuple(roots)


def _result_why(result: BossModCliResult) -> str:
    data = result.data or {}
    return str(data.get("error") or result.detail or "").strip()


def _blocked_line(why: str) -> str:
    text = (why or "").strip() or HOST_OUTSIDE_NEST_WHY
    if text.lower().startswith("blocked"):
        return text
    if text.lower().startswith("bossmod cli error:"):
        text = text.split(":", 1)[1].strip()
    return f"Blocked — {text}"
