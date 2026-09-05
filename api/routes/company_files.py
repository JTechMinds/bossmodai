"""Company workspace file browser and mutators."""

import asyncio
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import FileResponse

from api.routes._shared import (
    _TEXT_FILE_EXTENSIONS,
    _available_folder_opener_options,
    _child_virtual_path,
    _launch_file_explorer,
    _read_desk_file_preview,
)
from core import config

router = APIRouter()


class CompanyFileSaveBody(BaseModel):
    path: str
    content: str


class CompanyFileCreateBody(BaseModel):
    path: str  # parent directory path
    name: str
    kind: Literal["file", "folder"]


class CompanyFileDeleteBody(BaseModel):
    path: str


class CompanyFileRenameBody(BaseModel):
    path: str
    new_name: str


class CompanyFileMoveBody(BaseModel):
    source: str
    destination: str


@router.get("/company/files")
async def get_company_files(path: str = "/") -> dict[str, object]:
    """Return a browsable file view rooted at the company workspace."""
    return await asyncio.to_thread(_build_company_files_payload, path)


@router.put("/company/files")
async def save_company_file(body: CompanyFileSaveBody) -> dict[str, object]:
    """Write content back to a file in the company workspace."""
    root = _company_files_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None:
        raise HTTPException(400, "Invalid path")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, "File not found")

    await asyncio.to_thread(resolved.write_text, body.content, encoding="utf-8")
    stat = await asyncio.to_thread(resolved.stat)
    return {"status": "ok", "path": _company_virtual_path(root, resolved), "size_bytes": stat.st_size}


@router.post("/company/files/open-folder")
async def open_company_folder(body: dict) -> dict[str, object]:
    """Open a company workspace folder in the host file explorer."""
    raw_path = body.get("path", "/")
    root = _company_files_root()
    safe = _resolve_safe_company_path(root, raw_path)
    if safe is None or not safe.exists():
        raise HTTPException(404, "Path not found")

    target = safe if safe.is_dir() else safe.parent
    opener = config.get("desktop_open_folder_handler") or "auto"
    try:
        _launch_file_explorer(target, opener=opener)
    except OSError as exc:
        raise HTTPException(
            409,
            {
                "code": "desk_open_folder_handler_invalid",
                "message": str(exc),
                "options": _available_folder_opener_options(),
            },
        ) from exc

    return {"status": "ok", "path": str(target)}


# ── Company file operations (create / delete / rename / move / copy / search / raw) ──


_INVALID_NAME_RE = re.compile(r"(/|\.\.)")
_GIT_NAME_RE = re.compile(r"^\.git")

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


def _validate_name(name: str) -> None:
    """Reject names containing path separators, traversal tokens, or .git* prefixes."""
    from core.bm_cli.filesystem import is_denied_company_file

    if not name or _INVALID_NAME_RE.search(name) or _GIT_NAME_RE.match(name):
        raise HTTPException(400, "Invalid name")
    if is_denied_company_file(Path(name)):
        raise HTTPException(400, "Invalid name")


@router.post("/company/files/create", status_code=201)
async def create_company_file(body: CompanyFileCreateBody) -> dict[str, object]:
    """Create an empty file or folder in the company workspace."""
    root = _company_files_root()
    parent = _resolve_safe_company_path(root, body.path)
    if parent is None or not parent.exists() or not parent.is_dir():
        raise HTTPException(400, "Invalid parent path")

    _validate_name(body.name)
    target = parent / body.name

    if target.exists():
        raise HTTPException(409, "Target already exists")

    if body.kind == "file":
        await asyncio.to_thread(target.touch)
    else:
        await asyncio.to_thread(target.mkdir)

    return {"status": "ok", "path": _company_virtual_path(root, target.resolve())}


