"""BossMod AI — Frozen work-transcript CRUD (``work_snapshots``).

One row per work activity. Kept apart from ``activities`` because the
transcript can be large and ``get_active_activity`` is read on nearly every
path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.models.work_snapshot import WorkInterlude, WorkSnapshot
from db.crud import execute, fetch_one, insert_returning

_ALL_COLUMNS = (
    "activity_id, agent_id, task_id, transcript, fingerprints, interludes, "
    "no_progress_checkpoints, created_at, updated_at"
)


def save_work_snapshot(
    activity_id: str,
    *,
    agent_id: str,
    task_id: str | None,
    transcript: list[dict[str, str]],
    fingerprints: list[str],
    no_progress_checkpoints: int,
) -> WorkSnapshot:
    """Insert or replace the transcript, fingerprints and checkpoint count.

    Interludes already recorded on the row are kept; only
    ``clear_work_interludes`` removes them.

    Args:
        activity_id: The work activity this transcript belongs to.
        agent_id: Owning agent.
        task_id: Bound task, if any.
        transcript: Full working transcript as ``{role, content}`` messages.
        fingerprints: Every command fingerprint seen on this activity.
        no_progress_checkpoints: Checkpoints spent since the last progress step.

    Returns:
        The stored snapshot.
    """
    now = datetime.now(timezone.utc)
    return insert_returning(
        f"""
        INSERT INTO work_snapshots (
            activity_id, agent_id, task_id, transcript, fingerprints,
            interludes, no_progress_checkpoints, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, '[]', $6, $7, $7)
        ON CONFLICT(activity_id) DO UPDATE SET
            agent_id = excluded.agent_id,
            task_id = excluded.task_id,
            transcript = excluded.transcript,
            fingerprints = excluded.fingerprints,
            no_progress_checkpoints = excluded.no_progress_checkpoints,
            updated_at = excluded.updated_at
        RETURNING {_ALL_COLUMNS}
        """,
        [
            activity_id,
            agent_id,
            task_id,
            json.dumps(transcript),
            json.dumps(fingerprints),
            int(no_progress_checkpoints),
            now,
        ],
        WorkSnapshot,
    )


def get_work_snapshot(activity_id: str) -> WorkSnapshot | None:
    """Return the snapshot for one work activity, or ``None``."""
    return fetch_one(
        f"SELECT {_ALL_COLUMNS} FROM work_snapshots WHERE activity_id = $1",
        [activity_id],
        WorkSnapshot,
    )


def append_work_interlude(activity_id: str, entry: dict[str, Any]) -> WorkSnapshot:
    """Append one ``{from_name, content, reply}`` interlude to a snapshot.

    Raises:
        LookupError: No snapshot exists for ``activity_id``.
        pydantic.ValidationError: ``entry`` does not have the interlude shape.
    """
    interlude = WorkInterlude.model_validate(entry)
    snapshot = get_work_snapshot(activity_id)
    if snapshot is None:
        raise LookupError(f"No work snapshot for activity {activity_id}")
    interludes = [item.model_dump() for item in snapshot.interludes]
    interludes.append(interlude.model_dump())
    return _update_json_column(activity_id, "interludes", interludes)


def clear_work_interludes(activity_id: str) -> None:
    """Drop the interludes after a resume has rendered them."""
    execute(
        "UPDATE work_snapshots SET interludes = '[]', updated_at = $1 WHERE activity_id = $2",
        [datetime.now(timezone.utc), activity_id],
    )


def set_work_snapshot_checkpoints(activity_id: str, count: int) -> None:
    """Store how many no-progress checkpoints this activity has spent."""
    execute(
        "UPDATE work_snapshots SET no_progress_checkpoints = $1, updated_at = $2 "
        "WHERE activity_id = $3",
        [int(count), datetime.now(timezone.utc), activity_id],
    )


def delete_work_snapshot(activity_id: str) -> None:
    """Delete the snapshot of an ended work activity. Missing is a no-op."""
    execute("DELETE FROM work_snapshots WHERE activity_id = $1", [activity_id])


def delete_agent_work_snapshots(agent_id: str) -> None:
    """Delete every snapshot for an agent whose open activities were all cancelled."""
    execute("DELETE FROM work_snapshots WHERE agent_id = $1", [agent_id])


def _update_json_column(activity_id: str, column: str, value: list[Any]) -> WorkSnapshot:
    snapshot = fetch_one(
        f"""
        UPDATE work_snapshots SET {column} = $1, updated_at = $2
        WHERE activity_id = $3
        RETURNING {_ALL_COLUMNS}
        """,
        [json.dumps(value), datetime.now(timezone.utc), activity_id],
        WorkSnapshot,
    )
    if snapshot is None:
        raise LookupError(f"No work snapshot for activity {activity_id}")
    return snapshot
