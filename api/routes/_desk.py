"""Agent Desk filesystem helpers for the agents router."""

import os
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from api.routes._shared import (
    _TEXT_FILE_EXTENSIONS,
    _child_virtual_path,
    _read_desk_file_preview,
)
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries
from core.models import Agent
import db


def _build_agent_desk_payload(agent: Agent, path: str) -> dict[str, object]:
    """Build one filesystem-style Desk payload in a worker thread."""
    try:
        resolved = resolve_cli_path(agent.storage_key, "/", path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if resolved.real_path is None and resolved.virtual_path != "/":
        raise HTTPException(404, "Path not found")
    if resolved.real_path is not None and not resolved.exists:
        raise HTTPException(404, "Path not found")

    if resolved.real_path is not None and resolved.real_path.is_file():
        stat = resolved.real_path.stat()
        binary = resolved.real_path.suffix.lower() not in _TEXT_FILE_EXTENSIONS
        content, truncated = ("", False) if binary else _read_desk_file_preview(resolved.real_path)
        artifact = db.get_artifact_by_absolute_path(str(resolved.real_path.resolve()))
        return {
            "kind": "file",
            "path": resolved.virtual_path,
            "name": Path(resolved.virtual_path).name,
            "breadcrumbs": _desk_breadcrumbs(resolved.virtual_path),
            "artifact": _serialize_artifact(artifact),
            "content": content,
            "truncated": truncated,
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "binary": binary,
        }

    entries = _list_virtual_root_entries(agent) if resolved.virtual_path == "/" else _list_desk_entries(agent, resolved)
    return {
        "kind": "directory",
        "path": resolved.virtual_path,
        "name": _desk_display_name(resolved.virtual_path),
        "breadcrumbs": _desk_breadcrumbs(resolved.virtual_path),
        "entries": entries,
    }


def _list_virtual_root_entries(agent: Agent) -> list[dict[str, object]]:
    """Return the virtual filesystem mounts without scanning nested contents."""
    entries: list[dict[str, object]] = []
    for mount in virtual_root_entries():
        path = f"/{mount.rstrip('/')}"
        resolved = resolve_cli_path(agent.storage_key, "/", path)
        updated_at = None
        if resolved.real_path is not None and resolved.real_path.exists():
            updated_at = datetime.fromtimestamp(resolved.real_path.stat().st_mtime).isoformat()
        entries.append(
            {
                "name": mount.rstrip("/"),
                "path": path,
                "is_dir": True,
                "artifact": None,
                "size_bytes": None,
                "updated_at": updated_at,
                "category": "mount",
            }
        )
    return entries


def _list_desk_entries(
    agent: Agent,
    resolved,
    *,
    exclude_names: set[str] | None = None,
    directories_only: bool = False,
) -> list[dict[str, object]]:
    if resolved.real_path is None or not resolved.real_path.exists() or not resolved.real_path.is_dir():
        return []

    scanned: list[dict[str, object]] = []
    child_paths: list[str] = []
    with os.scandir(resolved.real_path) as iterator:
        for entry in iterator:
            name = entry.name
            if name in {".git", ".gitignore", ".gitattributes"}:
                continue
            if exclude_names and name in exclude_names:
                continue
            is_dir = entry.is_dir()
            if directories_only and not is_dir:
                continue
            stat_result = entry.stat()
            absolute_path = str(Path(entry.path).resolve())
            if not is_dir:
                child_paths.append(absolute_path)
            scanned.append(
                {
                    "name": name,
                    "path": _child_virtual_path(resolved.virtual_path, name),
                    "is_dir": is_dir,
                    "absolute_path": absolute_path,
                    "size_bytes": None if is_dir else stat_result.st_size,
                    "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                }
            )

    scanned.sort(key=lambda item: (not bool(item["is_dir"]), str(item["name"]).lower()))
    artifact_map = {
        artifact.absolute_path: artifact
        for artifact in db.list_artifacts(absolute_paths=child_paths, limit=max(len(child_paths), 1))
    }

    entries: list[dict[str, object]] = []
    for item in scanned:
        artifact = artifact_map.get(str(item["absolute_path"]))
        entries.append(
            {
                "name": item["name"],
                "path": item["path"],
                "is_dir": item["is_dir"],
                "artifact": _serialize_artifact(artifact),
                "size_bytes": item["size_bytes"],
                "updated_at": item["updated_at"],
                "category": _entry_category(str(item["path"]), artifact, is_dir=bool(item["is_dir"])),
            }
        )
    return entries


def _entry_category(path: str, artifact, *, is_dir: bool = False) -> str:
    if path == "/me":
        return "workspace"
    if path == "/projects":
        return "projects"
    if artifact is not None:
        return artifact.category
    if is_dir:
        return "folder"
    if path.startswith("/projects/"):
        return "project"
    if path.startswith("/me/notes/") or path == "/me/notes":
        return "note"
    return "output"


def _serialize_artifact(artifact) -> dict[str, object] | None:
    if artifact is None:
        return None
    return {
        "id": artifact.id,
        "title": artifact.title,
        "task_id": artifact.task_id,
        "virtual_path": artifact.virtual_path,
        "category": artifact.category,
        "size_bytes": artifact.size_bytes,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def _desk_breadcrumbs(path: str) -> list[dict[str, str]]:
    if path in {"", "/"}:
        return [{"label": "/", "path": "/"}]
    parts = [item for item in path.strip("/").split("/") if item]
    breadcrumbs: list[dict[str, str]] = [{"label": "/", "path": "/"}]
    current = ""
    for part in parts:
        current += f"/{part}"
        label = part
        breadcrumbs.append({"label": label, "path": current})
    return breadcrumbs


def _desk_display_name(path: str) -> str:
    if path == "/":
        return "Workspace"
    if path == "/me":
        return "Desk"
    if path == "/projects":
        return "Projects"
    return Path(path).name or path