@router.delete("/company/files")
async def delete_company_file(body: CompanyFileDeleteBody) -> dict[str, object]:
    """Delete a file or empty directory from the company workspace."""
    root = _company_files_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "Path not found")

    if resolved.is_dir():
        if any(resolved.iterdir()):
            raise HTTPException(409, "Directory is not empty")
        await asyncio.to_thread(resolved.rmdir)
    else:
        await asyncio.to_thread(resolved.unlink)

    return {"status": "ok"}


@router.patch("/company/files/rename")
async def rename_company_file(body: CompanyFileRenameBody) -> dict[str, object]:
    """Rename a file or folder in the company workspace."""
    root = _company_files_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "Path not found")

    _validate_name(body.new_name)
    new_target = resolved.parent / body.new_name

    await asyncio.to_thread(resolved.rename, new_target)

    return {"status": "ok", "path": _company_virtual_path(root, new_target.resolve())}


@router.post("/company/files/move")
async def move_company_file(body: CompanyFileMoveBody) -> dict[str, object]:
    """Move a file or folder within the company workspace."""
    root = _company_files_root()
    source = _resolve_safe_company_path(root, body.source)
    destination = _resolve_safe_company_path(root, body.destination)
    if source is None or not source.exists():
        raise HTTPException(404, "Source not found")
    if destination is None or not destination.exists() or not destination.is_dir():
        raise HTTPException(400, "Destination must be an existing directory")

    target = destination / source.name
    if target.exists():
        raise HTTPException(409, "Target already exists in destination")

    await asyncio.to_thread(shutil.move, str(source), str(target))

    return {"status": "ok", "path": _company_virtual_path(root, target.resolve())}


@router.post("/company/files/copy")
async def copy_company_file(body: CompanyFileMoveBody) -> dict[str, object]:
    """Copy a file or folder within the company workspace."""
    root = _company_files_root()
    source = _resolve_safe_company_path(root, body.source)
    destination = _resolve_safe_company_path(root, body.destination)
    if source is None or not source.exists():
        raise HTTPException(404, "Source not found")
    if destination is None or not destination.exists() or not destination.is_dir():
        raise HTTPException(400, "Destination must be an existing directory")

    target = destination / source.name
    if target.exists():
        raise HTTPException(409, "Target already exists in destination")

    if source.is_dir():
        await asyncio.to_thread(shutil.copytree, str(source), str(target))
    else:
        await asyncio.to_thread(shutil.copy2, str(source), str(target))

    return {"status": "ok", "path": _company_virtual_path(root, target.resolve())}


