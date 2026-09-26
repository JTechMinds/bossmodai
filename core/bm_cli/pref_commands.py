"""BossMod AI — ``pref``: the agent's only way to manage its standing prefs.

The prefs store is system-owned (``core/agent_loop/standing_prefs.py``) and
sits outside every agent path. This handler turns the ``pref`` subcommands
into typed store operations and returns the store's validation sentence
unchanged, so the agent sees exactly which rule failed.
"""

from __future__ import annotations

from core.agent_loop.standing_prefs import (
    StandingPref,
    list_standing_prefs,
    remove_standing_pref,
    set_standing_pref,
)
from core.bm_cli.command_registry import PREF_FORMS
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand


def handle_pref(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Set, remove, or list the calling agent's standing prefs.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``pref set <id> <kind> <source> [<source> …]`` with the rule as one
      line in ``content``. Adds the pref, or replaces the pref with that id.
    - ``pref remove <id>`` with no body.
    - ``pref list``: every pref in full, including any the warm section cut.

    Args:
        context: The calling agent, its state, and the CLI cwd.
        parsed: The shlex-parsed command; sources with spaces arrive quoted.
        content: The body field. Required for ``set``, refused for ``remove``.

    Returns:
        A ``success_result`` of kind ``pref``, or an ``error_result``. A
        malformed call lists the three forms; a store rejection (bad kind,
        text over the limit, unknown id, store cap, unreadable store) carries
        the store's sentence as the error.
    """
    subcommand = parsed.args[0] if parsed.args else ""
    if subcommand == "set":
        return _handle_set(context, parsed, content)
    if subcommand == "remove":
        return _handle_remove(context, parsed, content)
    if subcommand == "list":
        return _handle_list(context, parsed)
    reason = (
        '"pref" needs a subcommand: set, remove, or list.'
        if not subcommand
        else f'Unknown pref subcommand "{subcommand}".'
    )
    return _usage_error(context, parsed, reason)


def _handle_set(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    # subcommand, id, kind, then at least one source. The store checks the upper bound.
    if len(parsed.args) < 4:
        return _usage_error(context, parsed, "pref set needs <id> <kind> and at least one <source> after <kind>.")
    text = (content or "").strip()
    if not text:
        return _usage_error(context, parsed, "pref set needs the rule as one line in the body.")
    try:
        pref = set_standing_pref(
            context.agent.storage_key,
            pref_id=parsed.args[1],
            kind=parsed.args[2],
            text=text,
            sources=list(parsed.args[3:]),
        )
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} saved standing pref {pref.id}",
        kind="pref",
        data={"action": "set", "pref": pref.model_dump()},
        sections=[("PREF", [_pref_line(pref)])],
        authoritative_note="Saved. The engine injects standing prefs on every work turn.",
        cwd=context.cwd,
    )


def _handle_remove(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    if len(parsed.args) != 2:
        return _usage_error(context, parsed, "pref remove needs exactly one <id>.")
    if content is not None and content.strip():
        return _usage_error(context, parsed, "pref remove takes no body.")
    pref_id = parsed.args[1]
    try:
        remove_standing_pref(context.agent.storage_key, pref_id)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} removed standing pref {pref_id}",
        kind="pref",
        data={"action": "remove", "id": pref_id},
        sections=[("PREF", [f"removed {pref_id}"])],
        authoritative_note="Removed. It is no longer injected.",
        cwd=context.cwd,
    )


def _handle_list(context: CliExecutionContext, parsed: ParsedCliCommand) -> BossModCliResult:
    if len(parsed.args) != 1:
        return _usage_error(context, parsed, "pref list takes no arguments.")
    try:
        prefs = list_standing_prefs(context.agent.storage_key)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} listed standing prefs",
        kind="pref",
        data={"action": "list", "prefs": [pref.model_dump() for pref in prefs]},
        sections=[("STANDING PREFS", [_pref_line(pref) for pref in prefs] or ["no standing prefs"])],
        cwd=context.cwd,
    )


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in PREF_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)


def _pref_line(pref: StandingPref) -> str:
    # Full text, never clipped: list exists to show what the warm section cuts.
    return f"{pref.kind} {pref.id} — {pref.text} sources: {', '.join(pref.sources)}"
