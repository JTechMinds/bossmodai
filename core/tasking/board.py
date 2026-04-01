"""BossMod AI — Shared board views for tasks and manager rollups."""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop import activity_runtime
from core.bm_cli.filesystem import slugify_name
from core.models import Task
from core.models.message import HUMAN_SENDER_ID
from core.tasking.resolution import OPEN_TASK_STATUSES


def build_task_board(agent_id: str, *, scope: str) -> dict[str, Any]:
    """Return one board view for the given agent and scope."""
    if scope == "self":
        return _build_self_board(agent_id)
    if scope == "owned":
        return _build_owned_board(agent_id)
    if scope == "delegated":
        return _build_delegated_board(agent_id)
    raise ValueError(f"Unsupported board scope: {scope}")


def serialize_task_board(board: dict[str, Any]) -> dict[str, Any]:
    """Convert board objects into JSON-safe dictionaries."""
    return {
        "scope": board["scope"],
        "current_task": _serialize_task(board.get("current_task")),
        "sections": {
            key: [_serialize_task(item) for item in value]
            for key, value in (board.get("sections") or {}).items()
        },
        "assignee_rollup": board.get("assignee_rollup") or [],
        "child_tasks_by_parent": {
            key: [_serialize_task(item) for item in value]
            for key, value in (board.get("child_tasks_by_parent") or {}).items()
        },
    }


def build_project_summary(agent_id: str, *, current_task_id: str | None = None) -> list[dict[str, Any]]:
    """Return a compact project-level rollup for the given agent."""
    relevant = _open_tasks(assigned_to=agent_id)
    for status in ("complete", "blocked", "delegated", "abandoned"):
        for task in db.list_tasks(owner_id=agent_id, status=status):
            if all(existing.id != task.id for existing in relevant):
                relevant.append(task)
    for task in _open_tasks(owner_id=agent_id):
        if all(existing.id != task.id for existing in relevant):
            relevant.append(task)

    grouped: dict[str, dict[str, Any]] = {}
    for task in relevant:
        project = str(task.project or "").strip()
        if not project:
            continue
        bucket = grouped.setdefault(
            project,
            {
                "project": project,
                "path": f"/projects/{slugify_name(project)}",
                "counts": {},
                "latest_tasks": [],
                "sort_ts": task.last_activity,
            },
        )
        bucket["counts"][task.status] = bucket["counts"].get(task.status, 0) + 1
        bucket["sort_ts"] = max(bucket["sort_ts"], task.last_activity)
        if len(bucket["latest_tasks"]) < 3 and task.id != current_task_id:
            latest_row = _serialize_task(task) or {}
            bucket["latest_tasks"].append(
                {
                    "title": task.title,
                    "status": task.status,
                    "assigned_to": task.assigned_to,
                    "assignee_name": latest_row.get("assigned_to_name"),
                }
            )

    ordered = sorted(grouped.values(), key=lambda item: item["sort_ts"], reverse=True)
    return [
        {
            "project": item["project"],
            "path": item["path"],
            "counts": item["counts"],
            "latest_tasks": item["latest_tasks"],
        }
        for item in ordered[:3]
    ]


def _build_self_board(agent_id: str) -> dict[str, Any]:
    current_task = _current_task(agent_id)
    open_tasks = _open_tasks(assigned_to=agent_id)
    blocked = [task for task in open_tasks if task.status in {"blocked", "stalled"}]
    waiting = [task for task in open_tasks if task.status == "waiting"]
    pending_decisions = [task for task in open_tasks if task.status in {"pending", "accepted"}]
    recent_completed_ids = {row["id"] for row in db.get_recent_completed_tasks(agent_id, limit=5)}
    recent_completed = _tasks_from_ids(recent_completed_ids)
    return {
        "scope": "self",
        "current_task": current_task,
        "sections": {
            "my_open_tasks": open_tasks,
            "my_waiting_tasks": waiting,
            "my_blocked_tasks": blocked,
            "recent_completed_tasks": recent_completed,
            "tasks_waiting_on_me": pending_decisions,
        },
        "assignee_rollup": [],
        "child_tasks_by_parent": {},
    }


