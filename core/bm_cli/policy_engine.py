"""BossMod AI — DB-driven policy rule evaluator.

Replaces hardcoded command classification with database-backed rules loaded
from the ``cli_policy_rules`` table. Each evaluation reads the database.
``reload()`` still clears any leftover cache, but the runtime worker does
not need it: a Settings save happens in the API process, and this process
must not keep serving the rules or ``cli_default_policy`` it saw at boot.

Evaluation order (first match wins):
    1. Virtual commands  -> allowed, executor="virtual"
    2. never_allowed     -> denied
       (includes printenv / env dumps of GH_TOKEN / GITHUB_TOKEN, and gh auth token)
    3. always_allowed    -> allowed, executor="shell"
    4. approval_required -> denied, approval_required=True
    5. Default policy    -> ``cli_default_policy`` setting
       (factory approval_required; deny remains selectable)

Steps 2–5 run on each command the string really runs (see
:mod:`core.bm_cli.effective_commands`: wrappers unwrapped, ``find``
actions added), and :func:`strictest` combines the results.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path

import db
from core import config
from core.bm_cli.effective_commands import program_selecting_env_message, unwrap_command
from core.bm_cli.policy_strictness import strictest
from core.models.cli_policy import CliPolicyRule

logger = logging.getLogger(__name__)

# Tier evaluation order — first matching tier wins.
_TIER_ORDER: tuple[str, ...] = ("never_allowed", "always_allowed", "approval_required")
# A wrapper's own rule still counts when it is at least this strict.
_WRAPPER_RULE_TIERS: tuple[str, ...] = ("never_allowed", "approval_required")


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CommandPolicyDecision:
    """Immutable policy decision for a single CLI command."""

    allowed: bool
    tier: str
    executor: str
    approval_required: bool = False
    message: str | None = None
    matched_rule_id: str | None = None
    # The matched rule's ``cwd_prefix``: :func:`strictest` prefers a scoped
    # Always so its containment check still runs.
    matched_cwd_prefix: str | None = None


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

def _match_exact(command_str: str, pattern: str) -> bool:
    """Return ``True`` if the full command string equals the pattern exactly."""
    return command_str == pattern


def _match_prefix(command_str: str, pattern: str) -> bool:
    """Return ``True`` if the command starts with the pattern as a whole token.

    Handles both bare commands (``"rm"`` matches ``"rm"``) and commands with
    arguments (``"rm"`` matches ``"rm -rf file"``).  Prevents false positives
    like ``"rm"`` matching ``"rmdir"`` by requiring a space delimiter after
    the pattern when the command is longer.
    """
    if command_str == pattern:
        return True
    return command_str.startswith(pattern + " ")


def _match_glob(command_str: str, pattern: str) -> bool:
    """Return ``True`` if the command matches the shell-style glob pattern."""
    return fnmatch.fnmatch(command_str, pattern)


_MATCHERS = {
    "exact": _match_exact,
    "prefix": _match_prefix,
    "glob": _match_glob,
}


# ``python3.12`` / ``python3.12.3`` should still hit the ``python3`` seed rule.
_ARGV0_VERSION_SUFFIX_RE = re.compile(r"(?:\.\d+)+$")
# Clone venv binaries: ``../.venv/bin/pip`` should match the ``.venv/bin/pip`` seed.
_VENV_BIN_RE = re.compile(r"(?:^|/)(\.venv|venv)/bin/([^/]+)$")


def argv0_basename_after_resolve(argv0: str) -> str:
    """Return the basename of *argv0* after expanding a path-like token.

    Uses the path's own name, not the symlink target. Following
    ``/usr/bin/python3`` → ``python3.12`` would miss the ``python3`` seed rule
    and is host-dependent. Bare names (``bash``) are unchanged.
    """
    token = (argv0 or "").strip()
    if not token:
        return token
    try:
        expanded = Path(token).expanduser()
    except OSError:
        expanded = Path(token)
    return expanded.name or token


def argv0_policy_names(argv0: str) -> tuple[str, ...]:
    """Basenames that policy rules should see for *argv0*.

    Includes the path basename, a version-stripped form (``python3.12`` →
    ``python3``), and the symlink-target basename when that differs
    (``/bin/sh`` → ``dash``) so both names can match ``never_allowed``.
    """
    token = (argv0 or "").strip()
    if not token:
        return ()
    names: list[str] = []

    def _add(name: str) -> None:
        if not name or name in names:
            return
        names.append(name)
        stripped = _ARGV0_VERSION_SUFFIX_RE.sub("", name)
        if stripped and stripped not in names:
            names.append(stripped)

    _add(argv0_basename_after_resolve(token))
    path_like = token.startswith(("/", ".", "~")) or "/" in token
    if path_like:
        try:
            _add(Path(token).expanduser().resolve().name)
        except OSError:
            pass
    return tuple(names)


def is_venv_bin_path(argv0: str) -> bool:
    """Return True when *argv0* is a ``.venv/bin/…`` or ``venv/bin/…`` path."""
    posix = (argv0 or "").replace("\\", "/")
    return _VENV_BIN_RE.search(posix) is not None


def venv_bin_policy_subject(argv0: str, rest: tuple[str, ...] | list[str]) -> str | None:
    """Map a venv binary path to a ``.venv/bin/<name> …`` policy subject."""
    posix = (argv0 or "").replace("\\", "/")
    match = _VENV_BIN_RE.search(posix)
    if match is None:
        return None
    subject = f".venv/bin/{match.group(2)}"
    extra = [str(token) for token in rest]
    return " ".join([subject, *extra]) if extra else subject


def policy_command_subjects(command_str: str) -> tuple[str, ...]:
    """Command strings to evaluate against policy rules.

    Always includes the raw command. When argv[0] is a path (or a versioned
    interpreter), also includes rewrites that replace argv[0] with each
    policy name so ``/bin/bash -c id`` is evaluated as ``bash -c id``.
    Clone venv paths also get a ``.venv/bin/<name>`` subject so project-local
    pip matches the validate-on-clone seed instead of host ``pip install``.
    """
    subjects = [command_str]
    try:
        tokens = shlex.split(command_str, posix=True)
    except ValueError:
        tokens = command_str.split()
    if not tokens:
        return tuple(subjects)
    venv_subject = venv_bin_policy_subject(tokens[0], tokens[1:])
    if venv_subject is not None and venv_subject not in subjects:
        subjects.append(venv_subject)
    for name in argv0_policy_names(tokens[0]):
        if name == tokens[0]:
            continue
        rewritten = " ".join([name, *tokens[1:]])
        if rewritten not in subjects:
            subjects.append(rewritten)
    return tuple(subjects)


def _agent_floor(agent_id: str | None) -> str | None:
    """Return the floor of *agent_id*, or None when there is no agent or floor.

    An unknown agent id (the simulator accepts any) is on no floor, so
    floor-scoped rules do not match it.
    """
    if agent_id is None:
        return None
    agent = db.get_agent(agent_id)
    return agent.floor_id if agent is not None else None


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """DB-driven policy rule evaluator with in-memory caching.

    Thread-safe via :class:`threading.Lock`.  Rules are loaded lazily from the
    database on first evaluation and cached until :meth:`reload` is called.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: dict[str, list[CliPolicyRule]] | None = None

    # -- public API --------------------------------------------------------

    def evaluate(
        self,
        command_str: str,
        virtual_commands: frozenset[str],
        agent_id: str | None = None,
        *,
        assume_shell: bool | None = None,
        cwd: str | None = None,
    ) -> CommandPolicyDecision:
        """Evaluate *command_str* against the rule hierarchy.

        Parameters
        ----------
        command_str:
            The full command string (e.g. ``"rm -rf /tmp/junk"``).
        virtual_commands:
            Frozenset of command names handled by the virtual CLI layer.
        agent_id:
            Optional agent id for agent-specific rule overrides.
        assume_shell:
            When True, skip the global ``cli_shell_enabled`` gate so callers
            can peek at the rule that would apply after Shell Executor is on.
        cwd:
            Optional working directory. Scoped rules only match when this
            sits under their ``cwd_prefix``.

        Returns
        -------
        CommandPolicyDecision
            The strictest first-match decision across the commands
            *command_str* really runs (:func:`strictest`). Assigning a
            name :func:`~core.bm_cli.effective_commands.selects_program`
            accepts is never_allowed. A wrapper option that hides what runs
            needs approval at least.
        """
        # Extract the bare command name (first whitespace-delimited token).
        command_name = command_str.split()[0] if command_str.strip() else command_str

        # 1. Virtual commands — always allowed, handled internally.
        if command_name in virtual_commands:
            return CommandPolicyDecision(
                allowed=True,
                tier="virtual",
                executor="virtual",
            )

        # 2. Shell disabled globally — deny everything non-virtual.
        shell_on = (
            config.get_live("cli_shell_enabled") == "true"
            if assume_shell is None
            else bool(assume_shell)
        )
        if not shell_on:
            return CommandPolicyDecision(
                allowed=False,
                tier="disabled",
                executor="shell",
                message=f'"{command_name}" is not a built-in command and shell execution is not enabled. Type "help" to discover available commands.',
            )

        # Once per evaluation: floor-scoped rules need the agent's floor.
        floor_id = _agent_floor(agent_id)

        # 3. Token env dumps — never_allowed before always_allowed ``env``.
        dump_deny = self._secret_token_env_dump_decision(
            command_str, agent_id, cwd=cwd, floor_id=floor_id,
        )
        if dump_deny is not None:
            return dump_deny

        # 4. A variable that picks the binary makes an allowed name run
        #    another program. That is an invariant, not a rule.
        unwrapped = unwrap_command(command_str)
        if unwrapped.program_env is not None:
            return CommandPolicyDecision(
                allowed=False,
                tier="never_allowed",
                executor="shell",
                message=(
                    "Command blocked by policy rule: "
                    f"{program_selecting_env_message(unwrapped.program_env)}"
                ),
            )

        # 5. Each command it really runs walks the tiers; the strictest wins.
        decisions = [
            self._evaluate_effective(subject, agent_id, cwd=cwd, floor_id=floor_id)
            for subject in unwrapped.commands
        ]
        if unwrapped.replaced:
            # The wrapper's own Always must not decide, but an operator's
            # never/approval rule on it (``nohup``) still applies.
            wrapper = self._first_match(
                command_str, _WRAPPER_RULE_TIERS, agent_id, cwd=cwd, floor_id=floor_id,
            )
            if wrapper is not None:
                decisions.append(wrapper)
        if unwrapped.approval_reason is not None:
            decisions.append(CommandPolicyDecision(
                allowed=False,
                tier="approval_required",
                executor="shell",
                approval_required=True,
                message=f"Command requires approval: {unwrapped.approval_reason}",
            ))
        return strictest(decisions)

    def evaluate_dry_run(
        self,
        command_str: str,
        virtual_commands: frozenset[str],
        agent_id: str | None = None,
    ) -> CommandPolicyDecision:
        """Same as :meth:`evaluate` — alias for the policy simulator."""
        return self.evaluate(command_str, virtual_commands, agent_id)

    def reload(self) -> None:
        """Drop any cached rules.

        Evaluation reads the database itself, so a worker that never
        receives this call still sees a rule save. The call remains so
        the API process can drop a cache it may have filled earlier.
        """
        with self._lock:
            self._rules = None
        logger.debug("PolicyEngine cache invalidated")

    # -- internals ---------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Lazy-load enabled rules from the DB, grouped by tier."""
        with self._lock:
            if self._rules is not None:
                return

        # Fetch outside the lock to avoid holding it during I/O.
        rules_by_tier: dict[str, list[CliPolicyRule]] = {}
        for tier in _TIER_ORDER:
            rules_by_tier[tier] = db.get_cli_policy_rules_by_tier(tier)

        with self._lock:
            # Double-check: another thread may have loaded while we queried.
            if self._rules is None:
                self._rules = rules_by_tier
                total = sum(len(v) for v in rules_by_tier.values())
                logger.debug("PolicyEngine loaded %d rules across %d tiers", total, len(_TIER_ORDER))

    def _secret_token_env_dump_decision(
        self,
        command_str: str,
        agent_id: str | None,
        *,
        cwd: str | None,
        floor_id: str | None,
    ) -> CommandPolicyDecision | None:
        """Fail-closed never_allowed for printenv / env dumps of GitHub tokens."""
        from core.bm_cli.secret_env import (
            SECRET_TOKEN_ENV_DUMP_MESSAGE,
            command_dumps_secret_token_env,
        )

        if not command_dumps_secret_token_env(command_str):
            return None
        for rule in self._rules_for_tier("never_allowed", agent_id, floor_id):
            if self._match_rule(command_str, rule, cwd=cwd, floor_id=floor_id):
                return self._decision_for_tier("never_allowed", rule)
        return CommandPolicyDecision(
            allowed=False,
            tier="never_allowed",
            executor="shell",
            message=f"Command blocked by policy rule: {SECRET_TOKEN_ENV_DUMP_MESSAGE}",
        )

    def _evaluate_effective(
        self,
        subject: str,
        agent_id: str | None,
        *,
        cwd: str | None,
        floor_id: str | None,
    ) -> CommandPolicyDecision:
        """Decide one effective command: token dump, then tiers, then default."""
        dump_deny = self._secret_token_env_dump_decision(
            subject, agent_id, cwd=cwd, floor_id=floor_id,
        )
        if dump_deny is not None:
            return dump_deny
        matched = self._first_match(subject, _TIER_ORDER, agent_id, cwd=cwd, floor_id=floor_id)
        return matched if matched is not None else self._default_decision(subject)

    def _first_match(
        self,
        subject: str,
        tiers: tuple[str, ...],
        agent_id: str | None,
        *,
        cwd: str | None,
        floor_id: str | None,
    ) -> CommandPolicyDecision | None:
        """Walk *tiers* in order; return the first matching rule's decision."""
        for tier in tiers:
            for rule in self._rules_for_tier(tier, agent_id, floor_id):
                if self._match_rule(subject, rule, cwd=cwd, floor_id=floor_id):
                    return self._decision_for_tier(tier, rule)
        return None

    def _rules_for_tier(
        self,
        tier: str,
        agent_id: str | None,
        floor_id: str | None,
    ) -> list[CliPolicyRule]:
        """Return rules for *tier*, refined by *agent_id* and its *floor_id*.

        Always read the database. The API process calls :meth:`reload` after
        a rule save, but that object is not the runtime worker's. A cached
        tier list would keep the worker on the rules it loaded at boot.
        """
        return db.get_cli_policy_rules_by_tier(tier, agent_id=agent_id, floor_id=floor_id)

    def _match_rule(
        self,
        command_str: str,
        rule: CliPolicyRule,
        *,
        cwd: str | None,
        floor_id: str | None,
    ) -> bool:
        """Check if a single rule's pattern matches *command_str* or argv[0] basename.

        A floor-scoped rule matches only an agent on that floor, so a
        ``/projects/<slug>`` rule never reaches a same-named project on
        another floor. With no agent there is no floor and it never matches.
        """
        from core.bm_cli.cli_always import cwd_matches_rule_scope

        if rule.floor_id is not None and rule.floor_id != floor_id:
            return False
        if not cwd_matches_rule_scope(cwd, getattr(rule, "cwd_prefix", None)):
            return False
        matcher = _MATCHERS.get(rule.match_mode)
        if matcher is None:
            logger.warning(
                "Unknown match_mode %r on rule %s — skipping",
                rule.match_mode,
                rule.id,
            )
            return False
        return any(matcher(subject, rule.pattern) for subject in policy_command_subjects(command_str))

    @staticmethod
    def _decision_for_tier(tier: str, rule: CliPolicyRule) -> CommandPolicyDecision:
        """Build the appropriate :class:`CommandPolicyDecision` for a matched rule."""
        if tier == "never_allowed":
            return CommandPolicyDecision(
                allowed=False,
                tier="never_allowed",
                executor="shell",
                message=f"Command blocked by policy rule: {rule.description or rule.pattern}",
                matched_rule_id=rule.id,
                matched_cwd_prefix=rule.cwd_prefix,
            )
        if tier == "always_allowed":
            return CommandPolicyDecision(
                allowed=True,
                tier="always_allowed",
                executor="shell",
                matched_rule_id=rule.id,
                matched_cwd_prefix=rule.cwd_prefix,
            )
        if tier == "approval_required":
            return CommandPolicyDecision(
                allowed=False,
                tier="approval_required",
                executor="shell",
                approval_required=True,
                message=f"Command requires approval: {rule.description or rule.pattern}",
                matched_rule_id=rule.id,
                matched_cwd_prefix=rule.cwd_prefix,
            )
        # Unreachable for known tiers, but defensive.
        return CommandPolicyDecision(
            allowed=False,
            tier=tier,
            executor="shell",
            message=f"Matched rule in unrecognised tier '{tier}'",
            matched_rule_id=rule.id,
        )

    @staticmethod
    def _default_decision(command_str: str) -> CommandPolicyDecision:
        """Apply the default policy when no rule matches.

        Read the database, not the process cache. Settings writes reload
        only the API process; the runtime worker keeps the value it loaded
        at boot. A live read is what makes an operator's choice take effect
        on the next command. Unset means the factory default,
        approval_required. ``never_allowed`` is decided before this and is
        unchanged.
        """
        default_policy = config.get_live("cli_default_policy") or "approval_required"

        if default_policy == "approval_required":
            return CommandPolicyDecision(
                allowed=False,
                tier="default",
                executor="shell",
                approval_required=True,
                message=f"No matching rule — default policy requires approval for: {command_str}",
            )

        # "deny" or any unrecognised value → hard deny.
        return CommandPolicyDecision(
            allowed=False,
            tier="default",
            executor="shell",
            message=f"No matching rule — command denied by default policy: {command_str}",
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

policy_engine = PolicyEngine()
