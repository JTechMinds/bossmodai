"""BossMod AI — Task checklist rules: the assignee plans its task in parts and checks them off.

A checklist is an ordered list of ``TaskChecklistItem`` that the assignee
writes for the task it is working on (the ``checklist`` command in
``core/bm_cli/checklist_commands.py`` is the only writer). Items are numbered
by position from 1 and the list is append-only: a part that is no longer
needed is skipped with a reason, never removed, so a number names the same
item for the life of the task.

This module holds the rules and the one formatter every view of a checklist
uses (the prompt's Current Task block, the CLI, the done feedback reads the
same items). It has no transport code and imports nothing from the agent
loop.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

import db
from core.models import Task
from core.models.task import TaskChecklistItem


class ChecklistError(ValueError):
    """A checklist change that cannot be made, worded so the agent can act on it."""


def add_checklist_items(task_id: str, texts: list[str]) -> list[TaskChecklistItem]:
    """Append one open item per text to a task's checklist.

    The read and the write run in one transaction, so the append always
    lands on the list it read.

    Args:
        task_id: The task whose checklist grows.
        texts: The new items' text, in order.

    Returns:
        The task's whole checklist after the append, in item order.

    Raises:
        ChecklistError: ``texts`` is empty, a text is blank, or the task
            does not exist.
    """
    if not texts:
        raise ChecklistError("Name at least one item to add, one per line.")
    try:
        new_items = [TaskChecklistItem(text=text) for text in texts]
    except ValidationError as exc:
        raise ChecklistError("Every checklist item needs text.") from exc
    with db.transaction():
        task = _require_task(task_id)
        items = [*task.checklist, *new_items]
        db.update_task(task_id, checklist=items)
    return items


def resolve_checklist_item(
    task_id: str,
    number: int,
    *,
    status: Literal["done", "skipped"],
    note: str | None,
) -> tuple[TaskChecklistItem, list[TaskChecklistItem]]:
    """Mark checklist item ``number`` done or skipped, with its note.

    An item already resolved may be resolved again and the latest answer
    wins: a better note, or skipped turned into done. The read and the write
    run in one transaction.

    Args:
        task_id: The task whose checklist changes.
        number: The item's number, counting from 1.
        status: ``done`` or ``skipped``.
        note: What was found or where the result is (``done``, optional), or
            why the item is not needed (``skipped``, required).

    Returns:
        ``(item, items)``: the resolved item and the whole checklist after
        the change, in item order.

    Raises:
        ChecklistError: The task does not exist, the checklist is empty,
            ``number`` names no item (the message says how many there are),
            or a skip has no note.
    """
    with db.transaction():
        task = _require_task(task_id)
        items = list(task.checklist)
        if not items:
            raise ChecklistError(
                f"Your checklist is empty, so there is no item {number}. Add items with checklist add first."
            )
        if not 1 <= number <= len(items):
            count = f"{len(items)} item" if len(items) == 1 else f"{len(items)} items"
            raise ChecklistError(f"There is no item {number}: your checklist has {count}.")
        current = items[number - 1]
        try:
            resolved = TaskChecklistItem(text=current.text, status=status, note=note)
        except ValidationError as exc:
            # The text and status are already valid, so this is the skip
            # without a reason.
            raise ChecklistError(
                f"checklist skip {number} needs a body: why the item is not needed."
            ) from exc
        items[number - 1] = resolved
        db.update_task(task_id, checklist=items)
    return resolved, items


def open_checklist_items(task: Task) -> list[tuple[int, TaskChecklistItem]]:
    """Return the task's items still open, each with its number.

    Args:
        task: The task to read.

    Returns:
        ``(number, item)`` pairs in item order; ``[]`` when nothing is open
        or the task has no checklist.
    """
    return [(number, item) for number, item in enumerate(task.checklist, start=1) if item.status == "open"]


def checklist_progress(items: list[TaskChecklistItem]) -> tuple[int, int]:
    """Return ``(resolved, total)``, where resolved counts done and skipped items.

    Args:
        items: A checklist, in item order.

    Returns:
        How many items are checked off, and how many there are.
    """
    resolved = sum(1 for item in items if item.status != "open")
    return resolved, len(items)


def format_checklist_for_context(items: list[TaskChecklistItem] | list[dict[str, Any]]) -> list[str]:
    """Lines for a checklist: a progress header, then one line per item.

    The only checklist formatter: the prompt's Current Task block and every
    CLI view use it, so the agent and each view read the same lines::

        checklist (1 of 3 checked off):
        1. [done] Audit core/tasking — 3 issues, in /projects/app/audit.md
        2. [open] Audit db/tasks.py
        3. [skipped] Audit the plugins — the boss dropped them

    Accepts the models (a ``Task``) or their dumps (the turn's current-task
    dict, a serialized board row), as ``format_references_for_context`` does.

    Args:
        items: A checklist, in item order.

    Returns:
        The lines; ``[]`` when there are no items.

    Raises:
        pydantic.ValidationError: A dumped item is malformed.
    """
    if not items:
        return []
    models = [item if isinstance(item, TaskChecklistItem) else TaskChecklistItem.model_validate(item) for item in items]
    resolved, total = checklist_progress(models)
    lines = [f"checklist ({resolved} of {total} checked off):"]
    for number, item in enumerate(models, start=1):
        line = f"{number}. [{item.status}] {item.text}"
        lines.append(f"{line} — {item.note}" if item.note else line)
    return lines


def _require_task(task_id: str) -> Task:
    """The task, or a ``ChecklistError`` saying it no longer exists."""
    task = db.get_task(task_id)
    if task is None:
        raise ChecklistError(f"Task {task_id} no longer exists, so its checklist cannot change.")
    return task
