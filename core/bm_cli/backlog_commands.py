"""BossMod AI — the agent's ``backlog`` command: search and file the floor's backlog.

The backlog is unassigned work for the boss to triage (core/tasking/backlog.py
holds the rules). ``backlog`` / ``backlog list [text…]`` lists the open
backlog on the agent's floor; ``backlog add`` with a JSON body files one item.
Filing wakes nobody. This module is transport only: it parses, calls the
domain, and formats.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

import db
from core import config
from core.bm_cli.command_registry import BACKLOG_FORMS
from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.schedule_commands import validation_sentences
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.boss import boss_label
from core.floors import FloorDenied
from core.models import Task
from core.models.message import HUMAN_SENDER_ID
from core.models.task import DEFAULT_TASK_SEVERITY, BacklogItemCreate
from core.scheduling.recurrence import format_local_run
from core.tasking.backlog import BacklogFiling, file_backlog_item, list_floor_backlog

_SECTION = "FLOOR BACKLOG"
_HEADER = "id | severity | title | project | reported by | filed | refs"
_EMPTY = "The backlog on your floor is empty."
_NO_MATCH = 'Nothing in the backlog on your floor matches "{query}".'
_ALLOWED_SEVERITIES = "P0 (critical), P1 (high), P2 (medium) or P3 (low)"


def handle_backlog(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None,
) -> BossModCliResult:
    """Search or file the calling agent's floor backlog.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``backlog`` / ``backlog list [text…]``: the open backlog on the agent's
      floor, newest first, at most ``cli_backlog_list_limit`` rows, filtered
      by the words after ``list`` (title or description, any case). A
      trailing line says how many are hidden when the list is cut.
    - ``backlog add`` with a ``BacklogItemCreate`` JSON body: file one item.

    Args:
        context: The calling agent's CLI context.
        parsed: The parsed command.
        content: The JSON body for ``add``; refused for ``list``.

    Returns:
        A ``success_result`` of kind ``backlog``. A new filing carries the
        ``task_created`` activity under ``data["activity"]`` so the Tasks
        place refreshes, says when the severity was defaulted to P3, and
        lists each reference that was not linked. A bind says which item
        already holds it. Every user error is an ``error_result``: usage, a
        body that is not a JSON object, the validation sentences (with the
        allowed severities when severity was wrong), an ambiguous match
        (with the candidate ids), or no floor.
    """
    args = parsed.args
    subcommand = args[0] if args else "list"
    if subcommand == "list":
        if (content or "").strip():
            return _usage_error(context, parsed, "backlog list takes no body; put search words after list.")
        return _list(context, parsed, " ".join(args[1:]))
    if subcommand == "add":
        if len(args) != 1:
            return _usage_error(context, parsed, "backlog add takes no arguments; the item goes in the body.")
        return _add(context, parsed, content)
    return _usage_error(context, parsed, f'Unknown backlog subcommand "{subcommand}".')


def _list(context: CliExecutionContext, parsed: ParsedCliCommand, query: str) -> BossModCliResult:
    limit = config.require_positive_int("cli_backlog_list_limit")
    try:
        tasks, total = list_floor_backlog(context.agent.id, query=query or None, limit=limit)
    except FloorDenied as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    rows = [_row(task) for task in tasks]
    if rows:
        lines = [_HEADER, *(_line(row) for row in rows)]
        if total > len(rows):
            lines.append(f"showing {len(rows)} of {total} — add search words to narrow")
    else:
        lines = [_NO_MATCH.format(query=query) if query else _EMPTY]
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked the floor backlog via BossMod CLI",
        kind="backlog",
        data={"backlog": rows, "total": total},
        sections=[(_SECTION, lines)],
        cwd=context.cwd,
    )


def _add(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None) -> BossModCliResult:
    body = _body(context, parsed, content)
    if isinstance(body, BossModCliResult):
        return body
    try:
        filing = file_backlog_item(context.agent, body)
    except FloorDenied as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    if filing.outcome == "clarify_ambiguous_match" or filing.task is None:
        listed = ", ".join(f'{task.id} ("{task.title}")' for task in filing.candidates)
        return error_result(
            parsed.raw,
            f"More than one open task you reported already has this title: {listed}. "
            "Use taskmsg on the right one to add detail.",
            cwd=context.cwd,
        )
    if filing.outcome == "bind_existing_task":
        return success_result(
            command=parsed.raw,
            detail=f"{context.agent.name} found {filing.task.id} already in the backlog",
            kind="backlog",
            data={"action": "already_filed", "task_id": filing.task.id},
            sections=[(_SECTION, [
                f"Already in the backlog as {filing.task.id} (you filed it). Use taskmsg on it to add detail.",
            ])],
            cwd=context.cwd,
        )
    return _filed(context, parsed, body, filing.task, filing)


def _filed(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    body: BacklogItemCreate,
    task: Task,
    filing: BacklogFiling,
) -> BossModCliResult:
    """The success answer for a new filing, with its ``task_created`` activity."""
    lines = [_HEADER, _line(_row(task))]
    if "severity" not in body.model_fields_set:
        lines.append(f"Filed as {DEFAULT_TASK_SEVERITY} (the default). If it is more serious, say so with taskmsg.")
    lines.extend(f"Not linked: {item.path} — {item.reason}" for item in filing.rejected_references)
    data: dict[str, Any] = {
        "action": "filed",
        "task_id": task.id,
        "rejected_references": [{"path": item.path, "reason": item.reason} for item in filing.rejected_references],
        "activity": {
            "event": "task_created",
            "detail": f'{context.agent.name} filed "{task.title}" to the backlog',
            "extra": {"agent_id": context.agent.id, "task_id": task.id},
        },
    }
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} filed backlog item {task.id}",
        kind="backlog",
        data=data,
        sections=[(_SECTION, lines)],
        authoritative_note="Filed. It waits unassigned for the boss to triage; nobody was woken.",
        cwd=context.cwd,
    )


def _body(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None,
) -> BacklogItemCreate | BossModCliResult:
    """Parse and validate the ``add`` body, or the error result that says why not."""
    text = (content or "").strip()
    if not text:
        return _usage_error(context, parsed, "backlog add needs the JSON body.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return error_result(parsed.raw, f"The body must be a JSON object: {exc.msg}", cwd=context.cwd)
    if not isinstance(raw, dict):
        return error_result(parsed.raw, "The body must be a JSON object: it is not an object", cwd=context.cwd)
    try:
        return BacklogItemCreate.model_validate(raw)
    except ValidationError as exc:
        message = validation_sentences(exc)
        if any(error.get("loc", ())[:1] == ("severity",) for error in exc.errors()):
            message += f". Severity must be {_ALLOWED_SEVERITIES}."
        return error_result(parsed.raw, message, cwd=context.cwd)


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in BACKLOG_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)


def _row(task: Task) -> dict[str, Any]:
    """One backlog item as plain data for the line and the result's ``data``."""
    return {
        "id": task.id,
        "severity": task.severity,
        "title": task.title,
        "project": task.project,
        "reported_by": _reporter_name(task.requester_id),
        "filed": format_local_run(_aware(task.created_at)),
        "references": len(task.references),
    }


def _line(row: dict[str, Any]) -> str:
    return " | ".join([
        row["id"],
        row["severity"],
        trim(str(row["title"])),
        row["project"] or "-",
        row["reported_by"],
        row["filed"],
        str(row["references"]),
    ])


def _reporter_name(requester_id: str | None) -> str:
    if not requester_id:
        # The reporter was deleted, which detaches requester_id.
        return "no longer here"
    if requester_id == HUMAN_SENDER_ID:
        return boss_label()
    agent = db.get_agent(requester_id)
    return agent.name if agent is not None else "no longer here"


def _aware(instant: datetime) -> datetime:
    # SQLite's current_timestamp is UTC without an offset.
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=timezone.utc)
