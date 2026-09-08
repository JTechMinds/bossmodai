"""Import and export orchestration for agent packs.

Import hydrates hire-form fields only. It never creates or patches an
agent. Catalog imports read catalog.yaml at a pinned ref first, then the
pack file. Export reads an existing agent's profile fields back into a pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.agent_pack.catalog import (
    CATALOG_INDEX_PATH,
    CatalogEntry,
    parse_catalog_yaml,
    resolve_catalog_entry,
)
from core.agent_pack.github import (
    PackLocation,
    PackSource,
    assert_trust,
    catalog_pack_location,
    parse_catalog_repo,
    parse_github_pack_url,
    validate_pin_ref,
)
from core.agent_pack.quality import validate_pack_quality
from core.agent_pack.schema import (
    AgentPack,
    AgentPackError,
    export_agent_pack,
    pack_author_from_company,
    parse_pack_yaml,
)
from core.models.agent import Agent
from core.models.settings import AIPersonality


@dataclass(frozen=True)
class PackImportRequest:
    """Operator import request. Catalog (id/path + ref) or a GitHub file URL."""

    url: str | None = None
    pack_id: str | None = None
    path: str | None = None
    ref: str | None = None
    confirm: bool = False
    confirm_token: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class PackImportResult:
    """Hydrated hire fields plus the commit the app pinned."""

    pack: AgentPack
    location: PackLocation
    hire_fields: dict[str, Any]
    catalog_entry: CatalogEntry | None = None


def import_pack(
    request: PackImportRequest,
    *,
    source: PackSource,
    catalog_repo: str,
    catalog_path: str,
    extra_allowlist: str | None,
    confirm_secret: str,
) -> PackImportResult:
    """Fetch a pinned pack and return hire-form fields. Does not hire."""
    del catalog_path  # packs live at packs/<category>/<id>.agent.yaml via the index
    if request.agent_id:
        raise AgentPackError(
            "Pack import hydrates hire-form fields only and will not overwrite a live hire.",
            code="live_hire_overwrite",
            status=409,
        )
    url = (request.url or "").strip() or None
    pack_id = (request.pack_id or "").strip() or None
    path = (request.path or "").strip() or None
    ref = (request.ref or "").strip() or None
    if url and (pack_id or path):
        raise AgentPackError(
            "Pass either a GitHub URL or a catalog id/path, not both.",
            code="invalid_source",
        )
    if url:
        return _import_url(
            url,
            ref=ref,
            confirm=request.confirm,
            confirm_token=request.confirm_token,
            source=source,
            catalog_repo=catalog_repo,
            extra_allowlist=extra_allowlist,
            confirm_secret=confirm_secret,
        )
    if not ref:
        raise AgentPackError(
            "Catalog import requires a pinned commit SHA or tag.",
            code="floating_ref",
        )
    return _import_catalog(
        pack_id=pack_id,
        path=path,
        ref=ref,
        source=source,
        catalog_repo=catalog_repo,
    )


def export_pack(
    agent: Agent,
    *,
    personalities: Iterable[AIPersonality] | None = None,
    company_name: str | None = None,
    company_url: str | None = None,
) -> AgentPack:
    """Export an agent's specialty / description / done bar as a pack."""
    hint = _personality_hint(agent, personalities)
    author = pack_author_from_company(company_name, company_url)
    return export_agent_pack(agent, personality_hint=hint, pack_author=author)


def _import_url(
    url: str,
    *,
    ref: str | None,
    confirm: bool,
    confirm_token: str | None,
    source: PackSource,
    catalog_repo: str,
    extra_allowlist: str | None,
    confirm_secret: str,
) -> PackImportResult:
    location = parse_github_pack_url(url)
    if ref and ref != location.requested_ref:
        raise AgentPackError(
            "URL ref and request ref do not match.",
            code="invalid_source",
        )
    assert_trust(
        location,
        catalog_repo=catalog_repo,
        extra_allowlist=extra_allowlist,
        confirm=confirm,
        confirm_token=confirm_token,
        confirm_secret=confirm_secret,
    )
    sha = source.resolve_commit_sha(location.owner, location.repo, location.requested_ref)
    pinned = location.with_sha(sha)
    raw = source.fetch_file(pinned.owner, pinned.repo, pinned.path, pinned.commit_sha or sha)
    pack = _parse_imported_pack(raw)
    return PackImportResult(
        pack=pack,
        location=pinned,
        hire_fields=pack.hire_fields(),
    )


def _import_catalog(
    *,
    pack_id: str | None,
    path: str | None,
    ref: str,
    source: PackSource,
    catalog_repo: str,
) -> PackImportResult:
    validate_pin_ref(ref)
    owner, repo = parse_catalog_repo(catalog_repo)
    sha = source.resolve_commit_sha(owner, repo, ref)
    index_text = source.fetch_file(owner, repo, CATALOG_INDEX_PATH, sha)
    index = parse_catalog_yaml(index_text)
    entry = resolve_catalog_entry(index, pack_id=pack_id, path=path)
    location = catalog_pack_location(catalog_repo=catalog_repo, path=entry.path, ref=ref)
    pinned = location.with_sha(sha)
    raw = source.fetch_file(pinned.owner, pinned.repo, pinned.path, sha)
    pack = _parse_imported_pack(raw)
    return PackImportResult(
        pack=pack,
        location=pinned,
        hire_fields=pack.hire_fields(),
        catalog_entry=entry,
    )


def _parse_imported_pack(raw: str | bytes) -> AgentPack:
    """Schema-parse an imported pack and require senior quality sections."""
    pack = parse_pack_yaml(raw)
    validate_pack_quality(pack)
    return pack


def _personality_hint(
    agent: Agent,
    personalities: Iterable[AIPersonality] | None,
) -> str | None:
    template = (agent.prompt_template or "").strip()
    if not template or personalities is None:
        return None
    for personality in personalities:
        if (personality.prompt_template or "").strip() == template:
            name = (personality.name or "").strip()
            return name or None
    return None
