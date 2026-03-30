"""BossMod AI — Conservative board-first task resolution helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

import db
from core.models import Task

OPEN_TASK_STATUSES = ("pending", "accepted", "active", "blocked", "stalled")

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class TaskResolution:
    """Result of resolving a work-shaped request against the task board."""

    outcome: str
    task: Task | None = None
    candidates: tuple[Task, ...] = ()
    reason: str | None = None


def resolve_existing_task(
    *,
    assigned_to: str | None,
    requester_id: str | None,
    owner_id: str | None,
    parent_task_id: str | None,
    project: str | None,
    title: str,
    deliverable_paths: tuple[str, ...] = (),
) -> TaskResolution:
    """Return whether this work request should bind to an open existing task."""
    candidates = _candidate_tasks(
        assigned_to=assigned_to,
        requester_id=requester_id,
        owner_id=owner_id,
        parent_task_id=parent_task_id,
    )
    if not candidates:
        return TaskResolution(outcome="create_new_task")

    exact_matches: list[Task] = []
    for task in candidates:
        if _same_workstream(
            task,
            title=title,
            project=project,
            parent_task_id=parent_task_id,
            deliverable_paths=deliverable_paths,
            requester_id=requester_id,
            owner_id=owner_id,
            assigned_to=assigned_to,
        ):
            exact_matches.append(task)

    if len(exact_matches) == 1:
        return TaskResolution(
            outcome="bind_existing_task",
            task=exact_matches[0],
            candidates=tuple(exact_matches),
            reason="Open task matched the same workstream exactly.",
        )
    if len(exact_matches) > 1:
        return TaskResolution(
            outcome="clarify_ambiguous_match",
            candidates=tuple(exact_matches),
            reason="Multiple open tasks matched the same workstream.",
        )
    return TaskResolution(outcome="create_new_task")


def normalize_workstream_title(value: str | None) -> str:
    """Return a normalized workstream title."""
    return " ".join(_normalize_words(value or ""))


def task_deliverable_paths(task: Task | None) -> tuple[str, ...]:
    """Expose normalized deliverable paths for workstream matching."""
    if task is None:
        return ()
    return _task_deliverable_paths(task)


def _candidate_tasks(
    *,
    assigned_to: str | None,
    requester_id: str | None,
    owner_id: str | None,
    parent_task_id: str | None,
) -> list[Task]:
    seen: set[str] = set()
    tasks: list[Task] = []
    filters = [
        {"assigned_to": assigned_to, "parent_task_id": parent_task_id},
        {"assigned_to": assigned_to, "requester_id": requester_id},
        {"assigned_to": assigned_to, "owner_id": owner_id},
        {"parent_task_id": parent_task_id},
    ]
    for filter_fields in filters:
        clean_fields = {key: value for key, value in filter_fields.items() if value is not None}
        if not clean_fields:
            continue
        for status in OPEN_TASK_STATUSES:
            for task in db.list_tasks(status=status, **clean_fields):
                if task.id in seen:
                    continue
                seen.add(task.id)
                tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _same_workstream(
    task: Task,
    *,
    title: str,
    project: str | None,
    parent_task_id: str | None,
    deliverable_paths: tuple[str, ...],
    requester_id: str | None,
    owner_id: str | None,
    assigned_to: str | None,
) -> bool:
    if assigned_to is not None and task.assigned_to != assigned_to:
        return False
    if requester_id is not None and task.requester_id != requester_id:
        return False
    if owner_id is not None and task.owner_id != owner_id:
        return False
    if parent_task_id is not None and task.parent_task_id != parent_task_id:
        return False
    if _normalize_words(task.title) != _normalize_words(title):
        return False
    if _normalize_words(task.project or "") != _normalize_words(project or ""):
        return False
    if deliverable_paths and _task_deliverable_paths(task) != deliverable_paths:
        return False
    return True


def _normalize_words(value: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(value.lower()))


def _task_deliverable_paths(task: Task) -> tuple[str, ...]:
    if task.work_contract is None:
        return ()
    paths = [
        item.path.strip()
        for item in task.work_contract.deliverables
        if item.type == "file" and isinstance(item.path, str) and item.path.strip()
    ]
    return tuple(sorted(paths))
