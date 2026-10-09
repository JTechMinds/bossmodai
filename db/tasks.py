"""BossMod AI — Task CRUD."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from core.models import Task, TaskNotificationSettings, WorkContract
from core.models.task import DEFAULT_TASK_SEVERITY, TaskReference, TaskSeverity
from db.crud import build_update, insert_returning_dict, query
from db.task_notification_policies import delete_task_notification_settings, set_task_notification_settings
from db.task_notification_targets import delete_task_notification_target, set_task_notification_target_channel_id
from db.task_references import delete_task_references, set_task_references
from db.task_work_contracts import delete_task_work_contract, set_task_work_contract

_TASK_COLUMNS = (
    "t.id, t.title, t.description, t.project, t.assigned_to, t.requester_id, t.owner_id, t.created_by, "
    "t.status, t.severity, twc.work_contract, trf.refs AS \"references\", "
    "tnp.source_channel, tnp.policy AS notification_policy, tnp.updated_at AS notification_policy_updated_at, "
    "tnt.channel_id AS notification_channel_id, "
    "t.parent_task_id, t.schedule_id, t.cost_ceiling, t.completion_summary, "
    "t.status_note, t.watchdog_pinged_at, t.last_progress_at, t.last_heartbeat_at, "
    "t.last_activity, t.closed_at, t.created_at"
)

_TASK_VALID_COLUMNS = {
    "title", "description", "project", "assigned_to", "requester_id", "owner_id",
    "status", "severity", "parent_task_id", "cost_ceiling", "completion_summary",
    "status_note", "watchdog_pinged_at", "last_progress_at", "last_heartbeat_at",
    "last_activity", "closed_at",
}


def _validate_persisted_work_contract(work_contract: Any) -> WorkContract:
    """Validate the durable task contract shape and path invariants."""
    contract = WorkContract.model_validate(work_contract)
    for item in contract.deliverables:
        if item.type == "file" and not item.path.startswith("/"):
            raise ValueError('task work_contract file deliverables must use absolute BossMod CLI paths')
    return contract


def _validate_notification_settings(
    source_channel: Any,
    notification_policy: Any,
) -> TaskNotificationSettings:
    """Validate durable task notification settings."""
    return TaskNotificationSettings.model_validate(
        {
            "task_id": "placeholder",
            "source_channel": source_channel,
            "policy": notification_policy,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )


def create_task(
    title: str,
    description: str | None = None,
    project: str | None = None,
    assigned_to: str | None = None,
    requester_id: str | None = None,
    owner_id: str | None = None,
    created_by: str | None = None,
    parent_task_id: str | None = None,
    work_contract: Any | None = None,
    source_channel: str | None = None,
    notification_policy: str | None = None,
    notification_channel_id: str | None = None,
    schedule_id: str | None = None,
    severity: TaskSeverity = DEFAULT_TASK_SEVERITY,
    references: list[TaskReference] | None = None,
) -> Task:
    """Insert a new task.

    ``owner_id`` is stored as given: who owns a task is decided in
    ``core/agent_loop/task_roles.py`` ``default_task_owner_id``, never here.
    ``requester_id`` defaults to ``created_by``.

    ``schedule_id`` links a run to the schedule whose occurrence created it
    (core/scheduling/runner.py); every other task leaves it ``None``.
    ``severity`` is P0–P3 (P3 unless given). ``references`` are the
    documents the assignee reads first; they are stored as given, so the
    caller checks them (``core/tasking/references.py``).
    """
    validated_work_contract = None
    if work_contract is not None:
        validated_work_contract = _validate_persisted_work_contract(work_contract)
    validated_notification_settings = None
    if source_channel is not None or notification_policy is not None:
        if source_channel is None or notification_policy is None:
            raise ValueError("source_channel and notification_policy must be provided together")
        validated_notification_settings = _validate_notification_settings(source_channel, notification_policy)

    resolved_requester_id = requester_id if requester_id is not None else created_by

    row = insert_returning_dict(
        f"""
        INSERT INTO tasks (
            title, description, project, assigned_to, requester_id, owner_id, created_by, parent_task_id,
            schedule_id, severity
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        [
            title, description, project, assigned_to, resolved_requester_id, owner_id, created_by,
            parent_task_id, schedule_id, severity,
        ],
    )
    task_id = row["id"]
    if validated_work_contract is not None:
        set_task_work_contract(task_id, validated_work_contract)
    if references:
        set_task_references(task_id, references)
    if validated_notification_settings is not None:
        set_task_notification_settings(
            task_id,
            source_channel=validated_notification_settings.source_channel,
            policy=validated_notification_settings.policy,
        )
        set_task_notification_target_channel_id(task_id, notification_channel_id)
    task = get_task(task_id)
    if task is None:
        raise RuntimeError(f"Failed to reload created task {task_id}")
    return task