def _build_owned_board(agent_id: str) -> dict[str, Any]:
    owned = _open_tasks(owner_id=agent_id)
    delegated = [task for task in owned if task.assigned_to and task.assigned_to != agent_id]
    blocked = [task for task in delegated if task.status in {"blocked", "stalled"}]
    waiting = [task for task in delegated if task.status == "waiting"]
    waiting_on_owner = [task for task in delegated if task.status in {"pending", "blocked", "stalled"}]
    return {
        "scope": "owned",
        "current_task": _current_task(agent_id),
        "sections": {
            "tasks_i_own": owned,
            "tasks_i_delegated": delegated,
            "waiting_child_tasks": waiting,
            "blocked_or_stalled_child_tasks": blocked,
            "tasks_waiting_on_me": waiting_on_owner,
        },
        "assignee_rollup": _assignee_rollup(delegated),
        "child_tasks_by_parent": _group_children_by_parent(delegated),
    }


def _build_delegated_board(agent_id: str) -> dict[str, Any]:
    delegated = [task for task in _open_tasks(owner_id=agent_id) if task.assigned_to and task.assigned_to != agent_id]
    return {
        "scope": "delegated",
        "current_task": _current_task(agent_id),
        "sections": {
            "tasks_i_delegated": delegated,
        },
        "assignee_rollup": _assignee_rollup(delegated),
        "child_tasks_by_parent": _group_children_by_parent(delegated),
    }


def _current_task(agent_id: str) -> Task | None:
    active_task_id = activity_runtime.get_active_task_id(agent_id)
    if not active_task_id:
        return None
    return db.get_task(active_task_id)


def _open_tasks(
    *,
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    for status in OPEN_TASK_STATUSES:
        for task in db.list_tasks(
            assigned_to=assigned_to,
            owner_id=owner_id,
            requester_id=requester_id,
            parent_task_id=parent_task_id,
            status=status,
        ):
            if task.id in seen:
                continue
            seen.add(task.id)
            tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _tasks_from_ids(task_ids: set[str]) -> list[Task]:
    tasks: list[Task] = []
    for task_id in task_ids:
        task = db.get_task(task_id)
        if task is not None:
            tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _group_children_by_parent(tasks: list[Task]) -> dict[str, list[Task]]:
    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        if not task.parent_task_id:
            continue
        grouped.setdefault(task.parent_task_id, []).append(task)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return grouped


def _assignee_rollup(tasks: list[Task]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not task.assigned_to:
            continue
        agent = db.get_agent(task.assigned_to)
        row = counts.setdefault(
            task.assigned_to,
            {
                "agent_id": task.assigned_to,
                "agent_name": agent.name if agent is not None else task.assigned_to,
                "counts": {},
            },
        )
        status_counts = row["counts"]
        status_counts[task.status] = int(status_counts.get(task.status, 0)) + 1
    rows = list(counts.values())
    rows.sort(key=lambda item: str(item["agent_name"]).lower())
    return rows


def _serialize_task(task: Task | None) -> dict[str, Any] | None:
    if task is None:
        return None
    assigned_name = None
    owner_name = None
    requester_name = None
    if task.assigned_to:
        assigned = db.get_agent(task.assigned_to)
        assigned_name = assigned.name if assigned is not None else None
    if task.owner_id:
        owner = db.get_agent(task.owner_id)
        owner_name = owner.name if owner is not None else None
    if task.requester_id and task.requester_id != HUMAN_SENDER_ID:
        requester = db.get_agent(task.requester_id)
        requester_name = requester.name if requester is not None else None
    elif task.requester_id == HUMAN_SENDER_ID:
        requester_name = "Human Operator"

    events = db.list_task_events(task.id, limit=5)
    latest_event = events[-1] if events else None
    return {
        **task.model_dump(mode="json"),
        "assigned_to_name": assigned_name,
        "owner_name": owner_name,
        "requester_name": requester_name,
        "latest_event": (
            {
                "event_type": latest_event.event_type,
                "author_name": latest_event.author_name,
                "content": latest_event.content,
                "created_at": latest_event.created_at,
            }
            if latest_event is not None
            else None
        ),
    }
