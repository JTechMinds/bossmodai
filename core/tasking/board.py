"""BossMod AI — Shared board views for tasks and manager rollups."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.role_contracts import is_auditor_specialty, operator_done_claim_guidance
from core.bm_cli.filesystem import slugify_name
from core.models import Agent, Task, TaskEvent
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
    """Convert board objects into JSON-safe dictionaries.

    Reads a fixed number of statements however many tasks the board holds:
    one batch for every agent the tasks name and one for each task's latest
    event. A task shown in several sections is serialized once, and the
    sections share that one dict, so treat the result as read-only.

    Args:
        board: A :func:`build_task_board` result.

    Returns:
        The board with every ``Task`` replaced by its serialized row.
    """
    sections = board.get("sections") or {}
    children = board.get("child_tasks_by_parent") or {}
    serialize = _task_serializer([
        board.get("current_task"),
        *(task for rows in sections.values() for task in rows),
        *(task for rows in children.values() for task in rows),
    ])
    return {
        "scope": board["scope"],
        "current_task": serialize(board.get("current_task")),
        "sections": {
            key: [serialize(item) for item in value]
            for key, value in sections.items()
        },
        "assignee_rollup": board.get("assignee_rollup") or [],
        "child_tasks_by_parent": {
            key: [serialize(item) for item in value]
            for key, value in children.items()
        },
    }


def build_project_summary(agent_id: str, *, current_task_id: str | None = None) -> list[dict[str, Any]]:
    """Return a compact project-level rollup for the given agent."""
    relevant = _open_tasks(assigned_to=agent_id)
    owned_statuses = ("complete", "blocked", "delegated", "abandoned")
    owned = db.list_tasks_by_statuses(owned_statuses, owner_id=agent_id)
    # Grouped back into status order: which three tasks a project shows
    # depends on this order, which was one read per status.
    for status in owned_statuses:
        for task in owned:
            if task.status == status and all(existing.id != task.id for existing in relevant):
                relevant.append(task)
    for task in _open_tasks(owner_id=agent_id):
        if all(existing.id != task.id for existing in relevant):
            relevant.append(task)
    agents = _agents_named_by(relevant)

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
            assignee = agents.get(task.assigned_to) if task.assigned_to else None
            bucket["latest_tasks"].append(
                {
                    "title": task.title,
                    "status": task.status,
                    "assigned_to": task.assigned_to,
                    "assignee_name": assignee.name if assignee is not None else None,
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
    recent_completed_delegated = [
        task
        for task in db.list_recent_tasks(owner_id=agent_id, status="complete", limit=6)
        if task.assigned_to and task.assigned_to != agent_id
    ]
    return {
        "scope": "owned",
        "current_task": _current_task(agent_id),
        "sections": {
            "tasks_i_own": owned,
            "tasks_i_delegated": delegated,
            "recent_completed_delegated_tasks": recent_completed_delegated,
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


def next_board_task(task: Any, *, author_id: str | None = None) -> Task | None:
    """Return the next open card after ``task``.

    A later open sibling comes first. Otherwise the next open card on the
    same channel, in created order. The author and the human operator are
    not that owner. There is no chat-text match.
    """
    if task is None:
        return None
    author = (author_id or "").strip()
    parent_id = str(getattr(task, "parent_task_id", None) or "").strip()
    channel_id = str(getattr(task, "notification_channel_id", None) or "").strip()
    if parent_id:
        card = _later_open_card(task, author_id=author, parent_task_id=parent_id)
        if card is not None:
            return card
    if not channel_id:
        return None
    return _later_open_card(task, author_id=author, notification_channel_id=channel_id)


def next_board_owner_id(task: Any, *, author_id: str | None = None) -> str | None:
    """Return who should wake for the next open card after ``task``."""
    card = next_board_task(task, author_id=author_id)
    if card is None:
        return None
    return str(card.assigned_to or "").strip() or str(card.owner_id or "").strip() or None


def pending_channel_card_for_owner(
    channel_id: str,
    owner_id: str,
    *,
    exclude_task_ids: set[str] | None = None,
) -> Task | None:
    """Earliest pending channel card already assigned to ``owner_id``.

    Pending is the Work bind. Accepted, active, and Soft-blocked cards are
    left alone.
    """
    token = (channel_id or "").strip()
    owner = (owner_id or "").strip()
    if not token or not owner or owner == HUMAN_SENDER_ID:
        return None
    skipped = exclude_task_ids or set()
    rows = [
        item
        for item in db.list_tasks(
            notification_channel_id=token,
            assigned_to=owner,
            status="pending",
        )
        if item.id not in skipped and getattr(item, "source_channel", None) == "channel"
    ]
    if not rows:
        return None
    rows.sort(key=lambda item: (item.created_at, item.id))
    return rows[0]


def _later_open_card(
    task: Any,
    *,
    author_id: str,
    parent_task_id: str | None = None,
    notification_channel_id: str | None = None,
) -> Task | None:
    """First open card strictly after ``task`` in this pool, skipping the author."""
    rows = db.list_tasks_by_statuses(
        OPEN_TASK_STATUSES,
        parent_task_id=parent_task_id,
        notification_channel_id=notification_channel_id,
    )
    rows.sort(key=lambda item: (item.created_at, item.id))
    for item in rows:
        if item.id == task.id or item.created_at < task.created_at:
            continue
        owner = str(item.assigned_to or "").strip() or str(item.owner_id or "").strip()
        if not owner or owner == author_id or owner == HUMAN_SENDER_ID:
            continue
        return item
    return None


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
    tasks = db.list_tasks_by_statuses(
        OPEN_TASK_STATUSES,
        assigned_to=assigned_to,
        owner_id=owner_id,
        requester_id=requester_id,
        parent_task_id=parent_task_id,
    )
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _tasks_from_ids(task_ids: set[str]) -> list[Task]:
    tasks = list(db.get_tasks_by_ids(list(task_ids)).values())
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
    agents = _agents_named_by(tasks)
    for task in tasks:
        if not task.assigned_to:
            continue
        agent = agents.get(task.assigned_to)
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


def _agents_named_by(tasks: Iterable[Task]) -> dict[str, Agent]:
    """Every agent a task names as assignee, owner or requester, in one read.

    The human operator's id and ids with no agent row are simply absent,
    the same answer a per-id ``db.get_agent`` gives.
    """
    ids: list[str] = []
    for task in tasks:
        for agent_id in (task.assigned_to, task.owner_id, task.requester_id):
            if agent_id and agent_id != HUMAN_SENDER_ID:
                ids.append(agent_id)
    return db.get_agents_by_ids(list(dict.fromkeys(ids)))


def _task_serializer(tasks: Iterable[Task | None]) -> Callable[[Task | None], dict[str, Any] | None]:
    """Prepare :func:`_serialize_task` for ``tasks`` and memoize it per task id.

    Two reads, whatever the count: the agents the tasks name, and each
    task's newest event. Tasks passed to the returned function must be among
    ``tasks``; ``None`` serializes to ``None``.
    """
    present = [task for task in tasks if task is not None]
    agents = _agents_named_by(present)
    task_ids = list(dict.fromkeys(task.id for task in present))
    newest = db.list_recent_task_events(task_ids, limit_per_task=1)
    memo: dict[str, dict[str, Any]] = {}

    def serialize(task: Task | None) -> dict[str, Any] | None:
        if task is None:
            return None
        row = memo.get(task.id)
        if row is None:
            events = newest.get(task.id) or []
            row = _serialize_task(task, agents=agents, latest_event=events[-1] if events else None)
            memo[task.id] = row
        return row

    return serialize


def _serialize_task(task: Task, *, agents: dict[str, Agent], latest_event: TaskEvent | None) -> dict[str, Any]:
    """Serialize one task from prebuilt lookups; reads nothing itself.

    Args:
        task: The task.
        agents: Agent id -> agent, covering the task's assignee, owner and
            requester (:func:`_agents_named_by`).
        latest_event: The task's newest event, or ``None`` when it has none.
    """
    assigned = agents.get(task.assigned_to) if task.assigned_to else None
    assigned_name = assigned.name if assigned is not None else None
    owner_name = None
    requester_name = None
    if task.owner_id:
        owner = agents.get(task.owner_id)
        owner_name = owner.name if owner is not None else None
    if task.requester_id and task.requester_id != HUMAN_SENDER_ID:
        requester = agents.get(task.requester_id)
        requester_name = requester.name if requester is not None else None
    elif task.requester_id == HUMAN_SENDER_ID:
        requester_name = "Human Operator"

    has_files = bool(task.work_contract and task.work_contract.deliverables)
    return {
        **task.model_dump(mode="json"),
        "assigned_to_name": assigned_name,
        "assigned_to_role": assigned.role if assigned is not None else None,
        "assigned_to_done_fail_bar": assigned.done_fail_bar if assigned is not None else None,
        "done_claim_guidance": operator_done_claim_guidance(
            auditor=is_auditor_specialty(assigned.role if assigned is not None else None),
            done_fail_bar=assigned.done_fail_bar if assigned is not None else None,
            has_file_deliverables=has_files,
        ),
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
