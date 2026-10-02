"""Scoped CLI Always rules — the Settings Always path, bounded to one workspace.

Always allow on an Approve card writes a real ``always_allowed`` row the
same way Settings does, limited to the scope the command stays inside:

* one locked clone under ``/me/host-work`` → ``cwd_prefix=/me/host-work``
  (every locked clone, today's nest behaviour);
* one floor project → ``cwd_prefix=/projects/<slug>`` plus ``floor_id``,
  because project names are virtual per floor;
* the agent's own ``/me`` → ``cwd_prefix=/me`` plus ``agent_id``.

Rule matching is string- and cwd-based, so a scoped rule can match a
command whose operands reach outside its scope. ``contain_scoped_always``
checks the resolved paths at run time and sends such a command back to
the approval path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import db
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.effective_commands import effective_commands
from core.bm_cli.floor_roots import agent_floor_id
from core.bm_cli.host_roots import is_within_roots
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import CommandPolicyDecision, argv0_basename_after_resolve
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent
from core.models.cli_policy import CliApprovalRequest, CliPolicyRule

NEST_CWD_PREFIX = "/me/host-work"
NEST_ALWAYS_CATEGORY = "nest"
SCOPED_ALWAYS_CATEGORY = "scoped"
ME_CWD_PREFIX = "/me"
PROJECTS_CWD_PREFIX = "/projects"
NEST_SCOPE_LABEL = "locked clones"
ME_SCOPE_LABEL = "your workspace"
# Effect class (approval_gate.effects) for kill/pkill/killall/docker.
HOST_PROCESS_EFFECT = "host_process"

# A bare rule for these tools would also allow their dangerous subcommands
# (``git push --force``, ``docker run``), so an Always rule names the
# subcommand too.
_SUBCOMMAND_TOOLS = frozenset({"git", "gh", "npm", "npx", "uv", "pip", "cargo", "docker"})

_HOST_WORK_DIR = PurePosixPath(NEST_CWD_PREFIX).relative_to(ME_CWD_PREFIX).as_posix()


@dataclass(frozen=True)
class AlwaysScope:
    """Where an Always rule written from one approval applies.

    Attributes:
        cwd_prefix: Virtual cwd prefix stored on the rule.
        label: Operator-facing name, shown as "Always allow in <label>".
        floor_id: Set for a floor project rule; the rule matches only that floor.
        agent_id: Set for a ``/me`` rule; the rule matches only that agent.
    """

    cwd_prefix: str
    label: str
    floor_id: str | None
    agent_id: str | None


def _posix_cwd(raw: str | None) -> str:
    return (raw or "").replace("\\", "/").strip()


def looks_like_desktop_or_home(raw: str | None) -> bool:
    """Return True for Desktop / $HOME-shaped paths that must never get Always."""
    token = _posix_cwd(raw)
    if not token:
        return False
    parts = [part for part in token.split("/") if part]
    if "Desktop" in parts or "desktop" in parts:
        return True
    if token == "/me" or token == "/":
        return False
    if len(parts) >= 2 and parts[0] in {"home", "Users"} and "host-work" not in parts:
        return True
    return False


def is_nest_cwd(raw: str | None) -> bool:
    """Return True when *raw* is a locked nest clone cwd under /me/host-work."""
    token = _posix_cwd(raw)
    if not token or looks_like_desktop_or_home(token):
        return False
    if token == NEST_CWD_PREFIX or token.startswith(NEST_CWD_PREFIX + "/"):
        return True
    parts = token.split("/")
    try:
        index = parts.index("host-work")
    except ValueError:
        return False
    return index + 1 < len(parts)


def cwd_matches_rule_scope(cwd: str | None, cwd_prefix: str | None) -> bool:
    """Return True when a rule's optional cwd prefix covers *cwd*.

    Unscoped rules (no prefix) match any cwd. Scoped rules fail closed when
    cwd is missing, Desktop/home, or outside the prefix.
    """
    prefix = _posix_cwd(cwd_prefix)
    if not prefix:
        return True
    token = _posix_cwd(cwd)
    if not token or looks_like_desktop_or_home(token):
        return False
    if token == prefix or token.startswith(prefix + "/"):
        return True
    if prefix == NEST_CWD_PREFIX or prefix.startswith(NEST_CWD_PREFIX + "/"):
        return is_nest_cwd(token)
    return False


def scope_label(cwd_prefix: str) -> str:
    """Return the operator-facing name of a rule's cwd prefix.

    Args:
        cwd_prefix: A rule's virtual cwd prefix.

    Returns:
        ``"locked clones"``, ``"your workspace"``, the project slug for
        ``/projects/<slug>``, or the prefix itself for any other scope.
    """
    prefix = _posix_cwd(cwd_prefix).rstrip("/")
    if prefix == NEST_CWD_PREFIX:
        return NEST_SCOPE_LABEL
    if prefix == ME_CWD_PREFIX:
        return ME_SCOPE_LABEL
    parts = PurePosixPath(prefix).parts
    if len(parts) == 3 and f"/{parts[1]}" == PROJECTS_CWD_PREFIX:
        return parts[2]
    return prefix


def always_allow_pattern(command: str, matched_rule_id: str | None = None) -> str | None:
    """Return the Settings Always prefix for *command*.

    The pattern names what the command really runs: policy evaluates a
    wrapped command (``timeout 5 cp a b``) as ``cp a b``, so a ``timeout``
    rule would never match. A command that runs more than one command
    (``find -exec``, ``find -delete``) gets no pattern: one rule cannot
    cover both.

    For that one command: reuses the matched rule's prefix pattern when
    present (``git push``), unless it is a bare subcommand tool (``git``).
    Otherwise the argv0 basename (``sed``), plus the subcommand for the
    tools in ``_SUBCOMMAND_TOOLS`` (git global options such as ``-C`` are
    skipped).

    Args:
        command: The approved command.
        matched_rule_id: The rule that sent the command for approval, if any.

    Returns:
        The pattern, or None when the command runs more than one command,
        or a subcommand tool has no subcommand to name (``git``,
        ``npm --prefix x install``): a bare rule is never written.

    Raises:
        ValueError: The command does not parse.
    """
    effective = effective_commands(command)
    if len(effective) != 1:
        return None
    if matched_rule_id:
        rule = db.get_cli_policy_rule(matched_rule_id)
        if rule is not None and rule.match_mode == "prefix":
            pattern = rule.pattern.strip()
            if pattern and not _is_bare_subcommand_tool(pattern):
                return pattern
    parsed = parse_cli_command(effective[0])
    name = argv0_basename_after_resolve(parsed.name) or parsed.name
    if name not in _SUBCOMMAND_TOOLS:
        return name
    if name == "git":
        # Imported here: git_argv → nest_git → this module at import time.
        from core.bm_cli.git_argv import _git_command_tail

        tail = _git_command_tail(parsed.args)
        subcommand = tail[0] if tail else None
    else:
        first = parsed.args[0] if parsed.args else None
        subcommand = first if first is not None and not first.startswith("-") else None
    return f"{name} {subcommand}" if subcommand else None


def always_scope_for(agent: Agent, approval: CliApprovalRequest) -> AlwaysScope | None:
    """Return the narrowest scope an Always rule for *approval* may cover.

    The working directory and every path the command touches must sit in
    one locked clone, else one floor project, else the agent's ``/me``.

    Args:
        agent: The agent that asked to run the command.
        approval: The pending approval request.

    Returns:
        The scope, or None when no Always is offered: no cwd, the command
        or cwd does not resolve (parse error, path jail), it affects host
        processes or containers (no path bounds those, and a rule would
        bypass the gate's host-process card), the paths span more than one
        scope, or no pattern can be named.

    Raises:
        LookupError: No agent owns ``agent.storage_key``.
    """
    from core.bm_cli.approval_gate.facts import command_facts, paths_within

    if not approval.cwd:
        return None
    try:
        if always_allow_pattern(approval.command, approval.matched_rule_id) is None:
            return None
        facts = command_facts(agent, parse_cli_command(approval.command), approval.cwd)
    except ValueError:
        # Includes PathJailError / PathOutsideRootsError: a command whose
        # paths do not resolve cannot be proven to stay in any scope.
        return None
    if facts.effect == HOST_PROCESS_EFFECT:
        return None
    for scope, root in _candidate_scopes(agent, facts.cwd_real):
        if paths_within(facts, root):
            return scope
    return None


def always_scope_for_request(approval: CliApprovalRequest) -> AlwaysScope | None:
    """Load the request's agent and return :func:`always_scope_for`.

    Raises:
        LookupError: The request's agent no longer exists.
    """
    agent = db.get_agent(approval.agent_id)
    if agent is None:
        raise LookupError(f"No agent {approval.agent_id!r} for approval {approval.id!r}")
    return always_scope_for(agent, approval)


def write_scoped_always_rule(agent: Agent, approval: CliApprovalRequest) -> CliPolicyRule:
    """Write (or reuse) the scoped Settings Always rule for *approval*.

    Args:
        agent: The agent that asked to run the command.
        approval: The pending approval request.

    Returns:
        The new or existing ``always_allowed`` rule.

    Raises:
        ValueError: No scope can be offered for this command.
    """
    scope = always_scope_for(agent, approval)
    pattern = always_allow_pattern(approval.command, approval.matched_rule_id)
    if scope is None or pattern is None:
        raise ValueError(
            "Always allow needs a tool and subcommand whose working directory and "
            "every path stay inside one locked clone, one floor project, or /me."
        )
    existing = _find_scoped_always_rule(pattern, scope)
    if existing is not None:
        return existing
    return db.create_cli_policy_rule(
        tier="always_allowed",
        pattern=pattern,
        match_mode="prefix",
        agent_id=scope.agent_id,
        cwd_prefix=scope.cwd_prefix,
        floor_id=scope.floor_id,
        description=f"Always allow {pattern} in {scope.label}.",
        category=NEST_ALWAYS_CATEGORY if scope.cwd_prefix == NEST_CWD_PREFIX else SCOPED_ALWAYS_CATEGORY,
    )


def contain_scoped_always(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    policy: CommandPolicyDecision,
) -> CommandPolicyDecision:
    """Return *policy*, or approval_required when a scoped Always rule overreaches.

    Applies to an ``always_allowed`` decision from a rule with a cwd prefix
    other than the nest prefix (nest rules keep today's behaviour). The
    working directory and every path the command touches must resolve
    inside the prefix's real directory. A command that cannot be resolved
    is not proven contained and goes to approval too, and so does a
    host-process command (``kill``, ``docker``): its effects are not paths,
    so no scope contains them.

    Args:
        agent: The agent running the command.
        parsed: The command (virtual or already-rewritten real paths).
        cwd: The agent's virtual working directory.
        policy: The policy decision for the command.

    Returns:
        *policy* unchanged, or an ``approval_required`` decision naming the
        rule and its scope.

    Raises:
        LookupError: No agent owns ``agent.storage_key``.
    """
    from core.bm_cli.approval_gate.facts import command_facts, paths_within
    from core.bm_cli.virtual_fs import resolve_cli_path

    if policy.tier != "always_allowed" or not policy.matched_rule_id:
        return policy
    rule = db.get_cli_policy_rule(policy.matched_rule_id)
    if rule is None:
        return _needs_approval(policy, f"Always rule {policy.matched_rule_id} was removed")
    prefix = _posix_cwd(rule.cwd_prefix)
    if not prefix or prefix == NEST_CWD_PREFIX:
        return policy
    overreach = (
        f"Always rule {rule.pattern} covers {scope_label(prefix)} only; "
        "this command reaches outside it"
    )
    try:
        root = resolve_cli_path(agent.storage_key, "/", prefix).real_path
        facts = command_facts(agent, parsed, cwd)
    except ValueError:
        return _needs_approval(policy, overreach)
    if root is None or not paths_within(facts, Path(root).resolve()):
        return _needs_approval(policy, overreach)
    if facts.effect == HOST_PROCESS_EFFECT:
        return _needs_approval(
            policy,
            f"Always rule {rule.pattern} covers {scope_label(prefix)} only; "
            "this command affects host processes or containers outside any scope",
        )
    return policy


def _needs_approval(policy: CommandPolicyDecision, message: str) -> CommandPolicyDecision:
    return CommandPolicyDecision(
        allowed=False,
        tier="approval_required",
        executor=policy.executor,
        approval_required=True,
        message=message,
        matched_rule_id=policy.matched_rule_id,
    )


def _is_bare_subcommand_tool(pattern: str) -> bool:
    tokens = pattern.split()
    return len(tokens) == 1 and argv0_basename_after_resolve(tokens[0]) in _SUBCOMMAND_TOOLS


def _candidate_scopes(agent: Agent, cwd_real: Path) -> list[tuple[AlwaysScope, Path]]:
    """Return the scopes holding *cwd_real*, narrowest first, with their real roots."""
    me = agent_artifact_dir(agent.storage_key).resolve()
    host_work = me / _HOST_WORK_DIR
    scopes: list[tuple[AlwaysScope, Path]] = []
    if cwd_real != host_work and is_within_roots(cwd_real, (host_work,)):
        clone = host_work / cwd_real.relative_to(host_work).parts[0]
        nest = AlwaysScope(NEST_CWD_PREFIX, NEST_SCOPE_LABEL, None, None)
        scopes.append((nest, clone))
    project = project_directory_for(agent.storage_key, cwd_real)
    if project is not None:
        prefix = f"{PROJECTS_CWD_PREFIX}/{project.name}"
        floor = agent_floor_id(agent.storage_key)
        scopes.append((AlwaysScope(prefix, project.name, floor, None), project))
    if is_within_roots(cwd_real, (me,)):
        scopes.append((AlwaysScope(ME_CWD_PREFIX, ME_SCOPE_LABEL, None, agent.id), me))
    return scopes


def _find_scoped_always_rule(pattern: str, scope: AlwaysScope) -> CliPolicyRule | None:
    """Return an existing Always rule for *pattern* with exactly this scope."""
    for rule in db.list_cli_policy_rules(tier="always_allowed"):
        if (
            rule.pattern == pattern
            and rule.match_mode == "prefix"
            and _posix_cwd(rule.cwd_prefix) == scope.cwd_prefix
            and rule.floor_id == scope.floor_id
            and rule.agent_id == scope.agent_id
        ):
            return rule
    return None