def _task_from_row(row: dict[str, Any]) -> Task:
    """Hydrate a task row, its optional work contract and its references.

    A task with no ``task_references`` row has no references (``[]``).
    """
    data = dict(row)
    raw_contract = data.get("work_contract")
    if raw_contract:
        data["work_contract"] = json.loads(raw_contract)
    else:
        data["work_contract"] = None
    raw_references = data.get("references")
    data["references"] = json.loads(raw_references) if raw_references else []
    return Task.model_validate(data)


def get_task(task_id: str) -> Task | None:
    """Fetch a single task by ID."""
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        WHERE t.id = $1
        """,
        [task_id],
    )
    if not rows:
        return None
    return _task_from_row(rows[0])


def get_tasks_by_ids(task_ids: Sequence[str]) -> dict[str, Task]:
    """Fetch many tasks in one read, keyed by id; the batch form of :func:`get_task`.

    Args:
        task_ids: Task ids; duplicates are fine. Empty reads nothing.

    Returns:
        ``{task_id: Task}``. Ids with no task are absent.
    """
    unique = list(dict.fromkeys(task_ids))
    if not unique:
        return {}
    placeholders = ", ".join(f"${index + 1}" for index in range(len(unique)))
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        WHERE t.id IN ({placeholders})
        """,
        unique,
    )
    tasks = [_task_from_row(row) for row in rows]
    return {task.id: task for task in tasks}


def _task_filter_conditions(
    params: list[Any],
    *,
    assigned_to: str | None,
    owner_id: str | None,
    requester_id: str | None,
    parent_task_id: str | None,
    notification_channel_id: str | None,
) -> list[str]:
    """SQL conditions for the optional task filters, appending their values to ``params``."""
    conditions: list[str] = []
    for column, value in (
        ("t.assigned_to", assigned_to),
        ("t.owner_id", owner_id),
        ("t.requester_id", requester_id),
        ("t.parent_task_id", parent_task_id),
        ("tnt.channel_id", notification_channel_id),
    ):
        if value is not None:
            params.append(value)
            conditions.append(f"{column} = ${len(params)}")
    return conditions


def list_tasks(
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    notification_channel_id: str | None = None,
    status: str | None = None,
) -> list[Task]:
    """Return tasks, optionally filtered by assignee, origin thread, and/or status."""
    params: list[Any] = []
    conditions = _task_filter_conditions(
        params,
        assigned_to=assigned_to,
        owner_id=owner_id,
        requester_id=requester_id,
        parent_task_id=parent_task_id,
        notification_channel_id=notification_channel_id,
    )
    if status is not None:
        params.append(status)
        conditions.append(f"t.status = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        {where}
        -- rowid breaks created_at ties (whole seconds) in insertion order.
        -- Without it the tie order would follow whichever index the planner
        -- picks (idx_tasks_status / _assigned_status / _owner_status).
        ORDER BY t.created_at, t.rowid
        """,
        params,
    )
    return [_task_from_row(row) for row in rows]


def list_tasks_by_statuses(
    statuses: Sequence[str],
    *,
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    notification_channel_id: str | None = None,
) -> list[Task]:
    """Return every task whose status is one of ``statuses``, in one query.

    The batch form of calling :func:`list_tasks` once per status. The
    optional filters are the same as :func:`list_tasks`'s and combine with
    AND.

    Args:
        statuses: Task statuses to include; duplicates are fine. Empty reads
            nothing.
        assigned_to, owner_id, requester_id, parent_task_id,
        notification_channel_id: Optional equality filters.

    Returns:
        Matching tasks, oldest first (``created_at``), like :func:`list_tasks`.
    """
    unique = list(dict.fromkeys(statuses))
    if not unique:
        return []
    params: list[Any] = list(unique)
    placeholders = ", ".join(f"${index + 1}" for index in range(len(unique)))
    conditions = [f"t.status IN ({placeholders})"]
    conditions.extend(_task_filter_conditions(
        params,
        assigned_to=assigned_to,
        owner_id=owner_id,
        requester_id=requester_id,
        parent_task_id=parent_task_id,
        notification_channel_id=notification_channel_id,
    ))
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        WHERE {' AND '.join(conditions)}
        -- rowid breaks created_at ties (whole seconds) in insertion order.
        -- Without it the tie order would follow whichever index the planner
        -- picks (idx_tasks_status / _assigned_status / _owner_status).
        ORDER BY t.created_at, t.rowid
        """,
        params,
    )
    return [_task_from_row(row) for row in rows]


