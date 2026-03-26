"""BossMod AI — Artifact registry CRUD."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.models import Artifact
from db.crud import execute, fetch_all, fetch_one, insert_returning

_ARTIFACT_COLUMNS = (
    "id, agent_id, task_id, virtual_path, absolute_path, title, kind, "
    "category, size_bytes, source_command, created_at, updated_at"
)


def get_artifact(artifact_id: str) -> Artifact | None:
    """Return one artifact by id."""
    return fetch_one(
        f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE id = $1",
        [artifact_id],
        Artifact,
    )


def get_artifact_by_absolute_path(absolute_path: str) -> Artifact | None:
    """Return one artifact by its concrete filesystem path."""
    return fetch_one(
        f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE absolute_path = $1",
        [absolute_path],
        Artifact,
    )


def list_artifacts(
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    absolute_paths: list[str] | None = None,
    absolute_path_prefix: str | None = None,
    limit: int = 200,
) -> list[Artifact]:
    """Return artifacts filtered by optional owner/task/path prefix."""
    if absolute_paths is not None and not absolute_paths:
        return []

    conditions: list[str] = []
    params: list[object] = []
    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"agent_id = ${len(params)}")
    if task_id is not None:
        params.append(task_id)
        conditions.append(f"task_id = ${len(params)}")
    if absolute_paths is not None:
        placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(absolute_paths)))
        params.extend(absolute_paths)
        conditions.append(f"absolute_path IN ({placeholders})")
    if absolute_path_prefix is not None:
        params.append(f"{absolute_path_prefix}%")
        conditions.append(f"absolute_path LIKE ${len(params)}")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return fetch_all(
        f"""
        SELECT {_ARTIFACT_COLUMNS}
        FROM artifacts
        {where}
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ${len(params)}
        """,
        params,
        Artifact,
    )


def upsert_artifact(
    *,
    agent_id: str,
    task_id: str | None,
    virtual_path: str,
    absolute_path: str,
    title: str,
    kind: str,
    category: str,
    size_bytes: int,
    source_command: str | None,
) -> Artifact:
    """Create or refresh one artifact registry row."""
    existing = get_artifact_by_absolute_path(absolute_path)
    if existing is None:
        return insert_returning(
            f"""
            INSERT INTO artifacts (
                agent_id, task_id, virtual_path, absolute_path, title,
                kind, category, size_bytes, source_command
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING {_ARTIFACT_COLUMNS}
            """,
            [
                agent_id,
                task_id,
                virtual_path,
                absolute_path,
                title,
                kind,
                category,
                size_bytes,
                source_command,
            ],
            Artifact,
        )

    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE artifacts
        SET agent_id = $1,
            task_id = $2,
            virtual_path = $3,
            title = $4,
            kind = $5,
            category = $6,
            size_bytes = $7,
            source_command = $8,
            updated_at = $9
        WHERE absolute_path = $10
        """,
        [
            agent_id,
            task_id,
            virtual_path,
            title,
            kind,
            category,
            size_bytes,
            source_command,
            now,
            absolute_path,
        ],
    )
    refreshed = get_artifact_by_absolute_path(absolute_path)
    if refreshed is None:
        raise RuntimeError(f"Failed to reload artifact for {absolute_path}")
    return refreshed


def build_artifact_title(path: str) -> str:
    """Derive a human-friendly title from a virtual file path."""
    return Path(path).name or path
