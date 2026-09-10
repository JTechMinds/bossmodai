"""Local agent template library — list, install, uninstall.

A template is a locally-installed, pinned snapshot of an agent pack, and it is
the only thing that pre-fills the create-agent form. Install runs through the
same ``import_pack`` pipeline the browse door uses, so pin validation, the repo
allowlist, the trust gate and the refusal of ``agent_id`` are inherited
unchanged — there is no second import pipeline. Installing still never creates
an agent; it writes one row.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Settings resolution and error translation are shared with the browse door on
# purpose: both routes are views of one pack pipeline, and the catalog repo,
# path, allowlist and pin must not be read two different ways.
from api.routes.agent_pack_support import catalog_pin, catalog_settings, http_error
from core.agent_pack import (
    GitHubPackSource,
    PackImportRequest,
    import_pack,
    pack_content_hash,
)
from core.agent_pack.github import PackLocation
from core.agent_pack.schema import AgentPackError
from core.models.agent_template import AgentTemplate
import db

router = APIRouter()
_SOURCE = GitHubPackSource()

# A pack fetched from a bare GitHub URL has no catalog row and therefore no
# category slug. Those installs group under one fixed bucket in the picker.
URL_INSTALL_CATEGORY = "imported"


class AgentTemplateInstallBody(BaseModel):
    """Install one pack into the local library, by catalog id or GitHub URL.

    ``ref`` pins the commit; a catalog install without one uses the app's
    configured catalog pin, the same pin the browse list was rendered from.
    ``confirm`` / ``confirm_token`` answer the trust gate for a repo that is
    not allowlisted. ``agent_id`` is declared only so it can be rejected —
    installing a template never patches a live hire.
    """

    id: str | None = None
    url: str | None = None
    ref: str | None = None
    confirm: bool = False
    confirm_token: str | None = None
    agent_id: str | None = Field(
        default=None,
        description="Rejected if set; installing never patches a hire.",
    )


def _pack_identity(location: PackLocation) -> str:
    """Return the ref-free identity of a URL-installed pack file.

    ``PackLocation.canonical_source()`` embeds the requested ref, so it would
    read the same file at a newer commit as a different pack and install a
    duplicate row. The library's natural key has to survive a re-pin, so the
    ref is dropped and only the repo path remains.
    """
    return f"github.com/{location.owner}/{location.repo}/{location.path}".lower()


@router.get("/agent-templates")
def list_agent_templates() -> list[AgentTemplate]:
    """Return every installed template, ordered by category then title.

    One indexed local read; no network and no catalog fetch. An empty library
    is an empty list, not an error.
    """
    return db.list_agent_templates()


@router.post("/agent-templates")
def install_agent_template(body: AgentTemplateInstallBody) -> AgentTemplate:
    """Install (or re-install) one pack into the local template library.

    Fetches the pack at its pinned commit through ``import_pack``, hashes its
    canonical YAML, and upserts one row keyed by catalog ``pack_id`` or by the
    ref-free source URL. Re-installing updates that row — including
    ``content_hash`` and ``updated_at`` — instead of duplicating it. Returns
    the stored ``AgentTemplate``.

    Failure modes, all translated from ``AgentPackError`` with the code intact:
    ``invalid_source`` (400) when neither an id nor a URL is given,
    ``floating_ref`` (400) for an unpinned ref, ``trust_required`` (403) for a
    URL outside the allowlist without ``confirm``, ``live_hire_overwrite``
    (409) when ``agent_id`` is set, and ``pin_unresolved`` /  ``fetch_failed``
    for a ref or file GitHub could not serve.
    """
    catalog_repo, catalog_path, extra, secret = catalog_settings()
    url = (body.url or "").strip() or None
    pack_id = (body.id or "").strip() or None
    ref = (body.ref or "").strip() or None
    if url is None and pack_id is None:
        raise http_error(
            AgentPackError(
                "Install needs a catalog pack id or a GitHub pack URL.",
                code="invalid_source",
            )
        )
    if url is None:
        # A URL carries its own ref and import_pack rejects a mismatched one,
        # so the configured-pin default is catalog-only.
        ref = catalog_pin(ref)

    try:
        result = import_pack(
            PackImportRequest(
                url=url,
                pack_id=pack_id,
                ref=ref,
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

    commit_sha = result.location.commit_sha
    if not commit_sha:
        raise http_error(
            AgentPackError(
                "Import returned no pinned commit; refusing to install an unpinned template.",
                code="pin_unresolved",
                status=502,
            )
        )

    pack = result.pack
    entry = result.catalog_entry
    if entry is not None:
        source, key_pack_id, source_url = "catalog", entry.id, None
        category, title = entry.category, entry.title
    else:
        # A URL install has no catalog title; the pack's own specialty is the
        # only name it carries.
        source, key_pack_id, source_url = "url", None, _pack_identity(result.location)
        category, title = URL_INSTALL_CATEGORY, pack.specialty
    author = pack.pack_author

    return db.upsert_agent_template(
        source=source,
        pack_id=key_pack_id,
        source_url=source_url,
        category=category,
        title=title,
        specialty=pack.specialty,
        description=pack.description,
        what_done_looks_like=pack.what_done_looks_like,
        personality_hint=pack.personality_hint,
        tools_hint=list(pack.tools_hint),
        author_name=author.name if author else None,
        author_url=author.url if author else None,
        commit_sha=commit_sha,
        content_hash=pack_content_hash(pack),
    )


@router.delete("/agent-templates/{template_id}", status_code=204)
def uninstall_agent_template(template_id: str):
    """Remove one installed template. 404s when the id is not installed.

    Local only: nothing is fetched, and agents created from the template are
    untouched — a template is a snapshot, not a live link.
    """
    if not db.delete_agent_template(template_id):
        raise HTTPException(404, "Agent template not found")
