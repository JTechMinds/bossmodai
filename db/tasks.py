"""BossMod AI — Task CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.models import Task, TaskNotificationSettings, WorkContract
from core.models.message import HUMAN_SENDER_ID
from db.crud import build_update, insert_returning_dict, query
from db.task_notification_policies import delete_task_notification_settings, set_task_notification_settings
from db.task_notification_targets import delete_task_notification_target, set_task_notification_target_channel_id
from db.task_work_contracts import delete_task_work_contract, set_task_work_contract

_TASK_COLUMNS = (
    "t.id, t.title, t.description, t.project, t.assigned_to, t.requester_id, t.owner_id, t.created_by, "
    "t.status, twc.work_contract, twc.updated_at AS work_contract_updated_at, "
    "tnp.source_channel, tnp.policy AS notification_policy, tnp.updated_at AS notification_policy_updated_at, "
    "tnt.channel_id AS notification_channel_id, "
    "t.parent_task_id, t.cost_ceiling, t.completion_summary, "
    "t.status_note, t.watchdog_pinged_at, t.last_progress_at, t.last_heartbeat_at, "
    "t.last_activity, t.created_at"
)

_TASK_VALID_COLUMNS = {
    "title", "description", "project", "assigned_to", "requester_id", "owner_id",
    "status", "parent_task_id", "cost_ceiling", "completion_summary",
    "status_note", "watchdog_pinged_at", "last_progress_at", "last_heartbeat_at",
    "last_activity",
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
) -> Task:
    """Insert a new task."""
    validated_work_contract = None
    if work_contract is not None:
        validated_work_contract = _validate_persisted_work_contract(work_contract)
    validated_notification_settings = None
    if source_channel is not None or notification_policy is not None:
        if source_channel is None or notification_policy is None:
            raise ValueError("source_channel and notification_policy must be provided together")
        validated_notification_settings = _validate_notification_settings(source_channel, notification_policy)

    resolved_requester_id = requester_id if requester_id is not None else created_by
    resolved_owner_id = owner_id if owner_id is not None else _default_task_owner_id(
        assigned_to=assigned_to,
        requester_id=resolved_requester_id,
        created_by=created_by,
    )

    row = insert_returning_dict(
        f"""
        INSERT INTO tasks (title, description, project, assigned_to, requester_id, owner_id, created_by, parent_task_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        [title, description, project, assigned_to, resolved_requester_id, resolved_owner_id, created_by, parent_task_id],
    )
    task_id = row["id"]
    if validated_work_contract is not None:
        set_task_work_contract(task_id, validated_work_contract)
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
    """Hydrate a task row and its optional work contract."""
    data = dict(row)
    raw_contract = data.get("work_contract")
    if raw_contract:
        data["work_contract"] = json.loads(raw_contract)
    else:
        data["work_contract"] = None
    return Task.model_validate(data)


def _default_task_owner_id(
    *,
    assigned_to: str | None,
    requester_id: str | None,
    created_by: str | None,
) -> str | None:
    """Pick the accountable owner for a task when one is not provided."""
    for candidate in (assigned_to, requester_id, created_by):
        if not isinstance(candidate, str):
            continue
        value = candidate.strip()
        if not value or value == HUMAN_SENDER_ID:
            continue
        return value
    return None


def get_task(task_id: str) -> Task | None:
    """Fetch a single task by ID."""
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        WHERE t.id = $1
        """,
        [task_id],
    )
    if not rows:
        return None
    return _task_from_row(rows[0])


def list_tasks(
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    status: str | None = None,
) -> list[Task]:
    """Return tasks, optionally filtered by assignee and/or status."""
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

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = query(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks t
        LEFT JOIN task_work_contracts twc ON twc.task_id = t.id
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        {where}
        ORDER BY t.created_at
        """,
        params,
    )
    return [_task_from_row(row) for row in rows]


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
        LEFT JOIN task_notification_policies tnp ON tnp.task_id = t.id
        LEFT JOIN task_notification_targets tnt ON tnt.task_id = t.id
        {where}
        ORDER BY t.last_activity DESC, t.created_at DESC
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
    """
    if "status" in fields:
        from core.tasking.transitions import assert_valid_task_transition

        current = get_task(task_id)
        if current is not None:
            assert_valid_task_transition(current.status, str(fields["status"]))

    work_contract = fields.pop("work_contract", None) if "work_contract" in fields else ...
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
        elif fields.get("status") in {"complete", "waiting", "blocked", "delegated", "abandoned", "stalled"}:
            fields.setdefault("last_progress_at", now)

    build_update("tasks", "id", task_id, fields, _TASK_VALID_COLUMNS)
    if validated_work_contract is not ...:
        if validated_work_contract is None:
            delete_task_work_contract(task_id)
        else:
            set_task_work_contract(task_id, validated_work_contract)
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
    if fields.get("status") in {"complete", "abandoned"}:
        from db.host_path_consent import clear_once_grants_for_task

        clear_once_grants_for_task(task_id)
    return updated
