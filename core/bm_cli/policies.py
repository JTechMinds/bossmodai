"""BossMod AI — Command policy evaluation for the BossMod CLI.

Delegates all policy decisions to :mod:`core.bm_cli.policy_engine`, which
evaluates DB-driven rules with in-memory caching.  This module re-exports
the :class:`CommandPolicyDecision` dataclass for backward compatibility.
"""

from __future__ import annotations

from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine
from core.bm_cli.types import ParsedCliCommand

__all__ = ["CommandPolicyDecision", "evaluate_command_policy", "evaluate_parsed_command_policy"]


def evaluate_parsed_command_policy(
    parsed: ParsedCliCommand,
    virtual_commands: frozenset[str],
    agent_id: str | None = None,
) -> CommandPolicyDecision:
    """Return the policy decision for one parsed BossMod CLI command."""
    if parsed.name in virtual_commands:
        return CommandPolicyDecision(
            allowed=True,
            tier="virtual",
            executor="virtual",
        )
    return policy_engine.evaluate(parsed.raw, virtual_commands, agent_id)


def evaluate_command_policy(
    command: str,
    virtual_commands: frozenset[str],
    agent_id: str | None = None,
) -> CommandPolicyDecision:
    """Parse and evaluate one raw command string against the CLI policy rules."""
    parsed = parse_cli_command(command)
    return evaluate_parsed_command_policy(parsed, virtual_commands, agent_id)
