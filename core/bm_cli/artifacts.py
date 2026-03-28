"""BossMod AI — Artifact registry integration for BossMod CLI writes."""

from __future__ import annotations

from pathlib import Path

import db
from core.agent_loop import activity_runtime
from core.bm_cli.types import BossModCliResult
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent


def register_cli_artifacts(agent: Agent, result: BossModCliResult) -> list[str]:
    """Upsert artifact records for file-writing CLI results and return ids."""
    if not result.ok or result.kind not in {"write", "append", "batch-write", "replace-section", "rewrite-section"}:
        return []
    data = result.data or {}
    task_id = activity_runtime.get_active_task_id(agent.id)
    virtual_paths: list[str] = []
    if isinstance(data.get("path"), str) and str(data.get("path")).strip():
        virtual_paths.append(str(data["path"]))
    if isinstance(data.get("paths"), list):
        virtual_paths.extend(str(item) for item in data["paths"] if isinstance(item, str) and item.strip())

    artifact_ids: list[str] = []
    seen_paths: set[str] = set()
    for virtual_path in virtual_paths:
        if virtual_path in seen_paths:
            continue
        seen_paths.add(virtual_path)
        resolved = resolve_cli_path(agent.storage_key, result.cwd or "/", virtual_path)
        if resolved.real_path is None or not resolved.real_path.exists() or resolved.real_path.is_dir():
            continue
        artifact = db.upsert_artifact(
            agent_id=agent.id,
            task_id=task_id,
            virtual_path=resolved.virtual_path,
            absolute_path=str(resolved.real_path),
            title=db.build_artifact_title(resolved.virtual_path),
            kind="file",
            category=_artifact_category(resolved.virtual_path),
            size_bytes=resolved.real_path.stat().st_size,
            source_command=result.command,
        )
        artifact_ids.append(artifact.id)
    return artifact_ids


def _artifact_category(virtual_path: str) -> str:
    normalized = virtual_path.strip()
    if normalized.startswith("/projects/"):
        return "project"
    if normalized.startswith("/me/notes/") or normalized == "/me/notes":
        return "note"
    return "output"
