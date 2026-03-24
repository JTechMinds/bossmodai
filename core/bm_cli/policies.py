"""BossMod AI — Command policy tiers for the virtual BossMod CLI."""

from __future__ import annotations

from dataclasses import dataclass

from core.bm_cli.types import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class CommandPolicyDecision:
    """Policy decision for one parsed BossMod CLI command."""

    allowed: bool
    tier: str
    executor: str
    approval_required: bool = False
    message: str | None = None


READ_COMMANDS = {
    "pwd",
    "cd",
    "ls",
    "cat",
    "status",
    "runtime",
    "activity",
    "current-task",
    "tasks",
    "recent-work",
    "location",
}

WRITE_COMMANDS = {
    "mkdir",
    "write",
    "append",
}

GIT_READ_SUBCOMMANDS = {"status", "log", "diff", "show"}
GIT_WRITE_SUBCOMMANDS = {"restore"}

APPROVAL_REQUIRED_COMMANDS = {
    "rm",
    "rmdir",
    "mv",
    "cp",
    "chmod",
    "chown",
    "shell",
    "exec",
}


def evaluate_command_policy(command_name: str) -> CommandPolicyDecision:
    """Return the current policy gate for a virtual CLI command."""
    if command_name in READ_COMMANDS:
        return CommandPolicyDecision(allowed=True, tier="read", executor="virtual")
    if command_name in WRITE_COMMANDS:
        return CommandPolicyDecision(allowed=True, tier="write", executor="virtual")
    if command_name in APPROVAL_REQUIRED_COMMANDS:
        return CommandPolicyDecision(
            allowed=False,
            tier="restricted",
            executor="shell",
            approval_required=True,
            message=f'Command "{command_name}" requires operator approval and is not enabled in the current BossMod CLI policy.',
        )
    return CommandPolicyDecision(
        allowed=False,
        tier="unknown",
        executor="virtual",
        message=f'Unsupported command: {command_name}',
    )


def evaluate_parsed_command_policy(parsed: ParsedCliCommand) -> CommandPolicyDecision:
    """Return the current policy gate for one parsed BossMod CLI command."""
    if parsed.name != "git":
        return evaluate_command_policy(parsed.name)
    if not parsed.args:
        return CommandPolicyDecision(
            allowed=False,
            tier="unknown",
            executor="virtual",
            message='Unsupported command: git',
        )
    subcommand = parsed.args[0]
    if subcommand in GIT_READ_SUBCOMMANDS:
        return CommandPolicyDecision(allowed=True, tier="read", executor="virtual")
    if subcommand in GIT_WRITE_SUBCOMMANDS:
        return CommandPolicyDecision(allowed=True, tier="write", executor="virtual")
    return CommandPolicyDecision(
        allowed=False,
        tier="unknown",
        executor="virtual",
        message=f'Unsupported git command: {subcommand}',
    )
