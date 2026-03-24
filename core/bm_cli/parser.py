"""BossMod AI — Shell-like command parsing for BossMod CLI."""

from __future__ import annotations

import shlex

from core.bm_cli.types import ParsedCliCommand


def parse_cli_command(command: str) -> ParsedCliCommand:
    """Parse a shell-like CLI command into a normalized command name plus args."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Command parse error: {exc}") from exc

    if not tokens:
        raise ValueError("Command is empty.")

    return ParsedCliCommand(
        raw=command,
        name=tokens[0].strip(),
        args=tuple(tokens[1:]),
    )
