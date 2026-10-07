"""BossMod AI — ``memory``: the agent's only way to manage its memory.

The memory store is system-owned (``core/agent_loop/standing_prefs.py``) and
sits outside every agent path. This handler turns the ``memory``
subcommands into typed store operations and returns the store's validation
sentence unchanged, so the agent sees exactly which rule failed. The agent
writes only the sentence; the store assigns the number.
"""

from __future__ import annotations

from core.agent_loop.standing_prefs import (
    Memory,
    add_memory,
    list_memories,
    remove_memory,
    replace_memory,
)
from core.bm_cli.command_registry import MEMORY_FORMS
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand


def handle_memory(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Add, replace, remove, or list the calling agent's memories.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``memory add`` with the memory as one line in ``content``; no args.
      The store assigns the next number and the result names it.
    - ``memory replace <n>`` with the new sentence in ``content``. Keeps the
      number and the position.
    - ``memory remove <n>`` with no body.
    - ``memory list``: every memory in full, including any the warm section cut.

    Args:
        context: The calling agent, its state, and the CLI cwd.
        parsed: The shlex-parsed command.
        content: The body field. Required for ``add`` and ``replace``,
            refused for ``remove`` and ``list``.

    Returns:
        A ``success_result`` of kind ``memory``, or an ``error_result``. A
        malformed call lists the four forms; a store rejection (text over the
        limit, a line break, an unknown number, the store cap, an unreadable
        store) carries the store's sentence as the error.
    """
    subcommand = parsed.args[0] if parsed.args else ""
    if subcommand == "add":
        return _handle_add(context, parsed, content)
    if subcommand == "replace":
        return _handle_replace(context, parsed, content)
    if subcommand == "remove":
        return _handle_remove(context, parsed, content)
    if subcommand == "list":
        return _handle_list(context, parsed, content)
    reason = (
        '"memory" needs a subcommand: add, replace, remove, or list.'
        if not subcommand
        else f'Unknown memory subcommand "{subcommand}".'
    )
    return _usage_error(context, parsed, reason)


def _handle_add(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    if len(parsed.args) != 1:
        return _usage_error(context, parsed, "memory add takes no arguments; the system assigns the number.")
    text = (content or "").strip()
    if not text:
        return _usage_error(context, parsed, "memory add needs the memory as one line in the body.")
    try:
        memory = add_memory(context.agent.storage_key, text)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return _saved(context, parsed, "add", memory)


def _handle_replace(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    if len(parsed.args) != 2:
        return _usage_error(context, parsed, "memory replace needs exactly one <n>, the memory's number.")
    memory_id = _memory_number(parsed.args[1])
    if memory_id is None:
        return _usage_error(context, parsed, f'memory number "{parsed.args[1]}" must be a positive whole number.')
    text = (content or "").strip()
    if not text:
        return _usage_error(context, parsed, "memory replace needs the new sentence as one line in the body.")
    try:
        memory = replace_memory(context.agent.storage_key, memory_id, text)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return _saved(context, parsed, "replace", memory)


def _handle_remove(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    if len(parsed.args) != 2:
        return _usage_error(context, parsed, "memory remove needs exactly one <n>, the memory's number.")
    memory_id = _memory_number(parsed.args[1])
    if memory_id is None:
        return _usage_error(context, parsed, f'memory number "{parsed.args[1]}" must be a positive whole number.')
    if content is not None and content.strip():
        return _usage_error(context, parsed, "memory remove takes no body.")
    try:
        memory = remove_memory(context.agent.storage_key, memory_id)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} removed memory #{memory.id}",
        kind="memory",
        data={"action": "remove", "memory": memory.model_dump()},
        sections=[("MEMORY", [f"removed {_memory_line(memory)}"])],
        authoritative_note="Removed. It is no longer shown to you.",
        cwd=context.cwd,
    )


def _handle_list(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    if len(parsed.args) != 1:
        return _usage_error(context, parsed, "memory list takes no arguments.")
    if content is not None and content.strip():
        return _usage_error(context, parsed, "memory list takes no body.")
    try:
        memories = list_memories(context.agent.storage_key)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} listed memories",
        kind="memory",
        data={"action": "list", "memories": [memory.model_dump() for memory in memories]},
        sections=[("MEMORIES", [_memory_line(memory) for memory in memories] or ["no memories saved"])],
        cwd=context.cwd,
    )


def _saved(context: CliExecutionContext, parsed: ParsedCliCommand, action: str, memory: Memory) -> BossModCliResult:
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} saved memory #{memory.id}",
        kind="memory",
        data={"action": action, "memory": memory.model_dump()},
        sections=[("MEMORY", [_memory_line(memory)])],
        authoritative_note="Saved. It is shown to you on every turn.",
        cwd=context.cwd,
    )


def _memory_number(token: str) -> int | None:
    """A positive whole number in base-10 digits, or None."""
    # isascii + isdigit: int() alone would take "+3", " 3" or "1_0".
    if not (token.isascii() and token.isdigit()):
        return None
    value = int(token, 10)
    return value if value >= 1 else None


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in MEMORY_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)


def _memory_line(memory: Memory) -> str:
    # Full text, never clipped: list exists to show what the warm section cuts.
    return f"{memory.id} — {memory.text}"
