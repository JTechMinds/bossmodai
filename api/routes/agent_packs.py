"""Agent pack import (hydrate hire fields) and export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.routes.agent_pack_support import catalog_pin, catalog_settings, http_error
from core import config
from core.agent_pack import (
    GitHubPackSource,
    CatalogListResult,
    PackImportRequest,
    describe_pack,
    export_pack,
    import_pack,
    list_catalog,
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


def _group_categories(result: CatalogListResult) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for card in result.packs:
        entry = card.entry
        if entry.category not in grouped:
            grouped[entry.category] = []
            order.append(entry.category)
        payload: dict[str, Any] = {
            "id": entry.id,
            "kind": entry.kind,
            "path": entry.path,
            "category": entry.category,
            "title": entry.title,
        }
        if card.summary:
            payload["summary"] = card.summary
        if card.specialty:
            payload["specialty"] = card.specialty
        if card.description:
            payload["description"] = card.description
        if card.what_done_looks_like:
            payload["what_done_looks_like"] = card.what_done_looks_like
        if card.description is not None and card.what_done_looks_like is not None:
            # Grouping needs no filter of its own: `list_catalog` withholds
            # every row that failed the parse-and-quality gate, so a card that
            # reaches here always carries both strings. The split is done here,
            # with the parser the quality gate uses, rather than left to the
            # reader.
            payload["sections"] = describe_pack(
                card.description, card.what_done_looks_like
            )
        if card.tools_hint:
            payload["tools_hint"] = list(card.tools_hint)
        if card.content_hash:
            # The marketplace compares this against the installed template's
            # hash to decide between "Installed" and "Update available".
            payload["content_hash"] = card.content_hash
        if card.pack_author:
            payload["pack_author"] = card.pack_author
        grouped[entry.category].append(payload)
    return [{"id": category, "packs": grouped[category]} for category in order]


def _withheld_payload(result: CatalogListResult) -> list[dict[str, str]]:
    """The catalog rows the app listed in its index but will not offer.

    The operator owns the catalog repo, so this is the signal that their own
    repo shipped something this app will not show: without it they would merge
    a PR, never see the pack in the grid, and get no reason why.

    ONE list with an explicit ``kind`` rather than a ``refused`` list beside an
    ``unavailable`` one — see ``CatalogListResult`` for why. ``kind`` is the
    fact the reader acts on: a refusal names what to fix in the pack, an
    unavailability says only that the file could not be read and the move is to
    retry. ``code`` stays alongside it as the precise cause, but no consumer
    should be classifying transport-versus-content out of it.

    ``path`` and ``category`` are published, not just logged. A maintainer told
    ``code-auditor: pack_quality`` still has to go and find the file, and this
    payload is flat while ``categories`` is grouped — so without ``category``
    the one derivation the grid's own shape invites, "which category lost a
    pack", cannot be made at all.

    Args:
        result: The list result whose ``withheld`` rows are being reported.

    Returns:
        One ``{"id", "path", "category", "title", "kind", "code", "message"}``
        mapping per withheld row, in catalog order. Empty when every listed
        pack was read and passed the gate.
    """
    return [
        {
            "id": row.id,
            "path": row.path,
            "category": row.category,
            "title": row.title,
            "kind": row.kind,
            "code": row.code,
            "message": row.message,
        }
        for row in result.withheld
    ]


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


@router.get("/agent-packs")
def list_agent_packs(ref: str | None = None) -> dict[str, Any]:
    """List catalog packs at the pinned commit for the marketplace's browse list.

    Does not hire and does not install. Picking a pack goes through
    ``POST /api/agent-templates``, which snapshots it into the local template
    library the Add agent picker reads. No UI path reaches
    ``POST /api/agent-packs/import`` any more; that route stays a documented
    API surface with tests of its own.

    ``categories`` carries only packs that were read AND passed the same gate
    install runs — ``list_catalog`` withholds the rest — so a listed card is
    always installable and the category counts are counts of installable packs.
    ``withheld`` reports what did not make it, one
    ``{"id", "path", "category", "title", "kind", "code", "message"}`` row
    each, so a catalog maintainer can see that their own repo shipped a pack
    this app will not show instead of watching it vanish. ``kind`` separates
    the two reasons that are not the same reason: ``"refused"`` (read and
    rejected — fix the pack) from ``"unavailable"`` (never read — retry). A
    catalog whose every pack is invalid is an empty ``categories`` plus a full
    ``withheld`` — not an error.
    """
    catalog_repo, _catalog_path, _extra, _secret = catalog_settings()
    pin = catalog_pin(ref)
    try:
        result = list_catalog(source=_SOURCE, catalog_repo=catalog_repo, ref=pin)
    except AgentPackError as exc:
        raise http_error(exc) from exc
    return {
        "repo": result.repo,
        "ref": result.requested_ref,
        "commit_sha": result.commit_sha,
        "pin_short": result.commit_sha[:7],
        "categories": _group_categories(result),
        "withheld": _withheld_payload(result),
    }


@router.post("/agent-packs/import")
def import_agent_pack(body: AgentPackImportBody) -> dict[str, Any]:
    """Fetch a pinned pack and return hire-form fields. Does not hire."""
    catalog_repo, catalog_path, extra, secret = catalog_settings()
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
        raise http_error(exc) from exc
    payload: dict[str, Any] = {
        "pack": result.pack.as_dict(),
        "hire_fields": result.hire_fields,
        "ignored_keys": list(result.pack.ignored_keys),
        "pin": _pin_payload(result.location),
    }
    if result.catalog_entry is not None:
        catalog: dict[str, Any] = {
            "id": result.catalog_entry.id,
            "kind": result.catalog_entry.kind,
            "path": result.catalog_entry.path,
            "category": result.catalog_entry.category,
            "title": result.catalog_entry.title,
        }
        if result.catalog_entry.summary:
            catalog["summary"] = result.catalog_entry.summary
        payload["catalog"] = catalog
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
        raise http_error(exc) from exc
    return {
        "pack": pack.as_dict(),
        "yaml": pack.to_yaml(),
        "hire_fields": pack.hire_fields(),
        "ignored_keys": list(pack.ignored_keys),
        "agent_id": agent.id,
    }
