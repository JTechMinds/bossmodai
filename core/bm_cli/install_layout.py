"""Application install vs company data root.

Project workspaces must not live inside the BossMod checkout. Agent git
walks parent directories for ``.git``; a project nested under the install
becomes a branch of the application repository.

On disk the company root holds one folder per floor, and each floor folder
holds that floor's projects (``<company>/<floor_id>/<project>``). The
floor mapping itself lives in ``core.bm_cli.floor_roots``; this module only
answers where the company root is and refuses a root inside the install.
"""

from __future__ import annotations

import os
from pathlib import Path

_INSTALL_ROOT = Path(__file__).resolve().parents[2]

COMPANY_ROOT_ENV = "BOSSMOD_COMPANY_ROOT"
# The flat per-install projects root this variable used to re-bind no longer
# exists. It is refused, not honoured, so an operator who set it learns the
# layout changed instead of silently getting a different directory.
RETIRED_PROJECTS_ROOT_ENV = "BOSSMOD_PROJECTS_ROOT"


class RetiredProjectsRootSetting(RuntimeError):
    """``BOSSMOD_PROJECTS_ROOT`` is set, but only ``BOSSMOD_COMPANY_ROOT`` is read.

    A RuntimeError, not a ValueError: path-resolution callers treat
    ValueError as "this path is denied", and a startup misconfiguration must
    not be reported to an agent as a denied path.
    """


def app_install_root() -> Path:
    """Return the BossMod application checkout (source tree)."""
    return _INSTALL_ROOT


def legacy_projects_root() -> Path:
    """Return the historical in-tree projects directory.

    New projects are not created here. The directory is left untouched so an
    operator can move it by hand.
    """
    return app_install_root() / "artifacts" / "projects"


def default_company_root() -> Path:
    """Return the configured company data root, before the outside-install check.

    ``BOSSMOD_COMPANY_ROOT`` re-binds the root. When unset, the company lives
    in a sibling data directory next to the checkout, not under it.

    Raises:
        RetiredProjectsRootSetting: ``BOSSMOD_PROJECTS_ROOT`` is still set.
    """
    retired = os.environ.get(RETIRED_PROJECTS_ROOT_ENV, "").strip()
    if retired:
        raise RetiredProjectsRootSetting(
            f"{RETIRED_PROJECTS_ROOT_ENV} is no longer read. Projects now live under a "
            "company root with one folder per floor "
            "(<company>/<floor id>/<project>). Unset "
            f"{RETIRED_PROJECTS_ROOT_ENV} and, if the company root should not be the "
            f"default ({app_install_root().parent / 'bossmod-data' / 'company'}), "
            f"set {COMPANY_ROOT_ENV} instead."
        )
    raw = os.environ.get(COMPANY_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return app_install_root().parent / "bossmod-data" / "company"


def company_layout_note() -> str:
    """Explain where the company root must live and what is left untouched.

    Nothing in the legacy in-tree project tree is copied, rewritten, or
    deleted.
    """
    return (
        "The company root must live outside the application install. "
        f"The default is {app_install_root().parent / 'bossmod-data' / 'company'}; "
        f"set {COMPANY_ROOT_ENV} to use another directory outside the install. "
        f"The legacy in-tree tree at {legacy_projects_root()} is not moved or rewritten."
    )


def require_company_root(candidate: Path) -> Path:
    """Resolve *candidate* and reject a root inside the application install.

    Raises:
        ValueError: The path cannot be resolved, or it is inside the install.
    """
    try:
        resolved = Path(candidate).expanduser().resolve()
    except OSError as exc:
        raise ValueError(
            "Company root cannot be resolved. " + company_layout_note()
        ) from exc
    install = app_install_root().resolve()
    if resolved == install or install in resolved.parents:
        raise ValueError(
            "Project workspaces must live outside the BossMod application install. "
            f"{resolved} is inside {install}. "
            + company_layout_note()
        )
    return resolved
