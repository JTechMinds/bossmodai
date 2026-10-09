"""BossMod AI — Operator task actions: edit, reassign, requirements, resume and mark complete.

The operator is the final judge of a task. These are the domain-layer verbs
behind the task detail's Edit mode (routes stay thin). Every status change
goes through ``transition_task``; nothing here widens the state machine.

Re-present: reassigning a task, or changing what it asks for, means the
assignee must look at it again. That is one step: stop live work, drop queued
wakes, move the task to ``pending`` and enqueue ``task_assigned`` — the only
trigger whose decision contract lets an agent (re)accept work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import assignment_wake_trigger, build_task_update_trigger
from core.agent_loop.deliverables import build_work_contract
from core.agent_loop.task_followups import _CHILD_UPDATES_TO_PARENT_EVENT_TYPES
from core.agent_loop.task_origin_mirrors import mirror_origin_status, mirror_task_completed_by_operator
from core.agent_loop.task_roles import default_task_owner_id
from core.boss import boss_label
from core.floors import assert_assignment, assert_task_stays_on_floor
from core.models import Agent, Task, TaskUpdateRequest, WorkContract
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import append_task_event, list_open_child_tasks, rewrite_shared_work_contract
from core.tasking.transitions import (
    IllegalTaskTransition,
    assert_valid_task_transition,
    is_terminal_task_status,
    transition_task,
)

# The statuses the "Needs you" queue treats as blocked (api/routes/needs.py
# BLOCKED_STATUSES): the only ones the operator hands back with Resume.
OPERATOR_RESUMABLE_STATUSES = frozenset({"blocked", "stalled"})


@dataclass(frozen=True, slots=True)
class OperatorTaskResult:
    """What an operator action changed, for the transport to deliver.

    Attributes:
        task: The task as stored now.
        trigger_requests: Wake specs the caller must enqueue (``enqueue_trigger(**spec)``).
        posted_lines: Origin-thread lines that were persisted and must be broadcast.
    """

    task: Task
    trigger_requests: list[dict[str, Any]] = field(default_factory=list)
    posted_lines: list[dict[str, Any]] = field(default_factory=list)


class OpenChildTasks(ValueError):
    """The task still has open child tasks; the operator resolves those first."""

    def __init__(self, children: list[Task]) -> None:
        self.children = children
        super().__init__("Resolve the task's open subtasks before marking it complete")


def update_task_as_operator(task_id: str, changes: TaskUpdateRequest) -> OperatorTaskResult:
    """Apply the operator's edit to an open task: title, description, assignee, requirements, severity.

    Only fields present in ``changes.model_fields_set`` are considered, and
    only those that differ from the stored task are written. A reassign, or a
    description/requirements change on an assigned task, re-presents the task
    to its assignee (see module docstring). A title-only or severity-only
    edit wakes nobody: neither changes what the task asks for.

    Args:
        task_id: The task to edit.
        changes: The validated edit. ``assigned_to: None`` unassigns;
            ``work_contract: None`` or an empty deliverables list drops the
            file requirement.

    Returns:
        The stored task, the wake specs to enqueue and the origin lines posted.

    Raises:
        ValueError: No such task, no such assignee, the human operator named as
            assignee, or relative deliverable paths on an unassigned task.
        IllegalTaskTransition: The task is closed.
        FloorDenied: The new assignee is not on the task's floor (the stored
            task's floor, ``assert_task_stays_on_floor``), or the assignment
            would cross floors with the owner or thread.
        PathOutsideRootsError: A deliverable path resolves outside the agent's roots.
    """
    task = db.get_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    if is_terminal_task_status(task.status):
        raise IllegalTaskTransition(task.status, task.status)

    fields_set = changes.model_fields_set
    columns: dict[str, Any] = {}
    summary_bits: list[str] = []

    # The request model rejects a blank or null title, so a set title is text.
    if "title" in fields_set and changes.title is not None and changes.title != task.title:
        columns["title"] = changes.title
        summary_bits.append("title")

    description_changed = False
    if "description" in fields_set:
        wanted = (changes.description or "").strip() or None
        if wanted != task.description:
            columns["description"] = wanted
            description_changed = True
            summary_bits.append("description")

    # The request model refuses a null severity, so a set severity is P0–P3.
    if "severity" in fields_set and changes.severity is not None and changes.severity != task.severity:
        columns["severity"] = changes.severity
        summary_bits.append("severity")

    parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
    old_assignee = db.get_agent(task.assigned_to) if task.assigned_to else None
    assignee_id = task.assigned_to
    assignee = old_assignee
    owner_id = task.owner_id
    assignee_changed = "assigned_to" in fields_set and changes.assigned_to != task.assigned_to
    if assignee_changed:
        assignee_id = changes.assigned_to
        assignee = _validate_new_assignee(assignee_id)
        # Checked on the stored task, before the owner moves: its floor is
        # where the work lives, whoever it goes to next.
        assert_task_stays_on_floor(task, assignee_id)
        if task.owner_id == task.assigned_to:
            # The owner was defaulted to the old assignee; it follows the work.
            owner_id = default_task_owner_id(
                assignee_id=assignee_id,
                parent_task=parent,
            )
        assert_assignment(
            assignee_id=assignee_id,
            owner_id=owner_id,
            channel_id=task.notification_channel_id,
            parent_channel_id=parent.notification_channel_id if parent is not None else None,
        )
        columns["assigned_to"] = assignee_id
        if owner_id != task.owner_id:
            columns["owner_id"] = owner_id
        summary_bits.append(
            f"reassigned from {_agent_label(old_assignee)} to {_agent_label(assignee)}"
        )

    contract_changed = False
    if "work_contract" in fields_set:
        contract = _normalize_contract(
            task,
            contract=changes.work_contract,
            assignee=assignee,
            owner_id=owner_id,
        )
        if contract != task.work_contract:
            columns["work_contract"] = contract
            contract_changed = True
            count = len(contract.deliverables) if contract is not None else 0
            summary_bits.append(
                f"requirements ({count} file{'' if count == 1 else 's'})" if count else "requirements (none)"
            )

    if not columns:
        return OperatorTaskResult(task=task)

    db.update_task(task.id, **columns)
    append_task_event(
        task_id=task.id,
        author_type="human",
        author_name=boss_label(),
        event_type="system",
        content=f"The boss edited the task: {'; '.join(summary_bits)}.",
    )

    trigger_requests: list[dict[str, Any]] = []
    if assignee_changed or ((description_changed or contract_changed) and assignee_id):
        reason = (
            f"Reassigned to {_agent_label(assignee)} by the boss"
            if assignee_changed
            else "Requirements changed by the boss"
        )
        _re_present(task, reason=reason)
        stored = db.get_task(task.id)
        if stored is None:
            raise RuntimeError(f"Failed to reload task {task.id} after operator edit")
        wake = assignment_wake_trigger(stored)
        if wake is not None:
            trigger_requests.append(wake)

    updated = db.get_task(task.id)
    if updated is None:
        raise RuntimeError(f"Failed to reload task {task.id} after operator edit")

    posted_lines: list[dict[str, Any]] = []
    if assignee_changed and old_assignee is not None and assignee is not None:
        posted = mirror_origin_status(
            task=updated,
            agent=old_assignee,
            kind="rerouted",
            target_name=assignee.name,
            reason="Reassigned by the boss",
        )
        if posted:
            posted_lines.append(posted)
    return OperatorTaskResult(task=updated, trigger_requests=trigger_requests, posted_lines=posted_lines)


def resume_task_as_operator(task_id: str) -> OperatorTaskResult:
    """Hand a blocked or stalled task back to its assignee, with nothing edited.

    This is the re-present step a reassign or a requirements change takes
    (see module docstring), without the change: live work stops, queued
    wakes are dropped, the task moves to ``pending`` and a fresh
    ``task_assigned`` wake is returned for the caller to enqueue. Nothing is
    destroyed, so it needs no confirmation.

    Args:
        task_id: The task to resume.

    Returns:
        The stored task (now ``pending``) and the assignee's wake to enqueue.
        No origin lines are posted.

    Raises:
        ValueError: No such task, or the task is not ``blocked``/``stalled``
            with an assignee.
        IllegalTaskTransition: The task is closed.
    """
    task = db.get_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    if is_terminal_task_status(task.status):
        raise IllegalTaskTransition(task.status, "pending")
    if not task.assigned_to or task.status not in OPERATOR_RESUMABLE_STATUSES:
        raise ValueError("Only a blocked or stalled task with an assignee can be resumed")

    _re_present(task, reason="Resumed by the boss")
    append_task_event(
        task_id=task.id,
        author_type="human",
        author_name=boss_label(),
        event_type="system",
        content="The boss resumed the task.",
    )
    stored = db.get_task(task.id)
    if stored is None:
        raise RuntimeError(f"Failed to reload task {task.id} after operator resume")
    trigger_requests: list[dict[str, Any]] = []
    wake = assignment_wake_trigger(stored)
    # None only when the assignee has left the task's floor; the task still
    # waits in pending, exactly as an operator edit leaves it.
    if wake is not None:
        trigger_requests.append(wake)
    return OperatorTaskResult(task=stored, trigger_requests=trigger_requests)


def complete_task_as_operator(task_id: str, *, summary: str) -> OperatorTaskResult:
    """Mark a task complete on the operator's behalf.

    The operator's judgment replaces the agent's done check (deliverables,
    claims). The state machine still applies: a ``pending`` task cannot jump
    to ``complete``. A delegated parent is told, the same way an agent
    completion tells it, so its assignee can close its own work.

    Args:
        task_id: The task to complete.
        summary: Why it is done, in the operator's words. It becomes the
            completion summary, the task event and the origin thread line.

    Returns:
        The stored task, the parent wake to enqueue (if any) and the origin
        line posted. Already-complete tasks return unchanged with empty lists.

    Raises:
        ValueError: No such task, or ``summary`` is blank.
        IllegalTaskTransition: The task is closed with another status, or
            its status cannot move to ``complete`` (``pending``).
        OpenChildTasks: The task still has open child tasks.
    """
    note = (summary or "").strip()
    if not note:
        raise ValueError("A task completion needs a summary")
    task = db.get_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    if task.status == "complete":
        return OperatorTaskResult(task=task)
    if is_terminal_task_status(task.status):
        raise IllegalTaskTransition(task.status, "complete")
    assert_valid_task_transition(task.status, "complete")
    children = list_open_child_tasks(parent_task_id=task.id)
    if children:
        raise OpenChildTasks(children)

    transition_task(
        task.id,
        "complete",
        reason=note,
        actor=boss_label(),
        actor_type="human",
        completion_summary=note,
        status_note=None,
        watchdog_pinged_at=None,
    )
    for activity in db.list_activities(task_id=task.id, limit=200):
        if activity.status in {"active", "paused"}:
            activity_runtime.complete_activity(activity.id, detail=note)
    db.delete_queued_triggers_for_task(task.id)
    append_task_event(
        task_id=task.id,
        author_type="human",
        author_name=boss_label(),
        event_type="completion",
        content=f"Marked complete: {note}",
    )
    updated = db.get_task(task.id)
    if updated is None:
        raise RuntimeError(f"Failed to reload task {task.id} after operator completion")

    trigger_requests: list[dict[str, Any]] = []
    parent = db.get_task(updated.parent_task_id) if updated.parent_task_id else None
    if parent is not None:
        parent_note = f'Child task "{updated.title}" marked complete by the boss: {note}'
        parent_event = append_task_event(
            task_id=parent.id,
            author_type="system",
            author_name="BossMod",
            event_type=_CHILD_UPDATES_TO_PARENT_EVENT_TYPES["completion"],
            content=parent_note,
        )
        if parent.assigned_to:
            trigger_requests.append(
                build_task_update_trigger(
                    parent,
                    recipient_agent_id=parent.assigned_to,
                    from_agent=None,
                    from_name=boss_label(),
                    content=parent_note,
                    attention_kind="completion_report",
                    source_task_event_id=parent_event.id if parent_event is not None else None,
                )
            )

    posted_lines: list[dict[str, Any]] = []
    posted = mirror_task_completed_by_operator(updated, note)
    if posted:
        posted_lines.append(posted)
    return OperatorTaskResult(task=updated, trigger_requests=trigger_requests, posted_lines=posted_lines)


def _validate_new_assignee(assignee_id: str | None) -> Agent | None:
    """Return the new assignee, or ``None`` for an unassign. Raises ``ValueError`` otherwise."""
    if assignee_id is None:
        return None
    if assignee_id == HUMAN_SENDER_ID:
        raise ValueError("Task assignee must be an agent, not you.")
    agent = db.get_agent(assignee_id)
    if agent is None:
        raise ValueError("Assigned agent not found")
    return agent


def _normalize_contract(
    task: Task,
    *,
    contract: WorkContract | None,
    assignee: Agent | None,
    owner_id: str | None,
) -> WorkContract | None:
    """Normalize an edited contract the way task creation does, then apply the sharing policy."""
    if contract is None or not contract.deliverables:
        return None
    if assignee is not None:
        normalized = build_work_contract(
            contract.deliverables,
            agent_storage_key=assignee.storage_key,
            cwd=db.ensure_agent_cli_state(assignee.id).cwd,
        )
    elif any(item.type == "file" and not item.path.startswith("/") for item in contract.deliverables):
        raise ValueError(
            "Task work_contract file deliverables must use absolute BossMod CLI paths when assigned_to is omitted."
        )
    else:
        normalized = contract
    rewritten = rewrite_shared_work_contract(
        task=task,
        work_contract=normalized,
        assigned_to=assignee.id if assignee is not None else None,
        requester_id=task.requester_id,
        owner_id=owner_id,
    )
    return rewritten if rewritten is not None else normalized


def _re_present(task: Task, *, reason: str) -> None:
    """Stop live work and drop queued wakes; move a non-pending task back to ``pending``.

    A pending task only loses its queued wakes: its stale ``task_assigned``
    (possibly for the old assignee) is replaced by the caller's fresh one.
    """
    if task.status != "pending":
        for activity in db.list_activities(task_id=task.id, limit=200):
            if activity.status in {"active", "paused"}:
                activity_runtime.cancel_activity(activity.id, detail="The boss changed the task")
    db.delete_queued_triggers_for_task(task.id)
    if task.status != "pending":
        transition_task(
            task.id,
            "pending",
            reason=reason,
            actor=boss_label(),
            actor_type="human",
            status_note=reason,
            watchdog_pinged_at=None,
        )


def _agent_label(agent: Agent | None) -> str:
    """Name an assignee for the audit line; ``nobody`` for an unassigned task."""
    return agent.name if agent is not None else "nobody"
