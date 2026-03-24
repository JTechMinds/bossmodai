"""BossMod AI — Virtual filesystem mapping for the controlled BossMod CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.filesystem import (
    agent_artifact_dir,
    projects_artifact_root,
    project_artifact_dir,
    resolve_relative_path,
)

DEFAULT_CLI_CWD = "/me"


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
    """Resolve a normalized CLI path into the bounded BossMod artifact roots."""
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
    elif mount == "projects":
        if len(parts) == 1:
            root = projects_artifact_root()
            candidate = root
        else:
            root = project_artifact_dir(parts[1])
            relative = "/".join(parts[2:])
            candidate = root if not relative else resolve_relative_path(root, relative)
    else:
        raise ValueError('BossMod CLI paths must stay under "/", "/me", or "/projects".')

    return ResolvedCliPath(
        virtual_path=virtual_path,
        real_path=candidate,
        exists=candidate.exists(),
        is_dir=candidate.is_dir() if candidate.exists() else virtual_path.endswith("/") or candidate.suffix == "",
        mount=mount,
    )


def virtual_root_entries() -> list[str]:
    """Return the top-level virtual CLI mounts."""
    return ["me/", "projects/"]
