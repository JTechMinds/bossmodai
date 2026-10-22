"""BossMod AI — the agent's ``checklist`` command: plan the current task in parts.

A checklist is the assignee's own plan for the task it is working on, in
numbered parts it checks off (core/tasking/checklist.py holds the rules).
``checklist`` shows it, ``checklist add`` appends one item per body line,
and ``checklist done <n>`` / ``checklist skip <n>`` resolve an item with the
body as its note. The command acts only on the turn's bound task
(``work_binding.bound_task_id``), so a detached turn has none and a
checklist can never land on the wrong task. This module is transport only:
it parses, calls the domain, and formats.

A ``done`` or ``skip`` also declares ``work_turn_boundary`` with a short
hand-off note (core/agent_loop/cli_turn_result.py documents the contract).
The command declares it in every turn type; only the execution loop acts on
it, by ending the turn and replacing the work's saved steps with the note.
"""

from __future__ import annotations

import re
from typing import Literal

import db
from core.agent_loop.work_binding import bound_task_id
from core.bm_cli.command_registry import CHECKLIST_FORMS
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.default_prompts import render_default_prompt
from core.models import Task
from core.models.task import TaskChecklistItem
from core.tasking.checklist import (
    ChecklistError,
    add_checklist_items,
    format_checklist_for_context,
    open_checklist_items,
    resolve_checklist_item,
)

_NO_TASK = "You have no current task. A checklist belongs to the task you are working on."
_BAD_NUMBER = "Name the item by its number, e.g. checklist done 2."
_EMPTY_ADD = "checklist add needs a body: one item per line."
_EMPTY_SKIP = "checklist skip <n> needs a body: why the item is not needed."
_EMPTY_VIEW = ["empty — add items with checklist add"]
_HANDOFF_PROMPT = "internal_loop_execution_checklist_handoff"
_HANDOFF_PATHS = {"number", "text", "open_items"}
# One leading list marker: "- ", "* ", "• ", "3. " or "3) ".
_LIST_MARKER_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def handle_checklist(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None,
) -> BossModCliResult:
    """Show, extend, or check off the checklist of the calling agent's current task.

    Forms (``parsed.args[0]`` is the subcommand):

    - ``checklist``: the checklist, one line per item with its progress.
    - ``checklist add``: append one open item per non-blank body line.
    - ``checklist done <n>``: mark item ``n`` done; the body, if any, is its
      note (what was found, or where the result is).
    - ``checklist skip <n>``: mark item ``n`` skipped; the body is required
      and says why the item is not needed.

    Args:
        context: The calling agent's CLI context.
        parsed: The parsed command.
        content: The body: the items for ``add``, the note for ``done`` and
            ``skip``. Refused for the view.

    Returns:
        A ``success_result`` whose section is the checklist in the shared
        format, with ``data`` ``{"task_id", "checklist"}``. ``kind`` is
        ``checklist`` for the view and ``add``, and
        ``checklist_item_resolved`` for ``done`` and ``skip`` (liveness
        counts that kind as progress). ``add``, ``done`` and ``skip`` declare
        a ``task_updated`` activity so the Tasks place refreshes; ``done``
        and ``skip`` also declare ``work_turn_boundary`` with the hand-off
        note. Every user error is an ``error_result``: no current task, an
        unknown form (with the forms listed), a missing or malformed item
        number, an empty ``add`` or ``skip`` body, or the domain's
        ``ChecklistError`` sentence (an item number past the end, a task
        that no longer exists).
    """
    task_id = bound_task_id(context.agent.id)
    if task_id is None:
        return error_result(parsed.raw, _NO_TASK, cwd=context.cwd)
    task = db.get_task(task_id)
    if task is None:
        return error_result(parsed.raw, f"Your current task {task_id} no longer exists.", cwd=context.cwd)
    args = parsed.args
    if not args:
        if (content or "").strip():
            return _usage_error(context, parsed, "checklist takes no body; to add items use checklist add.")
        return _answer(context, parsed, task, task.checklist, kind="checklist", detail="checked the checklist")
    subcommand = args[0]
    if subcommand == "add":
        return _add(context, parsed, task, content)
    if subcommand == "done":
        return _resolve(context, parsed, task, content, status="done")
    if subcommand == "skip":
        return _resolve(context, parsed, task, content, status="skipped")
    return _usage_error(context, parsed, f'Unknown checklist form "{subcommand}".')