def list_backlog_tasks(*, status: str) -> list[Task]:
    """Return every top-level, unassigned task in ``status``, newest first.

    The one query behind the backlog (``core/tasking/backlog.py``). The
    caller passes the backlog status, so this layer never defines what the
    backlog is.

    Args:
        status: The task status to match (``BACKLOG_STATUS``).

    Returns:
        Tasks with that status, no assignee and no parent, newest
        ``created_at`` first (insertion order breaks ties).
    """
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        WHERE t.status = $1 AND t.assigned_to IS NULL AND t.parent_task_id IS NULL
        ORDER BY t.created_at DESC, t.rowid DESC
        """,
        [status],
    )
    return [_task_from_row(row) for row in rows]


def list_open_task_ids_owned_by_missing_agents(
    *,
    terminal_statuses: Iterable[str],
    non_agent_ids: Iterable[str],
) -> list[str]:
    """Return ids of open tasks whose owner or assignee names an agent that no longer exists.

    Used only by ``core.agent_repository.AgentRepository.purge_orphans`` to
    cancel work an older delete left running with nobody on it. NULL is not
    an orphan, and neither is any value in ``non_agent_ids``.

    Args:
        terminal_statuses: Closed statuses (``TERMINAL_TASK_STATUSES``); every
            other status is open.
        non_agent_ids: Values these columns hold that are not agent ids
            (``HUMAN_SENDER_ID``). Must not be empty.

    Returns:
        Task ids, oldest first.

    Raises:
        ValueError: Either argument is empty.
    """
    closed = sorted({str(value) for value in terminal_statuses})
    sentinels = sorted({str(value) for value in non_agent_ids})
    if not closed or not sentinels:
        raise ValueError("terminal_statuses and non_agent_ids must both be given")
    params: list[Any] = [*closed, *sentinels]
    closed_marks = ", ".join(f"${index}" for index in range(1, len(closed) + 1))
    sentinel_marks = ", ".join(
        f"${index}" for index in range(len(closed) + 1, len(params) + 1)
    )

    def _missing(column: str) -> str:
        return (
            f"(t.{column} IS NOT NULL AND t.{column} NOT IN (SELECT id FROM agents) "
            f"AND t.{column} NOT IN ({sentinel_marks}))"
        )

    rows = query(
        f"""
        SELECT t.id
        FROM tasks t
        WHERE t.status NOT IN ({closed_marks})
          AND ({_missing("owner_id")} OR {_missing("assigned_to")})
        ORDER BY t.created_at, t.id
        """,
        params,
    )
    return [str(row["id"]) for row in rows]


def list_recent_tasks(
    *,
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> list[Task]:
    """Return tasks ordered by most recent activity first."""
    conditions: list[str] = []
    params: list[Any] = []

    if assigned_to is not None:
        params.append(assigned_to)
        conditions.append(f"t.assigned_to = ${len(params)}")
    if owner_id is not None:
        params.append(owner_id)
        conditions.append(f"t.owner_id = ${len(params)}")
    if requester_id is not None:
        params.append(requester_id)
        conditions.append(f"t.requester_id = ${len(params)}")
    if parent_task_id is not None:
        params.append(parent_task_id)
        conditions.append(f"t.parent_task_id = ${len(params)}")
    if status is not None:
        params.append(status)
        conditions.append(f"t.status = ${len(params)}")

    params.append(int(limit))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_references trf ON trf.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        {where}
        -- rowid makes the order total (both timestamps can tie); newest
        -- insert first, so whichever index the planner uses, ties read the same.
        ORDER BY t.last_activity DESC, t.created_at DESC, t.rowid DESC
        LIMIT ${len(params)}
        """,
        params,
    )
    return [_task_from_row(row) for row in rows]


