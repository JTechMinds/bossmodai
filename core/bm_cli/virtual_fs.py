"""BossMod AI — Virtual filesystem mapping for the controlled BossMod CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.filesystem import (
    agent_artifact_dir,
    resolve_relative_path,
    slugify_name,
)
from core.bm_cli.floor_roots import (
    FloorRootUnavailable,
    agent_floor_id,
    floor_root,
    projects_root_for_storage_key,
)
from core.bm_cli.host_roots import (
    PathOutsideRootsError,
    configured_host_roots,
    denial_message,
    extra_host_roots,
    is_within_roots,
    looks_like_named_absolute_path,
    resolve_absolute_under_roots,
)

DEFAULT_CLI_CWD = "/me"
NOTES_FOLDER_PATH = "/me/notes"


@dataclass(frozen=True, slots=True)
class ResolvedCliPath:
    """A normalized virtual BossMod CLI path mapped into bounded storage."""

    virtual_path: str
    real_path: Path | None
    exists: bool
    is_dir: bool
    mount: str


def normalize_cli_path(cwd: str, raw_path: str | None = None) -> str:
    """Normalize a CLI path against a current working directory."""
    base = (raw_path or "").strip()
    if not base:
        source = cwd or DEFAULT_CLI_CWD
    elif base.startswith("/"):
        source = base
    elif cwd == "/":
        source = f"/{base}"
    else:
        source = f"{cwd.rstrip('/')}/{base}"

    parts: list[str] = []
    for item in source.split("/"):
        if item in {"", "."}:
            continue
        if item == "..":
            if parts:
                parts.pop()
            continue
        parts.append(item)
    return "/" + "/".join(parts)


def resolve_cli_path(agent_storage_key: str, cwd: str, raw_path: str | None = None) -> ResolvedCliPath:
    """Resolve a normalized CLI path into the bounded BossMod artifact roots.

    ``/me`` is the agent's own folder. ``/projects`` is the folder of the
    floor the agent lives on, so an agent never reaches another floor's
    projects.

    Raises:
        PathOutsideRootsError: The path is outside every allowed root, or it
            is under ``/projects`` and the agent has no floor (on vacation).
        LookupError: ``/projects`` or a named path was resolved for a storage
            key no agent owns.
        ValueError: A relative path escapes its root.
    """
    virtual_path = normalize_cli_path(cwd, raw_path)
    if virtual_path == "/":
        return ResolvedCliPath(
            virtual_path="/",
            real_path=None,
            exists=True,
            is_dir=True,
            mount="root",
        )

    parts = [item for item in virtual_path.strip("/").split("/") if item]
    if not parts:
        return ResolvedCliPath(virtual_path="/", real_path=None, exists=True, is_dir=True, mount="root")

    mount = parts[0]
    if mount == "me":
        root = agent_artifact_dir(agent_storage_key)
        relative = "/".join(parts[1:])
        candidate = root if not relative else resolve_relative_path(root, relative)
        return _resolved(virtual_path, candidate, mount)
    if mount == "projects":
        projects_root = _projects_mount(agent_storage_key, virtual_path)
        if len(parts) == 1:
            candidate = projects_root
        else:
            root = projects_root / slugify_name(parts[1])
            relative = "/".join(parts[2:])
            candidate = root if not relative else resolve_relative_path(root, relative)
        return _resolved(virtual_path, candidate, mount)

    # User-named absolute path: allow when it stays inside /me, /projects,
    # or an operator-configured extra host root. Canonicalize back onto the
    # virtual mount when the real path is already one of those trees.
    if looks_like_named_absolute_path(virtual_path):
        return _resolve_named_absolute(agent_storage_key, virtual_path)

    extras = extra_host_roots()
    raise PathOutsideRootsError(
        denial_message(virtual_path, extra_roots=extras)
        if extras
        else 'BossMod CLI paths must stay under "/", "/me", "/projects", or a configured host root.',
        raw_path=virtual_path,
    )


def _projects_mount(agent_storage_key: str, virtual_path: str) -> Path:
    """Return the real folder behind this agent's ``/projects``: its floor's.

    An agent with no floor (on vacation) has no ``/projects``; that is a
    denied path, reported as one, not an empty folder.
    """
    try:
        return projects_root_for_storage_key(agent_storage_key)
    except FloorRootUnavailable as exc:
        raise PathOutsideRootsError(
            f'"/projects" is not available: {exc}. An agent sees only its own floor\'s projects.',
            raw_path=virtual_path,
        ) from exc


def _resolved(virtual_path: str, candidate: Path, mount: str) -> ResolvedCliPath:
    return ResolvedCliPath(
        virtual_path=virtual_path,
        real_path=candidate,
        exists=candidate.exists(),
        is_dir=candidate.is_dir() if candidate.exists() else virtual_path.endswith("/"),
        mount=mount,
    )


def _resolve_named_absolute(agent_storage_key: str, virtual_path: str) -> ResolvedCliPath:
    """Map a user-named absolute path onto /me, /projects, or a host root.

    Only the agent's own floor folder counts as ``/projects``; another
    floor's folder is outside every root and is denied.
    """
    agent_root = agent_artifact_dir(agent_storage_key).resolve()
    floor_id = agent_floor_id(agent_storage_key)
    projects_root = floor_root(floor_id).resolve() if floor_id is not None else None
    extra_roots = extra_host_roots()
    roots = (agent_root, *((projects_root,) if projects_root is not None else ()), *extra_roots)
    candidate = resolve_absolute_under_roots(virtual_path, roots)
    if is_within_roots(candidate, (agent_root,)):
        relative = "." if candidate == agent_root else candidate.relative_to(agent_root).as_posix()
        mapped = "/me" if relative == "." else f"/me/{relative}"
        return _resolved(mapped, candidate, "me")
    if projects_root is not None and is_within_roots(candidate, (projects_root,)):
        relative = "." if candidate == projects_root else candidate.relative_to(projects_root).as_posix()
        mapped = "/projects" if relative == "." else f"/projects/{relative}"
        return _resolved(mapped, candidate, "projects")
    return _resolved(str(candidate), candidate, "host")


def virtual_root_entries(agent_storage_key: str) -> list[str]:
    """Return one agent's top-level virtual CLI mounts, including host roots.

    ``projects/`` is listed only for an agent with a floor: an agent on
    vacation has no ``/projects``.

    Raises:
        LookupError: No agent owns this storage key.
    """
    entries = ["me/"]
    if agent_floor_id(agent_storage_key) is not None:
        entries.append("projects/")
    for root in configured_host_roots():
        entries.append(f"{root}/")
    return entries


def is_notes_folder_path(virtual_path: str) -> bool:
    """True for the Notes folder itself (`/me/notes` and normalized equivalents)."""
    return normalize_cli_path("/", virtual_path) == NOTES_FOLDER_PATH


def is_soft_empty_virtual_directory(resolved: ResolvedCliPath) -> bool:
    """True when a known virtual folder was never created and should list empty.

    Nested named files under Notes still 404 when absent.
    """
    if resolved.exists:
        return False
    return is_notes_folder_path(resolved.virtual_path)
