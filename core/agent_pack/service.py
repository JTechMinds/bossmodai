"""Import and export orchestration for agent packs.

Import hydrates hire-form fields only. It never creates or patches an
agent. Export reads an existing agent's profile fields back into a pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.agent_pack.github import (
    PackLocation,
    PackSource,
    assert_trust,
    catalog_location,
    parse_github_pack_url,
)
from core.agent_pack.schema import AgentPack, AgentPackError, export_agent_pack, parse_pack_yaml
from core.models.agent import Agent
from core.models.settings import AIPersonality


@dataclass(frozen=True)
class PackImportRequest:
    """Operator import request. Catalog (path+ref) or a GitHub file URL."""

    url: str | None = None
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
    if request.agent_id:
        raise AgentPackError(
            "Pack import hydrates hire-form fields only and will not overwrite a live hire.",
            code="live_hire_overwrite",
            status=409,
        )
    location = _resolve_location(
        request,
        catalog_repo=catalog_repo,
        catalog_path=catalog_path,
    )
    assert_trust(
        location,
        catalog_repo=catalog_repo,
        extra_allowlist=extra_allowlist,
        confirm=request.confirm,
        confirm_token=request.confirm_token,
        confirm_secret=confirm_secret,
    )
    sha = source.resolve_commit_sha(location.owner, location.repo, location.requested_ref)
    pinned = location.with_sha(sha)
    raw = source.fetch_file(pinned.owner, pinned.repo, pinned.path, pinned.commit_sha or sha)
    pack = parse_pack_yaml(raw)
    return PackImportResult(
        pack=pack,
        location=pinned,
        hire_fields=pack.hire_fields(),
    )


def export_pack(
    agent: Agent,
    *,
    personalities: Iterable[AIPersonality] | None = None,
) -> AgentPack:
    """Export an agent's specialty / description / done bar as a pack."""
    hint = _personality_hint(agent, personalities)
    return export_agent_pack(agent, personality_hint=hint)


def _resolve_location(
    request: PackImportRequest,
    *,
    catalog_repo: str,
    catalog_path: str,
) -> PackLocation:
    url = (request.url or "").strip() or None
    path = (request.path or "").strip() or None
    ref = (request.ref or "").strip() or None
    if url and path:
        raise AgentPackError(
            "Pass either a GitHub URL or a catalog path, not both.",
            code="invalid_source",
        )
    if url:
        location = parse_github_pack_url(url)
        if ref and ref != location.requested_ref:
            raise AgentPackError(
                "URL ref and request ref do not match.",
                code="invalid_source",
            )
        return location
    if path:
        if not ref:
            raise AgentPackError(
                "Catalog import requires a pinned commit SHA or tag.",
                code="floating_ref",
            )
        return catalog_location(
            catalog_repo=catalog_repo,
            catalog_path=catalog_path,
            path=path,
            ref=ref,
        )
    raise AgentPackError(
        "Pack import requires a GitHub URL or a catalog path plus pinned ref.",
        code="invalid_source",
    )


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