@router.get("/company/files/search")
async def search_company_files(q: str = Query(..., min_length=1)) -> list[dict[str, object]]:
    """Search for files and folders by name across the company workspace."""
    from core.bm_cli.filesystem import is_denied_company_file

    def _search() -> list[dict[str, object]]:
        root = _company_files_root()
        root_resolved = root.resolve()
        query_lower = q.lower()
        results: list[dict[str, object]] = []

        for dirpath, dirnames, filenames in os.walk(root_resolved):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".git") and not is_denied_company_file(Path(d))
            ]

            for name in dirnames + filenames:
                if name.startswith(".git") or is_denied_company_file(Path(name)):
                    continue
                if query_lower not in name.lower():
                    continue
                full = Path(dirpath) / name
                is_dir = full.is_dir()
                stat_result = full.stat()
                results.append({
                    "name": name,
                    "path": _company_virtual_path(root, full),
                    "is_dir": is_dir,
                    "size_bytes": None if is_dir else stat_result.st_size,
                    "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                })
                if len(results) >= 100:
                    break
            if len(results) >= 100:
                break

        _annotate_agent_names(results)
        return results

    return await asyncio.to_thread(_search)


@router.get("/company/files/raw")
async def get_company_file_raw(path: str = Query(..., min_length=1)):
    """Return the raw bytes of a file from the company workspace."""
    root = _company_files_root()
    resolved = _resolve_safe_company_path(root, path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "File not found")
    if not resolved.is_file():
        raise HTTPException(400, "Path is not a file")

    suffix = resolved.suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(str(resolved), media_type=mime_type)


def _company_files_root() -> Path:
    """Return the company browser root (``artifacts/projects``)."""
    from core.bm_cli.filesystem import company_files_root
    return company_files_root()


def _company_virtual_path(root: Path, resolved: Path) -> str:
    """Return the company-browser virtual path for a resolved filesystem path."""
    relative = str(resolved.relative_to(root.resolve())).replace("\\", "/")
    if relative in {"", "."}:
        return "/"
    return f"/{relative}"


def _resolve_safe_company_path(root: Path, raw_path: str) -> Path | None:
    """Resolve a user-supplied company path via ``resolve_relative_path``.

    Rejects traversal and backup/database suffixes (``*.bak``, ``*.sqlite3``, ``*.db``).
    """
    from core.bm_cli.filesystem import resolve_company_relative_path

    try:
        return resolve_company_relative_path(root, raw_path)
    except (ValueError, OSError):
        return None


def _annotate_agent_names(items: list[dict], name_key: str = "name") -> None:
    """Detect agent_XXXX keys in a list of dicts and attach agent_name."""
    agent_keys = [
        str(d.get(name_key, ""))
        for d in items
        if re.match(r"^agent_\d{4}$", str(d.get(name_key, "")))
    ]
    if not agent_keys:
        for d in items:
            d.setdefault("agent_name", None)
        return
    from db.agent_storage_identities import get_agent_names_by_storage_keys

    name_map = get_agent_names_by_storage_keys(agent_keys)
    for d in items:
        d["agent_name"] = name_map.get(str(d.get(name_key)))


def _build_company_files_payload(path: str) -> dict[str, object]:
    """Build a filesystem-style payload for the company workspace browser."""
    from core.bm_cli.filesystem import is_denied_company_file

    root = _company_files_root()
    resolved = _resolve_safe_company_path(root, path)
    if resolved is None:
        raise HTTPException(400, "Invalid path")
    if not resolved.exists():
        raise HTTPException(404, "Path not found")

    virtual_path = _company_virtual_path(root, resolved)

    if resolved.is_file():
        stat = resolved.stat()
        binary = resolved.suffix.lower() not in _TEXT_FILE_EXTENSIONS
        content, truncated = ("", False) if binary else _read_desk_file_preview(resolved)
        return {
            "kind": "file",
            "path": virtual_path,
            "name": resolved.name,
            "breadcrumbs": _company_breadcrumbs(virtual_path),
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "content": content,
            "truncated": truncated,
            "binary": binary,
        }

    entries: list[dict[str, object]] = []
    with os.scandir(resolved) as iterator:
        for entry in iterator:
            name = entry.name
            if name in {".git", ".gitignore", ".gitattributes"}:
                continue
            if is_denied_company_file(Path(name)):
                continue
            is_dir = entry.is_dir()
            stat_result = entry.stat()
            child_path = _child_virtual_path(virtual_path, name)
            entries.append({
                "name": name,
                "path": child_path,
                "is_dir": is_dir,
                "size_bytes": None if is_dir else stat_result.st_size,
                "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
            })
    entries.sort(key=lambda e: (not bool(e["is_dir"]), str(e["name"]).lower()))
    _annotate_agent_names(entries)

    return {
        "kind": "directory",
        "path": virtual_path,
        "name": Path(virtual_path).name if virtual_path != "/" else "Company Workspace",
        "breadcrumbs": _company_breadcrumbs(virtual_path),
        "entries": entries,
    }


def _company_breadcrumbs(path: str) -> list[dict[str, str]]:
    """Build breadcrumb trail for the company file browser."""
    if path in {"", "/"}:
        return [{"label": "Company", "path": "/"}]
    parts = [item for item in path.strip("/").split("/") if item]
    breadcrumbs: list[dict[str, str]] = [{"label": "Company", "path": "/"}]
    current = ""
    for part in parts:
        current += f"/{part}"
        breadcrumbs.append({"label": part, "path": current})
    _annotate_agent_names(breadcrumbs, name_key="label")
    return breadcrumbs
