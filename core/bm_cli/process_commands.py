"""BossMod AI — the agent's ``processes`` command: its background processes.

A pipeline ended with ``&`` keeps running after its command returns
(core/bm_cli/background_processes.py owns that). With no subcommand, or
``list``, this lists the calling agent's running processes and its newest
ended ones; ``logs`` shows the end of one's output and ``stop`` stops it.
An agent sees and stops only its own processes.

Kept out of state_commands.py, which is a different concern and already long.
"""

from __future__ import annotations

from typing import Any

import db
from core import config
from core.bm_cli import background_processes
from core.bm_cli.command_registry import PROCESSES_FORMS
from core.bm_cli.process_announce import PROCESS_CHANGED_EVENT, changed_extra
from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models.agent_process import AgentProcess
from core.scheduling.recurrence import format_local_run

NO_PROCESSES = "You have no background processes. End a command with & to start one."
MIN_ID_PREFIX = 8
# How many ended processes the list shows under the running ones.
RECENT_ENDED_LIMIT = 5
AGENT_STOP_REASON = "Stopped by agent"
MAX_OUTPUT_SETTING = "cli_shell_max_output_bytes"
_SECTION = "BACKGROUND PROCESSES"
_HEADER = "id | status | command | cwd | started | ended/why"
_ID_SUBCOMMANDS = {"logs", "stop"}


def handle_processes(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None,
) -> BossModCliResult:
    """List, read or stop the calling agent's background processes.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``processes`` / ``processes list``: the running processes, then up to
      the newest 5 ended ones, one line each: the first 8 characters of the
      id, status, command, cwd, start time, and for a running one how long
      it has run (and whether the operator was asked about it), for an
      ended one when and why it ended.
    - ``processes logs <id>``: the end of its output, capped at
      ``cli_shell_max_output_bytes``.
    - ``processes stop <id>``: stops it and everything it started.

    ``<id>`` is a unique prefix of at least 8 characters of one of the
    caller's processes. Every form first records processes that ended since
    they were last looked at, so the statuses are current.

    Args:
        context: The calling agent's CLI context; only its own processes are touched.
        parsed: The parsed command.
        content: Must be empty: no form takes a body.

    Returns:
        A ``success_result`` of kind ``processes`` (a stop also declares the
        ``agent_process_changed`` activity under ``data["activity"]`` for the
        turn to broadcast), or an ``error_result``: usage (listing the
        forms), an unknown, too-short or ambiguous id, a process that
        already ended, or a log file that is gone.

    Raises:
        core.config.ConfigError: ``cli_shell_max_output_bytes`` is missing
            (``logs`` only).
    """
    args = parsed.args
    subcommand = args[0] if args else "list"
    if (content or "").strip():
        return _usage_error(context, parsed, "processes takes no body.")
    if subcommand == "list":
        if len(args) > 1:
            return _usage_error(context, parsed, "processes list takes no arguments.")
        return _list(context, parsed)
    if subcommand in _ID_SUBCOMMANDS:
        if len(args) != 2:
            return _usage_error(context, parsed, f"processes {subcommand} needs exactly one <id>.")
        background_processes.live_for_agent(context.agent.id)
        resolved = _resolve(context, parsed, args[1])
        if isinstance(resolved, BossModCliResult):
            return resolved
        if subcommand == "logs":
            return _logs(context, parsed, resolved)
        return _stop(context, parsed, resolved)
    return _usage_error(context, parsed, f'Unknown processes subcommand "{subcommand}".')


def _list(context: CliExecutionContext, parsed: ParsedCliCommand) -> BossModCliResult:
    running = background_processes.live_for_agent(context.agent.id)
    ended = background_processes.recent_ended_for_agent(context.agent.id, limit=RECENT_ENDED_LIMIT)
    rows = [*running, *ended]
    lines = [_HEADER, *(_line(row) for row in rows)] if rows else [NO_PROCESSES]
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked background processes via BossMod CLI",
        kind="processes",
        data={"processes": [_data(row) for row in rows]},
        sections=[(_SECTION, lines)],
        cwd=context.cwd,
    )


def _logs(context: CliExecutionContext, parsed: ParsedCliCommand, row: AgentProcess) -> BossModCliResult:
    try:
        tail = background_processes.read_log_tail(row, config.require_int(MAX_OUTPUT_SETTING))
    except FileNotFoundError:
        return error_result(parsed.raw, f"The log for {row.short_id} is gone.", cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} read the log of background process {row.id}",
        kind="processes",
        data={"process": _data(row)},
        sections=[(f"LOG {row.short_id} ({row.status})", [tail.rstrip("\n") or "(no output yet)"])],
        cwd=context.cwd,
    )


def _stop(context: CliExecutionContext, parsed: ParsedCliCommand, row: AgentProcess) -> BossModCliResult:
    stopped = None
    if row.status == "running":
        stopped = background_processes.stop(row.id, by="agent", reason=AGENT_STOP_REASON)
    if stopped is None:
        return error_result(parsed.raw, f"{row.short_id} already ended. Nothing to stop.", cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} stopped background process {stopped.id}",
        kind="processes",
        data={
            "process": _data(stopped),
            "activity": {
                "event": PROCESS_CHANGED_EVENT,
                "detail": f"{context.agent.name} stopped a background process",
                "extra": changed_extra(context.agent.id, stopped.id),
            },
        },
        sections=[(_SECTION, [_HEADER, _line(stopped)])],
        cwd=context.cwd,
    )


def _resolve(context: CliExecutionContext, parsed: ParsedCliCommand, prefix: str) -> AgentProcess | BossModCliResult:
    """The caller's one process whose id starts with ``prefix``, or the error result."""
    if len(prefix) < MIN_ID_PREFIX:
        return error_result(
            parsed.raw,
            f"Give at least {MIN_ID_PREFIX} characters of the process id (processes list shows them).",
            cwd=context.cwd,
        )
    matches = db.find_agent_processes_by_prefix(context.agent.id, prefix)
    if not matches:
        return error_result(parsed.raw, f"No background process of yours matches {prefix}.", cwd=context.cwd)
    if len(matches) > 1:
        listed = ", ".join(row.id for row in matches)
        return error_result(
            parsed.raw, f"{prefix} matches more than one of your processes: {listed}. Give more characters.",
            cwd=context.cwd,
        )
    return matches[0]


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in PROCESSES_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)


def _line(row: AgentProcess) -> str:
    if row.status == "running":
        state = f"running for {background_processes.running_for(row)}"
        if row.checkin_requested_at is not None:
            state += "; the boss asked whether to keep it"
    else:
        # mark_agent_process_ended writes both; a row without them is corrupt.
        if row.ended_at is None or row.stop_reason is None:
            raise ValueError(f"ended background process {row.id} has no end time or reason")
        state = f"{format_local_run(row.ended_at)}: {row.stop_reason}"
    return " | ".join([
        row.short_id, row.status, trim(row.command), row.cwd, format_local_run(row.started_at), state,
    ])


def _data(row: AgentProcess) -> dict[str, Any]:
    """One process as plain data for the result."""
    return {
        "id": row.id,
        "status": row.status,
        "command": row.command,
        "cwd": row.cwd,
        "pid": row.pid,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at is not None else None,
        "stop_reason": row.stop_reason,
        "checkin_requested": row.checkin_requested_at is not None,
    }
