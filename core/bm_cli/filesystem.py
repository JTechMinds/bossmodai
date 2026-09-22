"""BossMod AI — Safe artifact path helpers for BossMod CLI."""

from __future__ import annotations

import re
from pathlib import Path

from core.bm_cli.install_layout import default_projects_root, require_projects_root


_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACTS_ROOT = _ROOT / "artifacts"
_AGENTS_ROOT = _ARTIFACTS_ROOT / "agents"
# Live project mount. Tests replace this. The default is outside the checkout;
# ``BOSSMOD_PROJECTS_ROOT`` re-binds it. The in-tree artifacts/projects tree
# is not rewritten.
_DEFAULT_PROJECTS_ROOT = default_projects_root()
_PROJECTS_ROOT = _DEFAULT_PROJECTS_ROOT

# Backup / database files must never be served by the company browser,
# even if a later change widens the company root back to artifacts/.
DENIED_COMPANY_FILE_SUFFIXES = frozenset({".bak", ".sqlite3", ".db"})


def ensure_artifact_roots() -> None:
    """Create the top-level artifact directories when missing."""
    _AGENTS_ROOT.mkdir(parents=True, exist_ok=True)
    projects_artifact_root()


def _live_projects_root() -> Path:
    """Return the project mount, honoring a monkeypatched ``_PROJECTS_ROOT``."""
    candidate = _PROJECTS_ROOT
    if candidate == _DEFAULT_PROJECTS_ROOT:
        candidate = default_projects_root()
    return require_projects_root(Path(candidate))


def artifacts_root() -> Path:
    """Return the bounded BossMod artifacts root."""
    ensure_artifact_roots()
    return _ARTIFACTS_ROOT


def agents_artifact_root() -> Path:
    """Return the bounded BossMod per-agent artifact root."""
    ensure_artifact_roots()
    return _AGENTS_ROOT


def projects_artifact_root() -> Path:
    """Return the shared project root, outside the application install."""
    root = _live_projects_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def company_files_root() -> Path:
    """Return the company file browser root (shared projects only).

    ``artifacts/db_backups`` and ``artifacts/agents`` stay outside this tree.
    Project directories live in the separated data root, not the checkout.
    """
    _AGENTS_ROOT.mkdir(parents=True, exist_ok=True)
    return projects_artifact_root()


def is_denied_company_file(path: Path) -> bool:
    """Return True for backup/database files that must not be served."""
    return path.suffix.lower() in DENIED_COMPANY_FILE_SUFFIXES


def normalize_company_relative_path(relative_path: str) -> str:
    """Normalize a company-browser path relative to ``company_files_root()``.

    Historical UI/API paths were rooted at ``artifacts/`` and therefore
    prefixed with ``/projects``. Strip a single leading ``projects``
    component so those paths keep working after the remount.
    """
    cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
    if not cleaned or cleaned == ".":
        return "."
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if parts and parts[0] == "projects":
        parts = parts[1:]
    return "/".join(parts) if parts else "."


def slugify_name(value: str) -> str:
    """Return a filesystem-safe slug for agent/project names."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-_.")
    return text or "untitled"


def legacy_agent_artifact_dir(agent_name: str) -> Path:
    """Return the legacy name-based personal artifact directory for an agent."""
    ensure_artifact_roots()
    return _AGENTS_ROOT / slugify_name(agent_name)


def transitional_agent_id_artifact_dir(agent_id: str) -> Path:
    """Return the transitional personal artifact directory keyed by raw agent id."""
    ensure_artifact_roots()
    return _AGENTS_ROOT / agent_id


def agent_artifact_dir(storage_key: str) -> Path:
    """Return the canonical immutable personal artifact directory for a storage key."""
    ensure_artifact_roots()
    path = _AGENTS_ROOT / storage_key
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_artifact_dir(project_name: str) -> Path:
    """Return the shared artifact directory for a project."""
    return projects_artifact_root() / slugify_name(project_name)


def resolve_relative_path(root: Path, relative_path: str) -> Path:
    """Resolve a relative artifact path and reject path traversal."""
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if candidate == root_resolved:
        return candidate
    if root_resolved not in candidate.parents:
        raise ValueError("Path escapes the allowed artifact directory")
    return candidate


def resolve_company_relative_path(root: Path, relative_path: str) -> Path:
    """Resolve a company-browser path inside the projects tree.

    Rejects path traversal and backup/database file suffixes.
    """
    candidate = resolve_relative_path(root, normalize_company_relative_path(relative_path))
    if is_denied_company_file(candidate):
        raise ValueError("File type is not allowed in the company workspace")
    return candidate
