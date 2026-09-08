"""Agent pack import (hydrate hire fields) and export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import config
from core.agent_pack import (
    ALLOWLIST_SETTING,
    CATALOG_PATH_SETTING,
    CATALOG_REPO_SETTING,
    DEFAULT_CATALOG_PATH,
    DEFAULT_CATALOG_REPO,
    GitHubPackSource,
    PackImportRequest,
    export_pack,
    import_pack,
)
from core.agent_pack.github import PackLocation
from core.agent_pack.schema import AgentPackError
import db

router = APIRouter()
_SOURCE = GitHubPackSource()


class AgentPackImportBody(BaseModel):
    """Import a pack from the catalog or a GitHub file URL.

    Hydrates hire-form fields only. Does not create or patch an agent.
    ``agent_id`` is rejected so a live hire cannot be silently overwritten.
    """

    url: str | None = None
    id: str | None = None
    path: str | None = None
    ref: str | None = None
    confirm: bool = False
    confirm_token: str | None = None
    agent_id: str | None = Field(default=None, description="Rejected if set; import never patches a hire.")


def _http_error(exc: AgentPackError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _catalog_settings() -> tuple[str, str, str | None, str]:
    catalog_repo = config.get(CATALOG_REPO_SETTING) or DEFAULT_CATALOG_REPO
    catalog_path = config.get(CATALOG_PATH_SETTING) or DEFAULT_CATALOG_PATH
    extra = config.get(ALLOWLIST_SETTING)
    secret = db.ensure_local_api_token()
    return catalog_repo, catalog_path, extra, secret


def _pin_payload(location: PackLocation) -> dict[str, Any]:
    return {
        "owner": location.owner,
        "repo": location.repo,
        "path": location.path,
        "ref": location.requested_ref,
        "commit_sha": location.commit_sha,
        "from_catalog": location.from_catalog,
        "canonical": location.canonical_source(),
    }


@router.post("/agent-packs/import")
def import_agent_pack(body: AgentPackImportBody) -> dict[str, Any]:
    """Fetch a pinned pack and return hire-form fields. Does not hire."""
    catalog_repo, catalog_path, extra, secret = _catalog_settings()
    try:
        result = import_pack(
            PackImportRequest(
                url=body.url,
                pack_id=body.id,
                path=body.path,
                ref=body.ref,
                confirm=body.confirm,
                confirm_token=body.confirm_token,
                agent_id=body.agent_id,
            ),
            source=_SOURCE,
            catalog_repo=catalog_repo,
            catalog_path=catalog_path,
            extra_allowlist=extra,
            confirm_secret=secret,
        )
    except AgentPackError as exc:
        raise _http_error(exc) from exc
    payload: dict[str, Any] = {
        "pack": result.pack.as_dict(),
        "hire_fields": result.hire_fields,
        "ignored_keys": list(result.pack.ignored_keys),
        "pin": _pin_payload(result.location),
    }
    if result.catalog_entry is not None:
        payload["catalog"] = {
            "id": result.catalog_entry.id,
            "kind": result.catalog_entry.kind,
            "path": result.catalog_entry.path,
            "category": result.catalog_entry.category,
            "title": result.catalog_entry.title,
        }
    return payload


@router.get("/agents/{agent_id}/pack")
def export_agent_pack(agent_id: str) -> dict[str, Any]:
    """Export one agent's hire-contract profile as a pack (YAML + fields)."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    try:
        pack = export_pack(
            agent,
            personalities=db.list_personalities(),
            company_name=config.get("company_name"),
            company_url=config.get("company_url"),
        )
    except AgentPackError as exc:
        raise _http_error(exc) from exc
    return {
        "pack": pack.as_dict(),
        "yaml": pack.to_yaml(),
        "hire_fields": pack.hire_fields(),
        "ignored_keys": list(pack.ignored_keys),
        "agent_id": agent.id,
    }
