"""BossMod AI — the agent's ``schedules`` command: list and manage its own recurring tasks.

A schedule (core/scheduling) creates one task per run. With no subcommand,
or ``list``, this lists the calling agent's schedules; ``add``, ``edit``,
``on``, ``off`` and ``remove`` change them through the same service and
models the operator's API uses (``core/scheduling/service.py``), so the
rules are one: an agent touches only its own schedules, and only while the
operator lets it (``agent_can_change``). Every change asks the runtime
worker to sync its timetable and posts a note in the operator's DM.

Kept out of state_commands.py, which is a different concern and already long.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

import db
from core.bm_cli.command_registry import SCHEDULE_FORMS
from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models.schedule import AgentSchedule, AgentScheduleUpdate, ScheduleCreate
from core.scheduling import service
from core.scheduling.notices import AgentScheduleVerb, agent_change_line, note_agent_change
from core.scheduling.recurrence import describe, format_local_run, next_occurrence

NO_SCHEDULES = "You have no scheduled recurring tasks."
MIN_ID_PREFIX = 8
_SECTION = "SCHEDULED RECURRING TASKS"
_HEADER = "id | title | repeats | on/off | you can manage | next run | last run | last task"
_ID_SUBCOMMANDS = {"edit", "on", "off", "remove"}


def handle_schedules(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None,
) -> BossModCliResult:
    """List or change the calling agent's schedules.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``schedules`` / ``schedules list``: one line per schedule — the first
      8 characters of its id, title, rule in words, ``on``/``off``, whether
      the agent may change it, next run (``off`` while switched off), last
      outcome with its local time, and the last run's task with its status.
    - ``schedules add`` with a ``ScheduleCreate`` JSON body (no
      ``agent_can_change``: only the operator sets that).
    - ``schedules edit <id>`` with an ``AgentScheduleUpdate`` JSON body.
    - ``schedules on <id>`` / ``schedules off <id>`` / ``schedules remove <id>``.

    ``<id>`` is a unique prefix of at least 8 characters of one of the
    caller's schedules.

    Args:
        context: The calling agent's CLI context; only its own schedules are touched.
        parsed: The parsed command.
        content: The JSON body for ``add`` and ``edit``; refused elsewhere.

    Returns:
        A ``success_result`` of kind ``schedules`` (for a change, the stored
        schedule's list line, with the operator's DM note under
        ``data["origin_chrome"]`` and the ``schedule_changed`` activity under
        ``data["activity"]`` for the turn to broadcast), or an
        ``error_result``: usage (listing the forms), a body that is not a JSON
        object, the validation sentences, an unknown, too-short or ambiguous
        id, or the operator's locked message, exactly.
    """
    args = parsed.args
    subcommand = args[0] if args else "list"
    if subcommand == "list":
        if len(args) > 1 or (content or "").strip():
            return _usage_error(context, parsed, "schedules list takes no arguments and no body.")
        return _list(context, parsed)
    if subcommand == "add":
        if len(args) != 1:
            return _usage_error(context, parsed, "schedules add takes no arguments; the schedule goes in the body.")
        return _add(context, parsed, content)
    if subcommand in _ID_SUBCOMMANDS:
        if len(args) != 2:
            return _usage_error(context, parsed, f"schedules {subcommand} needs exactly one <id>.")
        if subcommand != "edit" and (content or "").strip():
            return _usage_error(context, parsed, f"schedules {subcommand} takes no body.")
        return _change(context, parsed, subcommand, args[1], content)
    return _usage_error(context, parsed, f'Unknown schedules subcommand "{subcommand}".')


def _list(context: CliExecutionContext, parsed: ParsedCliCommand) -> BossModCliResult:
    now = datetime.now(timezone.utc)
    rows = [_row(schedule, now=now) for schedule in db.list_schedules_for_agent(context.agent.id)]
    lines = [_HEADER, *(_line(row) for row in rows)] if rows else [NO_SCHEDULES]
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked scheduled recurring tasks via BossMod CLI",
        kind="schedules",
        data={"schedules": rows},
        sections=[(_SECTION, lines)],
        cwd=context.cwd,
    )


def _add(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    body = _body(parsed, content, ScheduleCreate, context)
    if isinstance(body, BossModCliResult):
        return body
    actor = service.ScheduleActor("agent", context.agent.id)
    try:
        schedule = service.create_schedule(context.agent.id, body, actor=actor)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return _changed(context, parsed, "scheduled", schedule)


def _change(
    context: CliExecutionContext, parsed: ParsedCliCommand, subcommand: str, prefix: str, content: str | None,
) -> BossModCliResult:
    resolved = _resolve(context, parsed, prefix)
    if isinstance(resolved, BossModCliResult):
        return resolved
    actor = service.ScheduleActor("agent", context.agent.id)
    try:
        if subcommand == "edit":
            body = _body(parsed, content, AgentScheduleUpdate, context)
            if isinstance(body, BossModCliResult):
                return body
            return _changed(context, parsed, "changed", service.update_schedule(resolved.id, body, actor=actor))
        if subcommand in {"on", "off"}:
            on = subcommand == "on"
            schedule = service.set_enabled(resolved.id, on, actor=actor)
            return _changed(context, parsed, "switched on" if on else "switched off", schedule)
        return _changed(context, parsed, "removed", service.delete_schedule(resolved.id, actor=actor))
    except service.ScheduleLocked as exc:
        return error_result(parsed.raw, exc.message, cwd=context.cwd)
    except service.ScheduleNotFound:
        return error_result(parsed.raw, f"No schedule of yours matches {prefix}.", cwd=context.cwd)
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)


def _changed(
    context: CliExecutionContext, parsed: ParsedCliCommand, verb: AgentScheduleVerb, schedule: AgentSchedule,
) -> BossModCliResult:
    """Sync the worker, note the change in the operator's DM, and answer with the schedule.

    The note and the desk's ``schedule_changed`` repaint are declared on the
    result (``origin_chrome``, ``activity``; see
    core/agent_loop/cli_turn_result.py) for the turn to broadcast: this
    handler runs off the event loop.
    """
    line = agent_change_line(context.agent, verb, schedule)
    service.request_reload()
    posted = note_agent_change(context.agent, verb, schedule)
    data: dict[str, Any] = {
        "action": verb,
        "schedule_id": schedule.id,
        "activity": {
            "event": "schedule_changed",
            "detail": line,
            "extra": {"agent_id": context.agent.id, "schedule_id": schedule.id},
        },
    }
    chrome = posted.get("chat_message")
    if chrome:
        data["origin_chrome"] = chrome
    shown = _line(_row(schedule, now=datetime.now(timezone.utc))) if verb != "removed" else f"removed {schedule.id[:MIN_ID_PREFIX]}"
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} {verb} schedule {schedule.id}",
        kind="schedules",
        data=data,
        sections=[(_SECTION, [_HEADER, shown] if verb != "removed" else [shown])],
        authoritative_note="Saved. The operator has been told about this change.",
        cwd=context.cwd,
    )


def _body(
    parsed: ParsedCliCommand, content: str | None, model: type[BaseModel], context: CliExecutionContext,
) -> BaseModel | BossModCliResult:
    """Parse and validate a JSON body, or the error result that says why not."""
    text = (content or "").strip()
    if not text:
        return _usage_error(context, parsed, f"schedules {parsed.args[0]} needs the JSON body.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return error_result(parsed.raw, f"The body must be a JSON object: {exc.msg}", cwd=context.cwd)
    if not isinstance(raw, dict):
        return error_result(parsed.raw, "The body must be a JSON object: it is not an object", cwd=context.cwd)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        return error_result(parsed.raw, validation_sentences(exc), cwd=context.cwd)


def validation_sentences(exc: ValidationError) -> str:
    """A pydantic error as plain sentences: ``field: message``, joined, without pydantic's prefixes."""
    sentences = []
    for error in exc.errors():
        message = str(error.get("msg") or "invalid").removeprefix("Value error, ")
        where = ".".join(str(part) for part in error.get("loc") or ())
        sentences.append(f"{where}: {message}" if where else message)
    return "; ".join(sentences)


