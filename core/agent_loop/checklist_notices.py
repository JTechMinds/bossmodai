"""BossMod AI — the boss's note when an agent checks off a checklist item.

A long task worked through its checklist shows no progress in chat unless
each check-off says so. Every ``checklist done <n>`` / ``checklist skip <n>``
posts one system line in the boss's DM with the assignee, whatever the
task's origin (a thread, a peer's delegation, a schedule) and its
notification policy: the line is the boss's view of the agent's own work,
not a reply to the requester. The text and note are verbatim, and the task
title is named because one DM mixes all of an agent's tasks.

The line is of the ``checklist`` kind, which is operator-only
(``OPERATOR_ONLY_NOTIFICATION_KINDS``): ``db.list_notifications`` leaves it
out unless an operator view asks, so no agent prompt ever reads it. Kept
apart from core/tasking/checklist.py so the checklist rules stay free of the
chat layer, the same split as core/agent_loop/memory_notices.py.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.notifications import ChatNotification, persist_chat_notification
from core.models import Agent, Task
from core.models.task import TaskChecklistItem


def checklist_change_line(agent: Agent, task: Task, number: int, items: list[TaskChecklistItem]) -> str:
    """Return the note's sentence for one resolved checklist item.

    Args:
        agent: The assignee that resolved the item.
        task: The task whose checklist it is; its title is named.
        number: The item's number, counting from 1.
        items: The whole checklist after the change, in item order.

    Returns:
        ``<Agent> checked off item <n> of <total> on "<title>": <text>``
        for a done item, or ``<Agent> skipped item …`` for a skipped one,
        followed by `` — <note>`` when the item has a note. Title, text and
        note are verbatim.

    Raises:
        IndexError: ``number`` names no item in ``items`` (a caller bug).
        ValueError: The item is still ``open`` (a caller bug: only a
            resolved item is announced).
    """
    # Checked explicitly: items[number - 1] would read item -1 for number 0.
    if not 1 <= number <= len(items):
        raise IndexError(f"checklist has {len(items)} items; there is no item {number}")
    item = items[number - 1]
    if item.status == "done":
        verb = "checked off"
    elif item.status == "skipped":
        verb = "skipped"
    else:
        raise ValueError(f"checklist item {number} is {item.status!r}; only a done or skipped item is announced")
    detail = item.text if item.note is None else f"{item.text} — {item.note}"
    return f'{agent.name} {verb} item {number} of {len(items)} on "{task.title}": {detail}'


def note_checklist_change(agent: Agent, task: Task, number: int, items: list[TaskChecklistItem]) -> dict[str, Any]:
    """Persist the boss's DM note for one checklist item the agent resolved.

    Persisted through ``persist_chat_notification`` directly, with no
    duplicate check: resolving an item again is a real action, so each
    check-off gets its own line. The note is never shown to the agent
    (``prompt_visibility=False``, and its operator-only kind keeps it out of
    every agent-facing read of ``db.list_notifications``).

    It is not broadcast here: the ``checklist`` command runs off the event
    loop, so it hands the persisted line back for the turn to broadcast
    (``data["origin_chrome"]`` on its CLI result).

    Args:
        agent: The assignee that resolved the item; the line goes to its DM.
        task: The task whose checklist it is.
        number: The item's number, counting from 1.
        items: The whole checklist after the change, in item order.

    Returns:
        ``{"chat_message": {...}}``, the persisted line's broadcast payload.

    Raises:
        IndexError: ``number`` names no item in ``items``.
        ValueError: The item is still ``open``.
    """
    line = checklist_change_line(agent, task, number, items)
    posted = persist_chat_notification(
        agent,
        ChatNotification(
            kind="checklist",
            content=line,
            source_channel="task",
            policy="all",
            prompt_visibility=False,
            task_id=task.id,
        ),
    )
    return {"chat_message": posted}
