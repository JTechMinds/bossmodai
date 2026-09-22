"""Application install vs project data root.

Project workspaces must not live inside the BossMod checkout. Agent git
walks parent directories for ``.git``; a project nested under the install
becomes a branch of the application repository.
"""

from __future__ import annotations

import os
from pathlib import Path

_INSTALL_ROOT = Path(__file__).resolve().parents[2]


def app_install_root() -> Path:
    """Return the BossMod application checkout (source tree)."""
    return _INSTALL_ROOT


def legacy_projects_root() -> Path:
    """Return the historical in-tree projects directory.

    New projects are not created here. The directory is left untouched so an
    operator can move or re-bind it.
    """
    return app_install_root() / "artifacts" / "projects"


def default_projects_root() -> Path:
    """Return the configured project data root, before the outside-install check.

    ``BOSSMOD_PROJECTS_ROOT`` re-binds the root. When unset, projects live in
    a sibling data directory next to the checkout, not under it.
    """
    raw = os.environ.get("BOSSMOD_PROJECTS_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return app_install_root().parent / "bossmod-data" / "projects"


def projects_migration_note() -> str:
    """Explain how an existing in-tree project tree is adopted.

    Nothing is copied, rewritten, or deleted. The operator moves the
    directory or points ``BOSSMOD_PROJECTS_ROOT`` at the moved path.
    """
    return (
        "Existing project directories are not moved or rewritten. "
        f"Move {legacy_projects_root()} to {default_projects_root()} "
        "(or to another directory outside the application install). "
        "When that directory is not the default, set BOSSMOD_PROJECTS_ROOT "
        "to the moved path before starting BossMod. "
        "The old tree stays where it is until you move or re-bind it."
    )


def require_projects_root(candidate: Path) -> Path:
    """Resolve *candidate* and reject a root inside the application install."""
    try:
        resolved = Path(candidate).expanduser().resolve()
    except OSError as exc:
        raise ValueError(
            "Project workspace root cannot be resolved. " + projects_migration_note()
        ) from exc
    install = app_install_root().resolve()
    if resolved == install or install in resolved.parents:
        raise ValueError(
            "Project workspaces must live outside the BossMod application install. "
            f"{resolved} is inside {install}. "
            + projects_migration_note()
        )
    return resolved
