"""BossMod AI — Shell-like command parsing for BossMod CLI."""

from __future__ import annotations

import shlex

from core.bm_cli.command_registry import resolve_virtual_command_name
from core.bm_cli.types import ParsedCliCommand


def parse_cli_command(command: str) -> ParsedCliCommand:
    """Parse a shell-like CLI command into a normalized command name plus args."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Command parse error: {exc}") from exc

    if not tokens:
        raise ValueError("Command is empty.")

    raw_name = tokens[0].strip()
    canonical_name = resolve_virtual_command_name(raw_name) or raw_name

    return ParsedCliCommand(
        raw=command,
        name=canonical_name,
        args=tuple(tokens[1:]),
    )
