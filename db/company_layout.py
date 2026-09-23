"""One-time move of the flat projects tree into the company → floor layout.

Before floors had folders, every project lived directly under one flat
root, ``<install>/../bossmod-data/projects``. Now projects live under
``<company>/<floor_id>/``. Every existing agent and thread landed on Lobby,
so every existing project lands in ``<company>/lobby/``.

The flat root is found as the ``projects`` sibling of the company root. With
the default company root (``bossmod-data/company``) that is exactly the old
default (``bossmod-data/projects``); a custom ``BOSSMOD_COMPANY_ROOT``
carries its own sibling with it, which also keeps a test's temp company root
away from real data. The retired ``BOSSMOD_PROJECTS_ROOT`` stops startup
before this runs, so a custom old root is never guessed at.

Rules:

* A settings marker (``company_layout_version``) makes it run once. It is set
  only after every entry moved.
* The database is backed up before the first path rewrite. A failed backup
  logs ERROR and stops the migration without the marker, so the next boot
  retries. Nothing has moved at that point.
* Each top-level entry moves with ``shutil.move`` (a rename on the same
  filesystem). The absolute-path rewrite for that entry runs in the same
  transaction and commits only after the move succeeded, so a failed move
  rolls its rewrite back and the error stops startup with the entry still in
  the old root.
* A name already taken in Lobby's folder keeps both: the incoming entry gets
  a ``__from_projects`` suffix and a WARNING names both paths.
* Nothing is deleted. The emptied old root stays; the legacy in-tree
  ``artifacts/projects`` is not touched.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from db.connection import backup_database, database_path, transaction
from db.crud import query_one

logger = logging.getLogger(__name__)

MARKER_KEY = "company_layout_version"
MARKER_VALUE = "1"
MARKER_CATEGORY = "system"
CONFLICT_SUFFIX = "__from_projects"


def legacy_flat_projects_root(company: Path) -> Path:
    """Return the old flat projects root: the ``projects`` sibling of *company*."""
    return company.parent / "projects"


def migration_backup_dir() -> Path:
    """Where the pre-migration backup goes: ``db_backups`` beside the database file."""
    return database_path().parent / "db_backups"


def _marker_set() -> bool:
    row = query_one("SELECT value FROM settings WHERE key = $1", [MARKER_KEY])
    return row is not None and str(row.get("value") or "") == MARKER_VALUE


def _set_marker() -> None:
    from db.settings import set_setting

    set_setting(MARKER_KEY, MARKER_VALUE, MARKER_CATEGORY)


def _free_destination(lobby: Path, name: str) -> Path:
    """``lobby/name``, or a suffixed sibling when that name is already taken."""
    destination = lobby / name
    if not destination.exists():
        return destination
    candidate = lobby / f"{name}{CONFLICT_SUFFIX}"
    index = 2
    while candidate.exists():
        candidate = lobby / f"{name}{CONFLICT_SUFFIX}_{index}"
        index += 1
    return candidate


def _move_entry(source: Path, destination: Path) -> None:
    """Move one entry and repoint every stored absolute path under it."""
    from db.artifacts import rewrite_artifact_path_prefix
    from db.host_path_consent import rewrite_host_path_prefix

    old_prefix = str(source.resolve())
    # The destination does not exist yet; its resolved parent plus name is
    # where the entry will be after the move.
    new_prefix = str(destination.parent.resolve() / destination.name)
    with transaction():
        artifacts = rewrite_artifact_path_prefix(old_prefix, new_prefix)
        consent = rewrite_host_path_prefix(old_prefix, new_prefix)
        shutil.move(str(source), str(destination))
    logger.info(
        "Company layout: moved %s -> %s (%d artifact path(s), %d consent path(s))",
        old_prefix, new_prefix, artifacts, consent,
    )


def migrate_to_floor_layout() -> bool:
    """Move the flat projects tree into Lobby's folder, once.

    Returns:
        True when the layout is migrated (now or on an earlier boot), False
        when this boot stopped before migrating (logged at ERROR; the marker
        is not set, so the next boot retries).

    Raises:
        OSError, sqlite3.Error: Moving an entry or rewriting its paths
            failed. That entry is still in the old root with its paths
            unchanged; entries moved before it stay moved.
    """
    from core.bm_cli.floor_roots import company_root, floor_root
    from db.floors import LOBBY_ID

    if _marker_set():
        return True
    company = company_root()
    source = legacy_flat_projects_root(company)
    resolved_source = source.resolve()
    if resolved_source == company or company in resolved_source.parents or resolved_source in company.parents:
        logger.error(
            "Company layout: the old projects root %s overlaps the company root %s; not migrating",
            resolved_source, company,
        )
        return False
    entries = sorted(os.scandir(source), key=lambda entry: entry.name) if source.is_dir() else []
    if not entries:
        _set_marker()
        return True

    try:
        backup = backup_database(migration_backup_dir())
    except Exception:
        logger.exception(
            "Company layout: database backup failed; not moving %s. The next start retries.",
            source,
        )
        return False
    logger.info("Company layout: database backed up to %s before moving %s", backup, source)

    lobby = floor_root(LOBBY_ID)
    for entry in entries:
        incoming = Path(entry.path)
        destination = _free_destination(lobby, entry.name)
        if destination.name != entry.name:
            logger.warning(
                "Company layout: %s already exists; keeping both, %s moves to %s",
                lobby / entry.name, incoming, destination,
            )
        _move_entry(incoming, destination)
    _set_marker()
    logger.info("Company layout: %d entr(y/ies) from %s now under %s", len(entries), source, lobby)
    return True
