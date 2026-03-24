"""BossMod AI — Safe artifact path helpers for BossMod CLI."""

from __future__ import annotations

import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACTS_ROOT = _ROOT / "artifacts"
_AGENTS_ROOT = _ARTIFACTS_ROOT / "agents"
_PROJECTS_ROOT = _ARTIFACTS_ROOT / "projects"


def ensure_artifact_roots() -> None:
    """Create the top-level artifact directories when missing."""
    _AGENTS_ROOT.mkdir(parents=True, exist_ok=True)
    _PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)


def slugify_name(value: str) -> str:
    """Return a filesystem-safe slug for agent/project names."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-_.")
    return text or "untitled"


def agent_artifact_dir(agent_name: str) -> Path:
    """Return the personal artifact directory for an agent."""
    ensure_artifact_roots()
    path = _AGENTS_ROOT / slugify_name(agent_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_artifact_dir(project_name: str) -> Path:
    """Return the shared artifact directory for a project."""
    ensure_artifact_roots()
    return _PROJECTS_ROOT / slugify_name(project_name)


def resolve_relative_path(root: Path, relative_path: str) -> Path:
    """Resolve a relative artifact path and reject path traversal."""
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if candidate == root_resolved:
        return candidate
    if root_resolved not in candidate.parents:
        raise ValueError("Path escapes the allowed artifact directory")
    return candidate
