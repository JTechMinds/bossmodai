"""BossMod AI — The floor's backlog: unassigned work filed for the boss to triage.

Backlog = a top-level task that is ``pending`` with no assignee. That is
the same definition the watchdog uses ("Pending-without-assignee is
backlog, not in-flight work"), and it leaves out delegated children the
operator unassigned. ``db.list_backlog_tasks`` is the one query that
applies it.

An agent files into the backlog with the ``backlog`` CLI command
(core/bm_cli/backlog_commands.py, the transport); the rules live here.
A filing has no assignee, no owner and no parent, and wakes nobody: the
operator triages it, and ownership lands on the first assignee
(``default_task_owner_id``). It sits on the reporter's floor
(``task_floor_id``'s requester fallback).
"""

from __future__ import annotations

from dataclasses import dataclass

import db
from core.floors import FloorDenied, home_floor_id, task_floor_id
from core.models import Agent, Task
from core.models.task import BacklogItemCreate
from core.tasking.references import RejectedReference, check_references
from core.tasking.service import create_or_bind_task

BACKLOG_STATUS = "pending"

NO_FLOOR_REASON = "You are not on a floor, so there is no backlog to file into."


@dataclass(frozen=True)
class BacklogFiling:
    """What filing one backlog item did.

    Attributes:
        task: The new task (``create_new_task``), the existing one
            (``bind_existing_task``), or None (``clarify_ambiguous_match``).
        outcome: ``create_or_bind_task``'s outcome, unchanged.
        candidates: The open tasks that matched, for a bind or a clarify.
        rejected_references: References that were not linked, with why.
    """

    task: Task | None
    outcome: str
    candidates: tuple[Task, ...]
    rejected_references: tuple[RejectedReference, ...]


def file_backlog_item(agent: Agent, item: BacklogItemCreate) -> BacklogFiling:
    """File one unassigned, unowned, unparented backlog item reported by ``agent``.

    The item is independent of whatever task the agent is working on, so a
    scheduled run that files several items can still close. A reference that
    fails the rules (``check_references``) is left off and reported back; it
    never fails the filing. A bind (the same reporter already has an open
    task with this title) leaves the existing task's severity and references
    as they are.

    Args:
        agent: The reporter; becomes the requester and creator.
        item: The validated ``backlog add`` body.

    Returns:
        The filing's task, outcome, matching candidates and rejected references.

    Raises:
        FloorDenied: The agent has no home floor (on vacation): the item
            would sit on no floor, where no agent could ever see it.
    """
    if not home_floor_id(agent.id):
        raise FloorDenied(NO_FLOOR_REASON)
    kept, rejected = check_references(agent, item.references)
    result = create_or_bind_task(
        title=item.title,
        description=item.description,
        project=item.project,
        assigned_to=None,
        requester_id=agent.id,
        owner_id=None,
        created_by=agent.id,
        parent_task_id=None,
        work_contract=None,
        source_channel="work",
        notification_policy="none",
        notification_channel_id=None,
        severity=item.severity,
        references=kept,
        audit_author_type="agent",
        audit_author_name=agent.name,
        audit_author_agent_id=agent.id,
        audit_event_type="assignment",
    )
    return BacklogFiling(
        task=result.task,
        outcome=result.outcome,
        candidates=result.resolution.candidates,
        rejected_references=tuple(rejected),
    )


def list_floor_backlog(agent_id: str, *, query: str | None, limit: int) -> tuple[list[Task], int]:
    """The open backlog on ``agent_id``'s floor, newest first, optionally searched.

    A backlog item with no floor (operator-filed with no agent on it)
    matches no agent.

    Args:
        agent_id: Whose floor to read.
        query: Case-insensitive text matched against the title or the
            description; None or blank matches everything.
        limit: The most rows to return.

    Returns:
        ``(rows, total)``: at most ``limit`` matching tasks, and how many matched.

    Raises:
        FloorDenied: The agent has no home floor (on vacation).
    """
    floor_id = home_floor_id(agent_id)
    if not floor_id:
        raise FloorDenied(NO_FLOOR_REASON)
    needle = (query or "").strip().lower()
    matches = [
        task
        for task in db.list_backlog_tasks(status=BACKLOG_STATUS)
        if task_floor_id(task) == floor_id and _matches(task, needle)
    ]
    return matches[:limit], len(matches)


def _matches(task: Task, needle: str) -> bool:
    if not needle:
        return True
    return needle in task.title.lower() or needle in (task.description or "").lower()
