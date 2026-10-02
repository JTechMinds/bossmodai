"""Decide whether one shell-bound CLI command may run, before anything happens.

Each pre-execution gate (gh auth, Shell Executor consent, nest git consent,
locked-clone outcome, project-env rewrite, policy with scoped-Always
containment) is split in two. The decide half lives here and only reads
settings, the database, the filesystem and read-only git/ssh probes. Cards,
consent chrome, audit records and execution happen only when a caller
invokes :attr:`ShellAuthorization.materialize`, so several commands can be
decided before any runs. The effect halves stay in :mod:`core.bm_cli.runtime`,
which imports this module, so closures here reach them at call time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from core.bm_cli.cli_always import contain_scoped_always
from core.bm_cli.policies import evaluate_parsed_command_policy
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent

# The gate modules are imported where used: they reach core.agent_loop,
# which imports core.bm_cli (and so runtime and this module) at load.
if TYPE_CHECKING:
    from core.bm_cli.nest_git_consent import NestGitGate

AuthorizationKind = Literal["run", "approval", "block", "consent"]
_Text = str | None

# Audit tier a gate's pause is recorded under when its result names none.
_NEST_GIT_TIER = "nest_git"
_SHELL_OFF_TIER = "disabled"


class Materialize(Protocol):
    """Create a non-run decision's card, chrome or audit record; return the agent's result.

    ``content`` is stored on cards and records; ``channel_id`` is where cards
    and notes post. ``command``, when given, is the text shown, stored on
    the card or record and audited in place of the decided command's own
    text (a script's full text, so a consent resume re-runs the whole
    script); the decision and the argv the gates read are unchanged. The
    result can still refuse for reasons only the channel reveals (archived
    thread, chrome that could not post).
    """

    def __call__(
        self, *, content: str | None, channel_id: str | None, command: str | None = None,
    ) -> BossModCliResult: ...


@dataclass(frozen=True, slots=True)
class ShellAuthorization:
    """One command's decision, with its side effects deferred.

    Attributes:
        kind: ``run`` may execute; ``approval`` needs the approval path
            (card or auto-approve gate); ``block`` is refused; ``consent``
            pauses on a consent card.
        parsed: The command as decided, after locked-clone and project-env
            rewrites. ``run`` executes exactly this.
        policy: The policy decision behind the outcome; always set for
            ``run`` and ``approval``, None for gates decided before policy.
        message: The decision's why when it is known without side effects;
            None when the text only exists once materialized.
        materialize: Performs the side effects for every kind but ``run``;
            None exactly for ``run``.

    Raises:
        ValueError: ``materialize`` or ``policy`` does not match ``kind``.
    """

    kind: AuthorizationKind
    parsed: ParsedCliCommand
    policy: CommandPolicyDecision | None
    message: str | None
    materialize: Materialize | None

    def __post_init__(self) -> None:
        if (self.kind == "run") != (self.materialize is None):
            raise ValueError(f"a {self.kind!r} authorization must materialize unless it runs")
        if self.kind in ("run", "approval") and self.policy is None:
            raise ValueError(f"a {self.kind!r} authorization needs a policy decision")


def authorize_shell_command(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    trigger_type: str | None = None,
    native: bool = False,
) -> ShellAuthorization:
    """Decide one command through every pre-execution gate, in runtime order.

    Order: gh auth, Shell Executor consent, nest git consent, locked-clone
    outcome, project-env rewrite, then policy with scoped-Always
    containment. The first gate that decides wins; later gates are not read.

    Args:
        agent: The agent running the command.
        parsed: The parsed command.
        cwd: The agent's virtual working directory.
        trigger_type: The turn trigger. A consent resume
            (``host_path_consent_resolved``) re-runs without reopening cards.
        native: The command runs on the native shell whatever its name (a
            script segment such as ``ls`` or ``git status``). A virtual
            name then gets shell policy (``git -C`` project subjects
            included) and the Shell Executor and locked-clone gates, never
            the virtual tier.

    Returns:
        The decision. Nothing is written, posted or executed.
    """
    from core.agent_loop.work_binding import bound_task_id

    task_id = bound_task_id(agent.id)
    decided = (
        gh_cli_authorization(agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type)
        or shell_executor_authorization(
            agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type, native=native,
        )
        or nest_git_authorization(agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type)
        or locked_clone_authorization(
            agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type, native=native,
        )
    )
    if decided is not None:
        return decided
    gated = project_env_authorization(agent, parsed, cwd, task_id=task_id, trigger_type=trigger_type)
    if isinstance(gated, ShellAuthorization):
        return gated
    if native:
        return authorize_shell_policy(agent, gated, cwd, trigger_type=trigger_type)
    return policy_authorization(
        agent, gated, cwd, scoped_policy(agent, gated, cwd), trigger_type=trigger_type,
    )


def authorize_shell_policy(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    trigger_type: str | None = None,
) -> ShellAuthorization:
    """Decide a command that left the virtual handler for the shell.

    Uses :func:`shell_policy_for_command` (project ``git -C`` subjects)
    with scoped-Always containment; no consent gates run here.
    """
    policy = contain_scoped_always(agent, parsed, cwd, shell_policy_for_command(agent, parsed, cwd))
    return policy_authorization(agent, parsed, cwd, policy, trigger_type=trigger_type)


def gh_cli_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, *, task_id: str | None, trigger_type: str | None,
) -> ShellAuthorization | None:
    """Decide the gh auth gate. None when gh needs no card or note."""
    from core.bm_cli.nest_git_consent import apply_gh_cli_block, decide_gh_cli_block

    gate = decide_gh_cli_block(agent=agent, parsed=parsed, cwd=cwd, trigger_type=trigger_type)
    if gate is None:
        return None
    materialize = _gate_pause(
        apply_gh_cli_block, gate, agent, parsed, cwd, task_id, trigger_type, _NEST_GIT_TIER,
    )
    return _gate_authorization(gate, parsed, materialize)


def shell_executor_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, *,
    task_id: str | None, trigger_type: str | None, native: bool = False,
) -> ShellAuthorization | None:
    """Decide the Shell Executor gate. None when the shell is on or not needed.

    ``native`` makes a virtual-named command need the shell (a script segment).
    """
    from core.bm_cli.shell_executor_consent import apply_shell_executor_gate, decide_shell_executor

    gate = decide_shell_executor(
        agent=agent, parsed=parsed, cwd=cwd, task_id=task_id, trigger_type=trigger_type, native=native,
    )
    if gate is None:
        return None
    materialize = _gate_pause(
        apply_shell_executor_gate, gate, agent, parsed, cwd, task_id, trigger_type, _SHELL_OFF_TIER,
    )
    if gate.action == "gh":
        if gate.gh is None:
            raise ValueError("Shell Executor gh gate has no gh outcome")
        return _gate_authorization(gate.gh, parsed, materialize)
    if gate.action == "consent":
        return ShellAuthorization("consent", parsed, None, None, materialize)
    message = gate.peek.message if gate.peek is not None else None
    return ShellAuthorization("block", parsed, gate.peek, message, materialize)


def nest_git_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, *, task_id: str | None, trigger_type: str | None,
) -> ShellAuthorization | None:
    """Decide the nest git auth gate. None when no remote-git auth is missing."""
    from core.bm_cli.nest_git_consent import apply_nest_git_gate, decide_nest_git

    gate = decide_nest_git(agent=agent, parsed=parsed, cwd=cwd, trigger_type=trigger_type)
    if gate is None:
        return None
    materialize = _gate_pause(
        apply_nest_git_gate, gate, agent, parsed, cwd, task_id, trigger_type, _NEST_GIT_TIER,
    )
    return _gate_authorization(gate, parsed, materialize)


def locked_clone_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, *,
    task_id: str | None, trigger_type: str | None, native: bool = False,
) -> ShellAuthorization | None:
    """Decide the shared locked-clone outcome. None keeps the desk path.

    An allowed outcome still passes scoped-Always containment, so a scoped
    rule that overreaches goes to approval here too. ``native`` makes a
    virtual-named command a shell command here (a script segment).
    """
    from core.bm_cli.locked_clone_outcome import decide_locked_clone_shell_outcome

    outcome = decide_locked_clone_shell_outcome(
        agent, parsed, cwd, task_id=task_id, virtual_commands=_effects().VIRTUAL_COMMANDS, native=native,
    )
    if outcome is None:
        return None
    if outcome.kind == "never_allowed":
        if outcome.policy is not None and outcome.policy.tier == "never_allowed":
            return _never_allowed(agent, outcome.parsed, cwd, outcome.policy, trigger_type)
        message = outcome.message or outcome.blocked_why or "Command not permitted"
        host = bool(outcome.blocked_why) and "host path" in (outcome.blocked_why or "").lower()
        result_kind = "host_deny" if host else "error"
        refused = outcome.parsed

        def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
            del channel_id
            return _effects()._deny_locked_clone(
                agent, _shown(parsed, command), _shown(refused, command), cwd, message,
                result_kind=result_kind, content=content, trigger_type=trigger_type,
            )

        return ShellAuthorization("block", refused, outcome.policy, message, materialize)
    if outcome.kind == "approval_required":
        policy = outcome.policy or CommandPolicyDecision(
            allowed=False, tier="approval_required", executor="shell",
            approval_required=True, message=outcome.message,
        )
        return _approval(agent, outcome.parsed, cwd, policy, trigger_type, message=outcome.message)
    if outcome.policy is None:
        return None
    contained = contain_scoped_always(agent, outcome.parsed, cwd, outcome.policy)
    return policy_authorization(agent, outcome.parsed, cwd, contained, trigger_type=trigger_type)


def project_env_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, *, task_id: str | None, trigger_type: str | None,
) -> ParsedCliCommand | ShellAuthorization:
    """Rewrite host pip/pytest to the clone's uv/venv, or block it.

    Returns:
        The (possibly rewritten) command to keep deciding, or a ``block``.
    """
    from core.bm_cli.project_env import gate_locked_clone_command

    gated = gate_locked_clone_command(agent, parsed, cwd, task_id=task_id)
    if not isinstance(gated, BossModCliResult):
        return gated
    denied = gated

    def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
        del channel_id
        return _effects()._record_project_env_deny(
            agent, _shown(parsed, command), cwd, denied, content=content, trigger_type=trigger_type,
        )

    message = str((denied.data or {}).get("error") or denied.detail)
    return ShellAuthorization("block", parsed, None, message, materialize)


def scoped_policy(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> CommandPolicyDecision:
    """Policy for *parsed* (virtual or rule tiers), with scoped-Always containment.

    The cwd lets project- and ``/me``-scoped Always rules match; their
    paths are then checked against the scope.
    """
    policy = evaluate_parsed_command_policy(
        parsed, _effects().VIRTUAL_COMMANDS, agent_id=agent.id, cwd=cwd,
    )
    return contain_scoped_always(agent, parsed, cwd, policy)


def shell_policy_for_command(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> CommandPolicyDecision:
    """Match seed rules for project ``git -C`` without changing other ``-C`` forms.

    An operator Deny on the raw command still wins. ``git -C /projects/<slug>
    status`` matches ``git status``. ``git -C . status`` in ``/me`` does not.
    """
    raw = policy_engine.evaluate(parsed.raw, frozenset(), agent_id=agent.id, cwd=cwd)
    if raw.matched_rule_id:
        return raw
    from core.bm_cli.git_argv import git_policy_subject

    subject = git_policy_subject(agent, parsed, cwd)
    if not subject or subject == parsed.raw:
        return raw
    scoped = policy_engine.evaluate(subject, frozenset(), agent_id=agent.id, cwd=cwd)
    if scoped.matched_rule_id:
        return scoped
    return raw


def policy_authorization(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, policy: CommandPolicyDecision, *, trigger_type: str | None,
) -> ShellAuthorization:
    """Map a final policy decision to run, approval or block."""
    if policy.approval_required:
        return _approval(agent, parsed, cwd, policy, trigger_type, message=policy.message)
    if policy.allowed:
        return ShellAuthorization("run", parsed, policy, None, None)
    if policy.tier == "never_allowed":
        return _never_allowed(agent, parsed, cwd, policy, trigger_type)
    message = policy.message or f"Command not permitted: {parsed.name}"

    def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
        del channel_id
        return _effects()._deny_policy(
            agent, _shown(parsed, command), cwd, policy, message, content=content, trigger_type=trigger_type,
        )

    return ShellAuthorization("block", parsed, policy, message, materialize)


def _approval(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, policy: CommandPolicyDecision,
    trigger_type: str | None, *, message: str | None,
) -> ShellAuthorization:
    def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
        return _effects()._handle_approval_required(
            agent=agent, parsed=_shown(parsed, command), content=content, cwd_before=cwd,
            policy=policy, trigger_type=trigger_type, channel_id=channel_id,
        )

    return ShellAuthorization("approval", parsed, policy, message, materialize)


def _never_allowed(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, policy: CommandPolicyDecision, trigger_type: str | None,
) -> ShellAuthorization:
    def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
        return _effects()._deny_policy_never_allowed(
            agent=agent, parsed=_shown(parsed, command), content=content, cwd_before=cwd,
            policy=policy, trigger_type=trigger_type, channel_id=channel_id,
        )

    return ShellAuthorization("block", parsed, policy, policy.message, materialize)


def _gate_pause(
    apply: Callable[..., BossModCliResult], gate: object, agent: Agent, parsed: ParsedCliCommand,
    cwd: str, task_id: str | None, trigger_type: str | None, default_tier: str,
) -> Materialize:
    """Materializer for a consent gate: *apply* its outcome, then record the pause."""

    def materialize(*, content: _Text, channel_id: _Text, command: _Text = None) -> BossModCliResult:
        shown = _shown(parsed, command)
        result = apply(
            gate, agent=agent, parsed=shown, content=content, cwd=cwd, task_id=task_id, channel_id=channel_id,
        )
        return _effects()._record_gate_pause(
            agent, shown, cwd, result, content=content, trigger_type=trigger_type, default_tier=default_tier,
        )

    return materialize


def _gate_authorization(
    gate: NestGitGate, parsed: ParsedCliCommand, materialize: Materialize,
) -> ShellAuthorization:
    kind: AuthorizationKind = "consent" if gate.action == "consent" else "block"
    return ShellAuthorization(kind, parsed, None, gate.reason, materialize)


def _shown(parsed: ParsedCliCommand, command: str | None) -> ParsedCliCommand:
    """*parsed* with its text replaced by *command* for display and records, when given."""
    return parsed if command is None else replace(parsed, raw=command)


def _effects() -> ModuleType:
    """The runtime module (effect halves, VIRTUAL_COMMANDS); it imports this one at load."""
    from core.bm_cli import runtime

    return runtime