def _resolve(context: CliExecutionContext, parsed: ParsedCliCommand, prefix: str) -> AgentSchedule | BossModCliResult:
    """The caller's one schedule whose id starts with ``prefix``, or the error result."""
    if len(prefix) < MIN_ID_PREFIX:
        return error_result(
            parsed.raw,
            f"Give at least {MIN_ID_PREFIX} characters of the schedule id (schedules list shows them).",
            cwd=context.cwd,
        )
    matches = db.find_schedules_by_prefix(context.agent.id, prefix)
    if not matches:
        return error_result(parsed.raw, f"No schedule of yours matches {prefix}.", cwd=context.cwd)
    if len(matches) > 1:
        listed = ", ".join(f'{item.id} ("{item.title}")' for item in matches)
        return error_result(
            parsed.raw, f"{prefix} matches more than one of your schedules: {listed}. Give more characters.",
            cwd=context.cwd,
        )
    return matches[0]


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in SCHEDULE_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)


def _row(schedule: AgentSchedule, *, now: datetime) -> dict[str, Any]:
    """One schedule as plain data for the line and the result's ``data``."""
    last_task = db.get_task(schedule.last_task_id) if schedule.last_task_id else None
    return {
        "id": schedule.id,
        "title": schedule.title,
        "repeats": describe(schedule.recurrence),
        "enabled": schedule.enabled,
        "agent_can_change": schedule.agent_can_change,
        "next_run": format_local_run(next_occurrence(schedule.recurrence, after=now)) if schedule.enabled else None,
        "last_outcome": schedule.last_outcome,
        "last_run": format_local_run(schedule.last_occurrence_at) if schedule.last_occurrence_at else None,
        "last_task_id": schedule.last_task_id,
        "last_task_status": last_task.status if last_task is not None else None,
    }


def _line(row: dict[str, Any]) -> str:
    last_run = f"{row['last_outcome']} {row['last_run']}" if row["last_outcome"] else "none yet"
    if row["last_task_id"]:
        last_task = f"{row['last_task_id']} ({row['last_task_status'] or 'no longer on the board'})"
    else:
        last_task = "-"
    return " | ".join([
        row["id"][:MIN_ID_PREFIX],
        trim(str(row["title"])),
        row["repeats"],
        "on" if row["enabled"] else "off",
        "yes" if row["agent_can_change"] else "no (ask the operator)",
        row["next_run"] or "off",
        last_run,
        last_task,
    ])
