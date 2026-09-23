"""Floors on disk: the one seam that maps a floor to its company folder.

Layout::

    <company>/<floor_id>/<project>/...        one folder per floor
    <company>/.archived-floors/<floor_id>/    a deleted floor's folder

Folders are named by floor id, never by floor name: names can be renamed,
ids cannot. Display names are looked up where a folder is shown.

An agent's ``/projects`` is its floor's folder, so agents on one floor cannot
name another floor's projects. Two things are deliberately NOT under a floor:

* ``/me`` (``filesystem.agent_artifact_dir``) is agent-owned. It is the
  agent's own memory, like its prompt history, and travels with the agent
  when it moves floors.
* Extra host roots (the ``workspace_host_roots`` setting) are global. They
  are an operator allowlist for the whole install, a documented exception
  to floor isolation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from core.bm_cli.filesystem import slugify_name
from core.bm_cli.install_layout import default_company_root, require_company_root

logger = logging.getLogger(__name__)

ARCHIVED_FLOORS_DIRNAME = ".archived-floors"
ARCHIVED_FLOORS_LABEL = "Archived floors"
FLOOR_MARKER_NAME = ".floor.json"


class FloorRootUnavailable(LookupError):
    """A floor folder was asked for, but there is no floor to map it to.

    Raised for an unknown floor id and for an agent with no floor (on
    vacation), which has no ``/projects`` at all.
    """


def company_root() -> Path:
    """Return the company root, outside the application install, creating it.

    Raises:
        ValueError: The configured root is inside the install or unresolvable.
        RetiredProjectsRootSetting: ``BOSSMOD_PROJECTS_ROOT`` is still set.
    """
    root = require_company_root(default_company_root())
    root.mkdir(parents=True, exist_ok=True)
    return root


def _existing_floor_id(floor_id: str) -> str:
    """Return the stored id of an existing floor, or raise FloorRootUnavailable."""
    from db.floors import get_floor

    floor = get_floor(floor_id)
    if floor is None:
        raise FloorRootUnavailable(f"Floor not found: {floor_id!r}")
    return floor.id


def floor_root(floor_id: str) -> Path:
    """Return one floor's folder, ``<company>/<floor_id>``, creating it.

    Raises:
        FloorRootUnavailable: No floor has this id.
    """
    path = company_root() / _existing_floor_id(floor_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def floor_folder_exists(floor_id: str) -> bool:
    """True when the floor's folder is on disk. Never creates it.

    Raises:
        FloorRootUnavailable: No floor has this id.
    """
    return (company_root() / _existing_floor_id(floor_id)).is_dir()


def archived_floors_root() -> Path:
    """Return ``<company>/.archived-floors``, where deleted floors' folders go."""
    path = company_root() / ARCHIVED_FLOORS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_floor_id(storage_key: str) -> str | None:
    """Return the floor id of the agent that owns *storage_key*.

    None means the agent exists and has no floor (it is on vacation).

    Raises:
        LookupError: No agent owns this storage key.
    """
    import db

    agent = db.get_agent_by_storage_key(storage_key)
    if agent is None:
        raise LookupError(f"No agent owns storage key {storage_key!r}")
    floor_id = str(getattr(agent, "floor_id", None) or "").strip()
    return floor_id or None


def projects_root_for_storage_key(storage_key: str) -> Path:
    """Return the agent's ``/projects``: the folder of the floor it lives on.

    Raises:
        LookupError: No agent owns this storage key.
        FloorRootUnavailable: The agent has no floor (on vacation), or its
            floor row is gone.
    """
    floor_id = agent_floor_id(storage_key)
    if floor_id is None:
        raise FloorRootUnavailable("Agent has no floor")
    return floor_root(floor_id)


def project_dir(floor_id: str, project: str) -> Path:
    """Return one project's folder on a floor. The name is slugified.

    Raises:
        FloorRootUnavailable: No floor has this id.
    """
    return floor_root(floor_id) / slugify_name(project)


def ensure_floor_roots() -> None:
    """Create the company root, the archive folder, and every floor's folder.

    Run at startup, so the Files place shows one folder per floor even for
    floors created before floors had folders.
    """
    from db.floors import list_floors

    company_root()
    archived_floors_root()
    for floor in list_floors():
        floor_root(floor.id)


def archive_floor_folder(floor_id: str, floor_name: str, *, archived_at: datetime) -> tuple[Path, Path] | None:
    """Move a floor's folder to ``<company>/.archived-floors/<floor_id>``.

    A ``.floor.json`` marker ``{id, name, archived_at}`` is written into the
    folder first, so the archive can still show the floor's name after the
    floor row is gone. Nothing is deleted. Call while the floor row exists.

    Returns:
        ``(old_path, new_path)`` (both resolved) when a folder was moved, or
        None when the floor had no folder on disk.

    Raises:
        FloorRootUnavailable: No floor has this id.
        NotADirectoryError: The floor path exists but is not a directory.
        FileExistsError: The archive already holds a folder for this id.
    """
    token = _existing_floor_id(floor_id)
    source = company_root() / token
    if not source.exists():
        return None
    if not source.is_dir():
        raise NotADirectoryError(f"Floor path is not a directory: {source}")
    target = archived_floors_root() / token
    if target.exists():
        raise FileExistsError(f"Archived floor folder already exists: {target}")
    old_path = source.resolve()
    marker = {"id": token, "name": floor_name, "archived_at": archived_at.isoformat()}
    (source / FLOOR_MARKER_NAME).write_text(json.dumps(marker, ensure_ascii=True), encoding="utf-8")
    # Both paths sit under the company root, so this is a same-filesystem rename.
    os.replace(source, target)
    new_path = target.resolve()
    logger.info("Archived floor folder %s -> %s", old_path, new_path)
    return old_path, new_path


def archived_floor_name(folder: Path) -> str | None:
    """Return the floor name recorded in an archived folder's ``.floor.json``.

    None when the folder carries no marker. A marker that cannot be read is
    logged at WARNING and also answers None, so one damaged marker leaves its
    folder listed under its id instead of hiding the whole archive.
    """
    marker = folder / FLOOR_MARKER_NAME
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable archived floor marker %s: %s", marker, exc)
        return None
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name.strip():
        logger.warning("Archived floor marker %s has no name", marker)
        return None
    return name.strip()
