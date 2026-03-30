"""BossMod AI — Central task creation/reuse and task-thread operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import db
from core.agent_loop.task_roles import default_task_owner_id
from core.models import Task
from core.models.message import HUMAN_SENDER_ID
from core.tasking.resolution import OPEN_TASK_STATUSES, TaskResolution, resolve_existing_task


@dataclass(frozen=True, slots=True)
class TaskCreateOrBindResult:
    """Structured result for board-first task creation."""

    task: Task
    outcome: str
    resolution: TaskResolution


def append_task_event(
    *,
    task_id: str,
    author_type: str,
    author_name: str,
    event_type: str,
    content: str,
    author_agent_id: str | None = None,
    source_message_id: str | None = None,
    source_trigger_id: str | None = None,
):
    """Persist one task-thread event when content is available."""
    text = str(content or "").strip()
    if not text:
        return None
    return db.create_task_event(
        task_id=task_id,
        author_type=author_type,
        author_agent_id=author_agent_id,
        author_name=author_name,
        event_type=event_type,
        content=text,
        source_message_id=source_message_id,
        source_trigger_id=source_trigger_id,
    )


def create_or_bind_task(
    *,
    title: str,
    description: str | None,
    project: str | None,
    assigned_to: str | None,
    requester_id: str | None,
    owner_id: str | None,
    created_by: str | None,
    parent_task_id: str | None,
    work_contract: Any | None,
    source_channel: str | None,
    notification_policy: str | None,
    notification_channel_id: str | None,
    audit_author_name: str,
    audit_author_type: str,
    audit_author_agent_id: str | None = None,
    audit_event_type: str = "assignment",
    audit_source_trigger_id: str | None = None,
) -> TaskCreateOrBindResult:
    """Create a task only when the board does not already contain the workstream."""
    requested_owner_id = owner_id or default_task_owner_id(
        assignee_id=assigned_to,
        requester_id=requester_id,
        created_by=created_by,
        parent_task=db.get_task(parent_task_id) if parent_task_id else None,
    )
    deliverable_paths = _deliverable_paths_from_contract(work_contract)
    resolution = resolve_existing_task(
        assigned_to=assigned_to,
        requester_id=requester_id,
        owner_id=requested_owner_id,
        parent_task_id=parent_task_id,
        project=project,
        title=title,
        deliverable_paths=deliverable_paths,
    )
    if resolution.outcome == "bind_existing_task" and resolution.task is not None:
        append_task_event(
            task_id=resolution.task.id,
            author_type="system",
            author_name="BossMod",
            event_type="system",
            content="Reused the existing open task for the same workstream.",
            source_trigger_id=audit_source_trigger_id,
        )
        return TaskCreateOrBindResult(task=resolution.task, outcome=resolution.outcome, resolution=resolution)

    task = db.create_task(
        title=title,
        description=description,
        project=project,
        assigned_to=assigned_to,
        requester_id=requester_id,
        owner_id=requested_owner_id,
        created_by=created_by,
        parent_task_id=parent_task_id,
        work_contract=work_contract,
        source_channel=source_channel,
        notification_policy=notification_policy,
        notification_channel_id=notification_channel_id,
    )
    append_task_event(
        task_id=task.id,
        author_type=audit_author_type,
        author_name=audit_author_name,
        author_agent_id=audit_author_agent_id or _agent_id_for_author(created_by, audit_author_type),
        event_type=audit_event_type,
        content=_task_creation_event_content(task),
        source_trigger_id=audit_source_trigger_id,
    )
    return TaskCreateOrBindResult(task=task, outcome="create_new_task", resolution=resolution)


def create_or_bind_subtask(
    *,
    parent_task: Task,
    title: str,
    description: str | None,
    project: str | None,
    assigned_to: str,
    requester_id: str | None,
    owner_id: str | None,
    created_by: str | None,
    work_contract: Any | None,
    source_channel: str | None,
    notification_policy: str | None,
    notification_channel_id: str | None,
    audit_author_name: str,
    audit_author_type: str,
    audit_author_agent_id: str | None = None,
    audit_source_trigger_id: str | None = None,
) -> TaskCreateOrBindResult:
    """Create or reuse a child task for one parent workstream."""
    result = create_or_bind_task(
        title=title,
        description=description,
        project=project or parent_task.project,
        assigned_to=assigned_to,
        requester_id=requester_id,
        owner_id=owner_id,
        created_by=created_by,
        parent_task_id=parent_task.id,
        work_contract=work_contract,
        source_channel=source_channel or parent_task.source_channel,
        notification_policy=notification_policy or parent_task.notification_policy,
        notification_channel_id=notification_channel_id or parent_task.notification_channel_id,
        audit_author_name=audit_author_name,
        audit_author_type=audit_author_type,
        audit_author_agent_id=audit_author_agent_id,
        audit_event_type="assignment",
        audit_source_trigger_id=audit_source_trigger_id,
    )
    if result.outcome == "create_new_task":
        append_task_event(
            task_id=parent_task.id,
            author_type=audit_author_type,
            author_name=audit_author_name,
            author_agent_id=audit_author_agent_id or _agent_id_for_author(created_by, audit_author_type),
            event_type="assignment",
            content=f'Created child task "{result.task.title}" for delegated work.',
            source_trigger_id=audit_source_trigger_id,
        )
    return result


def list_open_child_tasks(*, parent_task_id: str, assigned_to: str | None = None) -> list[Task]:
    """Return open child tasks for one parent task, optionally filtered by assignee."""
    tasks: list[Task] = []
    seen: set[str] = set()
    for status in OPEN_TASK_STATUSES:
        for task in db.list_tasks(parent_task_id=parent_task_id, assigned_to=assigned_to, status=status):
            if task.id in seen:
                continue
            seen.add(task.id)
            tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _deliverable_paths_from_contract(work_contract: Any | None) -> tuple[str, ...]:
    if work_contract is None:
        return ()
    if hasattr(work_contract, "deliverables"):
        deliverables = getattr(work_contract, "deliverables") or []
    else:
        deliverables = (work_contract.get("deliverables") or []) if isinstance(work_contract, dict) else []
    paths = [
        str(item.path if hasattr(item, "path") else item.get("path")).strip()
        for item in deliverables
        if ((getattr(item, "type", None) if hasattr(item, "type") else item.get("type")) == "file")
        and str(item.path if hasattr(item, "path") else item.get("path") or "").strip()
    ]
    return tuple(sorted(paths))


def _task_creation_event_content(task: Task) -> str:
    assignee_name = None
    if task.assigned_to:
        assignee = db.get_agent(task.assigned_to)
        assignee_name = assignee.name if assignee is not None else task.assigned_to
    if assignee_name:
        return f'Created task "{task.title}" for {assignee_name}.'
    return f'Created task "{task.title}".'


def _agent_id_for_author(created_by: str | None, author_type: str) -> str | None:
    if author_type != "agent":
        return None
    if not created_by or created_by == HUMAN_SENDER_ID:
        return None
    return created_by
