"""Floor awareness for the company file browser.

The company root's top level is one folder per floor (named by floor id)
plus ``.archived-floors``, whose children are deleted floors' folders. This
module owns the two things the browser needs to know about that:

* how a floor-level folder is shown (its floor's name, never its id), in
  listings, breadcrumbs and search hits;
* that floor-level folders are managed by the floors themselves, so the
  browser refuses to rename, move, copy or delete them, and refuses to put
  anything next to them.

Split from company_files.py, which keeps the generic file operations.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from core.bm_cli.floor_roots import (
    ARCHIVED_FLOORS_DIRNAME,
    ARCHIVED_FLOORS_LABEL,
    archived_floor_name,
    company_root,
)

TOP_LEVEL_DESTINATION_WHY = (
    "Pick a floor first. The company top level holds only floor folders, "
    "and Archived floors holds only archived floors."
)
FLOOR_FOLDER_WHY = (
    "Floor folders can't be renamed, moved, copied or deleted in Files. "
    "Rename or delete the floor itself; a deleted floor's folder goes to Archived floors."
)


def floor_names() -> dict[str, str]:
    """Every floor's display name, keyed by the id its folder is named after."""
    from db.floors import list_floors

    return {floor.id: floor.name for floor in list_floors()}


def floor_display_name(parts: list[str], index: int, names: dict[str, str]) -> str | None:
    """Display name for ``parts[index]`` of a company-relative path, if floor-level.

    Top level: a floor id answers the floor's name, ``.archived-floors``
    answers "Archived floors". One level into the archive: the name recorded
    in that folder's ``.floor.json``. Anything else is not a floor folder.
    """
    if index == 0:
        if parts[0] == ARCHIVED_FLOORS_DIRNAME:
            return ARCHIVED_FLOORS_LABEL
        return names.get(parts[0])
    if index == 1 and parts[0] == ARCHIVED_FLOORS_DIRNAME:
        return archived_floor_name(company_root() / ARCHIVED_FLOORS_DIRNAME / parts[1])
    return None


def display_path(company_path: str, names: dict[str, str]) -> str:
    """A company-relative path with floor-level parts shown by name.

    ``/<floor id>/books/plan.md`` reads ``/Finance/books/plan.md``.
    """
    parts = [part for part in company_path.strip("/").split("/") if part]
    labels = [floor_display_name(parts, index, names) or part for index, part in enumerate(parts)]
    return "/" + "/".join(labels)


def annotate_floor_folders(entries: list[dict], virtual_path: str) -> None:
    """Attach ``floor_name`` and ``mount`` to floor folders in one listing.

    Floor folders (live or archived) get ``mount: "floor"``; the archive
    folder gets ``mount: "archive"``. Other entries get ``floor_name: None``.
    """
    base = [part for part in virtual_path.strip("/").split("/") if part]
    # Floor folders only live at the top level and one level into the archive.
    if len(base) > 1:
        for entry in entries:
            entry.setdefault("floor_name", None)
        return
    names = floor_names()
    for entry in entries:
        if entry.get("mount") == "host" or not entry.get("is_dir"):
            entry.setdefault("floor_name", None)
            continue
        parts = [*base, str(entry["name"])]
        display = floor_display_name(parts, len(parts) - 1, names)
        entry["floor_name"] = display
        if display is None:
            continue
        entry["mount"] = "archive" if parts == [ARCHIVED_FLOORS_DIRNAME] else "floor"


def annotate_search_hit(hit: dict, *, in_company: bool, names: dict[str, str]) -> None:
    """Give one search hit a ``display_path`` and, when floor-level, a ``floor_name``.

    A floor-level folder also gets the ``mount`` a listing would give it
    (``floor`` or ``archive``). Host-root hits keep their absolute path as
    the display path.
    """
    path = str(hit["path"])
    hit["floor_name"] = None
    if not in_company:
        hit["display_path"] = path
        return
    hit["display_path"] = display_path(path, names)
    parts = [part for part in path.strip("/").split("/") if part]
    if not hit.get("is_dir") or not parts:
        return
    display = floor_display_name(parts, len(parts) - 1, names)
    if display is None:
        return
    hit["floor_name"] = display
    hit["mount"] = "archive" if parts == [ARCHIVED_FLOORS_DIRNAME] else "floor"


def is_top_level_hidden(name: str) -> bool:
    """At the company top level, dot-entries other than the archive are hidden."""
    return name.startswith(".") and name != ARCHIVED_FLOORS_DIRNAME


def _floor_level_parents() -> tuple[Path, Path]:
    company = company_root()
    return company, company / ARCHIVED_FLOORS_DIRNAME


def refuse_floor_level_destination(parent: Path) -> None:
    """400 when something would be created or put at floor level.

    That is directly in the company root or directly in the archive.
    ``parent`` is the resolved directory the result would land in.
    """
    if parent in _floor_level_parents():
        raise HTTPException(400, TOP_LEVEL_DESTINATION_WHY)


def refuse_floor_folder(path: Path) -> None:
    """409 when *path* is a floor folder, the archive, or an archived floor.

    A top-level entry that is not a floor (a stray folder) is not protected
    here; it can still be moved into a floor or deleted.
    """
    company, archive = _floor_level_parents()
    if path == archive or path.parent == archive:
        raise HTTPException(409, FLOOR_FOLDER_WHY)
    if path.parent == company and path.name in floor_names():
        raise HTTPException(409, FLOOR_FOLDER_WHY)