def update_task(task_id: str, **fields: Any) -> Task | None:
    """Update task fields. Auto-updates last_activity on status change.

    Status changes go through the shared allow-map in
    ``core.tasking.transitions``. Illegal jumps raise
    ``IllegalTaskTransition`` and leave the row unchanged.

    ``references`` (a list of ``TaskReference``) replaces the task's
    references; an empty list removes them all.
    """
    if "status" in fields:
        from core.tasking.transitions import TERMINAL_TASK_STATUSES, assert_valid_task_transition

        current = get_task(task_id)
        if current is not None:
            target = str(fields["status"])
            assert_valid_task_transition(current.status, target)
            # Stamped on the way IN to a finished state, once. Finished states
            # have no way out (transitions.py), so nothing clears it, and the
            # identity update complete → complete must not move it either.
            if target in TERMINAL_TASK_STATUSES and current.status not in TERMINAL_TASK_STATUSES:
                fields.setdefault("closed_at", datetime.now(timezone.utc))

    work_contract = fields.pop("work_contract", None) if "work_contract" in fields else ...
    references = fields.pop("references", None) if "references" in fields else ...
    source_channel = fields.pop("source_channel", None) if "source_channel" in fields else ...
    notification_policy = fields.pop("notification_policy", None) if "notification_policy" in fields else ...
    notification_channel_id = fields.pop("notification_channel_id", None) if "notification_channel_id" in fields else ...
    validated_work_contract = (
        _validate_persisted_work_contract(work_contract)
        if work_contract not in (..., None)
        else work_contract
    )
    validated_notification_settings = ...
    if source_channel is not ... or notification_policy is not ...:
        if source_channel in (..., None) or notification_policy in (..., None):
            raise ValueError("source_channel and notification_policy must be updated together")
        validated_notification_settings = _validate_notification_settings(source_channel, notification_policy)
    if "status" in fields or "completion_summary" in fields or "status_note" in fields:
        now = datetime.now(timezone.utc)
        fields.setdefault("last_heartbeat_at", now)
        fields.setdefault("last_activity", now)
        if fields.get("completion_summary"):
            fields.setdefault("last_progress_at", now)
        elif fields.get("status") in {
            "complete",
            "waiting",
            "blocked",
            "delegated",
            "abandoned",
            "cancelled",
            "stalled",
        }:
            fields.setdefault("last_progress_at", now)

    build_update("tasks", "id", task_id, fields, _TASK_VALID_COLUMNS)
    if validated_work_contract is not ...:
        if validated_work_contract is None:
            delete_task_work_contract(task_id)
        else:
            set_task_work_contract(task_id, validated_work_contract)
    if references is not ...:
        if references:
            set_task_references(task_id, list(references))
        else:
            delete_task_references(task_id)
    if validated_notification_settings is not ...:
        set_task_notification_settings(
            task_id,
            source_channel=validated_notification_settings.source_channel,
            policy=validated_notification_settings.policy,
        )
    if notification_channel_id is not ...:
        if notification_channel_id is None:
            delete_task_notification_target(task_id)
        else:
            set_task_notification_target_channel_id(task_id, notification_channel_id)
    updated = get_task(task_id)
    if fields.get("status") in {"complete", "abandoned", "cancelled"}:
        from db.host_path_consent import clear_once_grants_for_task

        clear_once_grants_for_task(task_id)
    return updated
