"""Own git repository for one shared project workspace.

``git init`` runs in the project directory only. An existing repository,
including a clone, is left alone. The projects mount itself is never a repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.bm_cli.filesystem import projects_artifact_root
from core.bm_cli.host_roots import is_within_roots
from core.bm_cli import install_layout


def project_directory_for(real_path: Path) -> Path | None:
    """Return the project directory that contains *real_path*, if any.

    The projects mount itself is not a project. Nested paths map to the
    first directory under that mount.
    """
    projects = projects_artifact_root().resolve()
    try:
        resolved = Path(real_path).resolve()
    except OSError:
        return None
    if resolved == projects or not is_within_roots(resolved, (projects,)):
        return None
    parts = resolved.relative_to(projects).parts
    if not parts or parts[0] in {".", ".."}:
        return None
    return projects / parts[0]


def ensure_project_repository(real_path: Path) -> Path | None:
    """Create the project directory's git repo when the path is inside one.

    Returns the project directory, or None when *real_path* is not inside a
    project. Raises ``ValueError`` when ``git init`` fails.
    """
    project = project_directory_for(real_path)
    if project is None:
        return None
    install = install_layout.app_install_root().resolve()
    project = project.resolve()
    if project == install or install in project.parents:
        raise ValueError(
            "Refusing to create a project git repository inside the BossMod application install."
        )
    project.mkdir(parents=True, exist_ok=True)
    if (project / ".git").exists():
        return project
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    result = subprocess.run(
        ["git", "-C", str(project), "init"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0 or not (project / ".git").exists():
        message = (result.stderr or result.stdout or "git init failed").strip()
        raise ValueError(
            "Could not create a git repository for this project workspace. " + message
        )
    return project
