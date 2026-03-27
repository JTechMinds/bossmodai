"""BossMod AI — DB-driven policy rule evaluator with in-memory caching.

Replaces hardcoded command classification with database-backed rules loaded
from the ``cli_policy_rules`` table.  Rules are lazy-loaded on first use and
cached until :meth:`PolicyEngine.reload` invalidates them.

Evaluation order (first match wins):
    1. Virtual commands  -> allowed, executor="virtual"
    2. never_allowed     -> denied
    3. always_allowed    -> allowed, executor="shell"
    4. approval_required -> denied, approval_required=True
    5. Default policy    -> ``cli_default_policy`` setting ("deny" or "approval_required")
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass

import db
from core import config
from core.models.cli_policy import CliPolicyRule

logger = logging.getLogger(__name__)

# Tier evaluation order — first matching tier wins.
_TIER_ORDER: tuple[str, ...] = ("never_allowed", "always_allowed", "approval_required")


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

        Returns
        -------
        CommandPolicyDecision
            The first-match policy decision.
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
        if config.get("cli_shell_enabled") != "true":
            return CommandPolicyDecision(
                allowed=False,
                tier="disabled",
                executor="shell",
                message=f'"{command_name}" is not a built-in command and shell execution is not enabled. Use built-in commands: pwd cd ls cat mkdir write append status runtime activity current-task tasks recent-work location git.',
            )

        # 3. Walk tiers in strict order; first matching rule wins.
        self._ensure_loaded()
        for tier in _TIER_ORDER:
            rules = self._rules_for_tier(tier, agent_id)
            for rule in rules:
                if self._match_rule(command_str, rule):
                    return self._decision_for_tier(tier, rule)

        # 4. No rule matched — fall through to the default policy.
        return self._default_decision(command_str)

    def evaluate_dry_run(
        self,
        command_str: str,
        virtual_commands: frozenset[str],
        agent_id: str | None = None,
    ) -> CommandPolicyDecision:
        """Same as :meth:`evaluate` — alias for the policy simulator."""
        return self.evaluate(command_str, virtual_commands, agent_id)

    def reload(self) -> None:
        """Invalidate cached rules so they are re-fetched on next evaluation."""
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

    def _rules_for_tier(
        self,
        tier: str,
        agent_id: str | None,
    ) -> list[CliPolicyRule]:
        """Return rules for *tier*, optionally refined by *agent_id*.

        When an *agent_id* is provided the engine queries the DB directly for
        agent-specific + global rules (the DB query already orders
        agent-specific rules before global ones).  When no agent context is
        needed, the pre-cached global rules are used.
        """
        if agent_id is not None:
            return db.get_cli_policy_rules_by_tier(tier, agent_id=agent_id)

        with self._lock:
            if self._rules is not None:
                return self._rules.get(tier, [])
        return []

    def _match_rule(self, command_str: str, rule: CliPolicyRule) -> bool:
        """Check if a single rule's pattern matches *command_str*."""
        matcher = _MATCHERS.get(rule.match_mode)
        if matcher is None:
            logger.warning(
                "Unknown match_mode %r on rule %s — skipping",
                rule.match_mode,
                rule.id,
            )
            return False
        return matcher(command_str, rule.pattern)

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
            )
        if tier == "always_allowed":
            return CommandPolicyDecision(
                allowed=True,
                tier="always_allowed",
                executor="shell",
                matched_rule_id=rule.id,
            )
        if tier == "approval_required":
            return CommandPolicyDecision(
                allowed=False,
                tier="approval_required",
                executor="shell",
                approval_required=True,
                message=f"Command requires approval: {rule.description or rule.pattern}",
                matched_rule_id=rule.id,
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
        """Apply the default policy when no rule matches."""
        default_policy = config.get("cli_default_policy") or "deny"

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
