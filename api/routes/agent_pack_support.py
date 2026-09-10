"""Shared transport helpers for the agent-pack routes.

Two route modules are views of one pack pipeline: ``agent_packs`` browses and
imports, ``agent_templates`` installs into the local library. Both translate
``AgentPackError`` the same way and both must resolve the catalog repo, path,
allowlist and pin identically — reading those two different ways is how a
browse list and an install end up pinned to different commits.

These live here rather than in either route module so neither has to reach
into the other's privates.
"""

from __future__ import annotations

from fastapi import HTTPException

from core import config
from core.agent_pack import (
    ALLOWLIST_SETTING,
    CATALOG_PATH_SETTING,
    CATALOG_PIN_SETTING,
    CATALOG_REPO_SETTING,
    DEFAULT_CATALOG_PATH,
    DEFAULT_CATALOG_PIN,
    DEFAULT_CATALOG_REPO,
)
from core.agent_pack.schema import AgentPackError
import db


def http_error(exc: AgentPackError) -> HTTPException:
    """Translate a pack error into its HTTP form, preserving the code.

    The client branches on ``code`` — ``trust_required`` opens the confirm
    strip, everything else is shown as text — so the code travels in the body
    rather than being flattened into a message.
    """
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def catalog_settings() -> tuple[str, str, str | None, str]:
    """Resolve (catalog repo, catalog path, extra allowlist, confirm secret).

    Operator settings win over the shipped defaults. The secret is the local
    API token, which is what makes a trust confirmation unforgeable.
    """
    catalog_repo = config.get(CATALOG_REPO_SETTING) or DEFAULT_CATALOG_REPO
    catalog_path = config.get(CATALOG_PATH_SETTING) or DEFAULT_CATALOG_PATH
    extra = config.get(ALLOWLIST_SETTING)
    secret = db.ensure_local_api_token()
    return catalog_repo, catalog_path, extra, secret


def catalog_pin(requested: str | None = None) -> str:
    """Return the commit or tag the catalog is read at.

    An explicit request wins, then the operator's configured pin, then the
    shipped default. Never floats to a branch head: an unpinned catalog read
    is what lets a pack change under an operator between browse and install.
    """
    return (requested or "").strip() or config.get(CATALOG_PIN_SETTING) or DEFAULT_CATALOG_PIN