def parse_checklist_body(text: str) -> list[str]:
    """Split an ``add`` body into item texts: one per non-blank line.

    One leading list marker (``- ``, ``* ``, ``• ``, ``N. `` or ``N) ``) is
    dropped from each line, so a model's habitual numbering does not double
    up as "1. 1. …" against the checklist's own numbers.

    Args:
        text: The body as the agent wrote it.

    Returns:
        The item texts, stripped, in order; ``[]`` when every line is blank.
    """
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        items.append(_LIST_MARKER_RE.sub("", stripped, count=1))
    return items


def _add(
    context: CliExecutionContext, parsed: ParsedCliCommand, task: Task, content: str | None,
) -> BossModCliResult:
    if len(parsed.args) != 1:
        return _usage_error(context, parsed, "checklist add takes no arguments; put one item per line in the body.")
    texts = parse_checklist_body(content or "")
    if not texts:
        return error_result(parsed.raw, _EMPTY_ADD, cwd=context.cwd)
    try:
        items = add_checklist_items(task.id, texts)
    except ChecklistError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    added = f"{len(texts)} checklist item" if len(texts) == 1 else f"{len(texts)} checklist items"
    return _answer(
        context, parsed, task, items, kind="checklist", detail=f"added {added}", changed=True,
    )


def _resolve(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    task: Task,
    content: str | None,
    *,
    status: Literal["done", "skipped"],
) -> BossModCliResult:
    number = _item_number(parsed.args)
    if number is None:
        return error_result(parsed.raw, _BAD_NUMBER, cwd=context.cwd)
    note = (content or "").strip() or None
    if status == "skipped" and note is None:
        return error_result(parsed.raw, _EMPTY_SKIP, cwd=context.cwd)
    try:
        item, items = resolve_checklist_item(task.id, number, status=status, note=note)
    except ChecklistError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    return _answer(
        context,
        parsed,
        task,
        items,
        kind="checklist_item_resolved",
        detail=f"marked checklist item {number} {status}",
        changed=True,
        handoff=_handoff(number, item, task.model_copy(update={"checklist": items})),
    )


def _answer(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    task: Task,
    items: list[TaskChecklistItem],
    *,
    kind: str,
    detail: str,
    changed: bool = False,
    handoff: str | None = None,
) -> BossModCliResult:
    """The success result: the checklist section and the declared side effects."""
    data: dict[str, object] = {
        "task_id": task.id,
        "checklist": [item.model_dump() for item in items],
    }
    if changed:
        data["activity"] = {
            "event": "task_updated",
            "detail": f'{context.agent.name} updated the checklist on "{task.title}"',
            "extra": {"agent_id": context.agent.id, "task_id": task.id},
        }
    if handoff is not None:
        data["work_turn_boundary"] = {"handoff": handoff}
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} {detail} via BossMod CLI",
        kind=kind,
        data=data,
        sections=[(f"CHECKLIST — {task.title}", format_checklist_for_context(items) or _EMPTY_VIEW)],
        cwd=context.cwd,
    )


def _handoff(number: int, item: TaskChecklistItem, task: Task) -> str:
    """The note that replaces the work's saved steps once item ``number`` is checked off."""
    return render_default_prompt(
        _HANDOFF_PROMPT,
        {"number": number, "text": item.text, "open_items": bool(open_checklist_items(task))},
        allowed_paths=_HANDOFF_PATHS,
    )


def _item_number(args: tuple[str, ...]) -> int | None:
    """The ``<n>`` of ``done <n>`` / ``skip <n>`` as a positive whole number, or None."""
    if len(args) != 2:
        return None
    token = args[1]
    # isascii + isdigit: int() alone would take "+3", " 3" or "1_0".
    if not (token.isascii() and token.isdigit()):
        return None
    value = int(token, 10)
    return value if value >= 1 else None


def _usage_error(context: CliExecutionContext, parsed: ParsedCliCommand, reason: str) -> BossModCliResult:
    forms = "\n".join(f"  {form}" for form in CHECKLIST_FORMS)
    return error_result(parsed.raw, f"{reason}\nUsage:\n{forms}", cwd=context.cwd)
