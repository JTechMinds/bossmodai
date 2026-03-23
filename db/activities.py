"""BossMod AI — Runtime activity CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.models import Activity
from db.connection import get_connection
from db.crud import build_update, query

_ACTIVITY_COLUMNS = (
    "id, agent_id, kind, status, task_id, parent_activity_id, title, detail, "
    "destination, metadata, created_at, updated_at, ended_at"
)

_ACTIVITY_VALID_COLUMNS = {
    "kind",
    "status",
    "task_id",
    "parent_activity_id",
    "title",
    "detail",
    "destination",
    "metadata",
    "updated_at",
    "ended_at",
}


def _row_to_activity(description: list[tuple[str, ...]], row: tuple[Any, ...]) -> Activity:
    data = {col[0]: val for col, val in zip(description, row)}
    raw_metadata = data.get("metadata")
    if raw_metadata:
        data["metadata"] = json.loads(raw_metadata)
    else:
        data["metadata"] = {}
    return Activity.model_validate(data)


def create_activity(
    agent_id: str,
    kind: str,
    *,
    status: str = "active",
    task_id: str | None = None,
    parent_activity_id: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    destination: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity:
    """Insert a new runtime activity."""
    con = get_connection()
    result = con.execute(
        f"""
        INSERT INTO activities (
            agent_id, kind, status, task_id, parent_activity_id,
            title, detail, destination, metadata, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING {_ACTIVITY_COLUMNS}
        """,
        [
            agent_id,
            kind,
            status,
            task_id,
            parent_activity_id,
            title,
            detail,
            destination,
            json.dumps(metadata or {}),
            datetime.now(timezone.utc),
        ],
    )
    row = result.fetchone()
    return _row_to_activity(result.description, row)


def get_activity(activity_id: str) -> Activity | None:
    """Return a single activity by id."""
    rows = query(
        f"SELECT {_ACTIVITY_COLUMNS} FROM activities WHERE id = $1",
        [activity_id],
    )
    if not rows:
        return None
    return Activity.model_validate({
        **rows[0],
        "metadata": json.loads(rows[0]["metadata"]) if rows[0].get("metadata") else {},
    })


def get_active_activity(agent_id: str) -> Activity | None:
    """Return the active activity for an agent, if any."""
    rows = query(
        f"""
        SELECT {_ACTIVITY_COLUMNS}
        FROM activities
        WHERE agent_id = $1 AND status = 'active'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        [agent_id],
    )
    if not rows:
        return None
    return Activity.model_validate({
        **rows[0],
        "metadata": json.loads(rows[0]["metadata"]) if rows[0].get("metadata") else {},
    })


def get_resumable_work_activity(agent_id: str, task_id: str | None = None) -> Activity | None:
    """Return the newest paused work activity for an agent."""
    params: list[Any] = [agent_id]
    where = ["agent_id = $1", "kind = 'work'", "status = 'paused'"]
    if task_id is not None:
        params.append(task_id)
        where.append(f"task_id = ${len(params)}")

    rows = query(
        f"""
        SELECT {_ACTIVITY_COLUMNS}
        FROM activities
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        params,
    )
    if not rows:
        return None
    return Activity.model_validate({
        **rows[0],
        "metadata": json.loads(rows[0]["metadata"]) if rows[0].get("metadata") else {},
    })


def list_activities(
    *,
    agent_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Activity]:
    """Return activities filtered by optional agent, kind, and status."""
    conditions: list[str] = []
    params: list[Any] = []

    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"agent_id = ${len(params)}")
    if kind is not None:
        params.append(kind)
        conditions.append(f"kind = ${len(params)}")
    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = query(
        f"""
        SELECT {_ACTIVITY_COLUMNS}
        FROM activities
        {where}
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ${len(params)}
        """,
        params,
    )
    return [
        Activity.model_validate({
            **row,
            "metadata": json.loads(row["metadata"]) if row.get("metadata") else {},
        })
        for row in rows
    ]


def update_activity(activity_id: str, **fields: Any) -> Activity | None:
    """Update activity fields and return the refreshed row."""
    valid = {k: v for k, v in fields.items() if k in _ACTIVITY_VALID_COLUMNS}
    if "metadata" in valid and not isinstance(valid["metadata"], str):
        valid["metadata"] = json.dumps(valid["metadata"] or {})
    if valid:
        valid.setdefault("updated_at", datetime.now(timezone.utc))
        if valid.get("status") in {"completed", "cancelled"}:
            valid.setdefault("ended_at", datetime.now(timezone.utc))
    build_update("activities", "id", activity_id, valid, _ACTIVITY_VALID_COLUMNS)
    return get_activity(activity_id)


def cancel_open_activities(agent_id: str, detail: str | None = None) -> int:
    """Cancel all active or paused activities for an agent."""
    open_items = list_activities(agent_id=agent_id, limit=500)
    targets = [item for item in open_items if item.status in {"active", "paused"}]
    for item in targets:
        update_activity(item.id, status="cancelled", detail=detail or item.detail)
    return len(targets)
