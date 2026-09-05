"""BossMod AI — Detect managed write / batch / section-rewrite CLI requests."""

from __future__ import annotations

from core.bm_cli.parser import parse_cli_command

def is_managed_write_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed single-file writer."""
    if content is not None and content.strip():
        return False
    return _is_command_name(command, "write", expected_args=1)


def is_managed_batch_write_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed batch writer."""
    if content is None or not content.strip():
        return False
    return _is_command_name(command, "bwrite", expected_args=0)


def is_managed_section_rewrite_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed section rewriter."""
    if content is None or not content.strip():
        return False
    return _is_command_name(command, "rewsect", expected_args=2)


def _is_command_name(command: str, expected_name: str, *, expected_args: int) -> bool:
    """Return whether the command parses to the expected name and arity."""
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        return False
    return parsed.name == expected_name and len(parsed.args) == expected_args

