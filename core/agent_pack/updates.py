"""Catalog updates for installed packs and the agents hired from them.

Planning is pure: ``plan_updates`` compares a catalog listing with the
installed templates and the linked agents and decides what would change,
touching nothing. ``check_updates`` and ``apply_updates`` are the I/O around
it. Every GitHub read happens before any write, and every write of one apply
— templates, the catalog pin, agents — lands in one database transaction, so
an update is never half-applied.

Staleness is per pack file (its ``content_hash``), while the ``from → to``
versions are catalog commits: "catalog version", not the commit where that one
file last changed. Pinning per file would cost a GitHub API call per pack.
Each version carries its commit's committer date, which is what the operator
reads; the SHAs stay internal.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from core.agent_loop.communication_contract import load_communication_value
from core.agent_pack.github import CATALOG_PIN_SETTING, PackSource, parse_catalog_repo, validate_pin_ref
from core.agent_pack.schema import AgentPackError
from core.agent_pack.service import CatalogListPack, CatalogListResult, list_catalog
from core.models.agent import Agent, normalize_hire_text

if TYPE_CHECKING:
    # Type-only: core.models.agent_template imports core.agent_pack.service,
    # so a runtime import here would be circular.
    from core.models.agent_template import AgentTemplate

# ``db`` is imported inside the I/O functions below, never at module top:
# db imports core.models.agent_template, which imports this package, so a
# top-level import would make loading either one a cycle.

# A pack an installed template or a linked agent names that the catalog at the
# target commit no longer lists at all.
SKIPPED_REMOVED = "removed"
# The catalog still offers the pack, but no template for it is installed, so
# the agents hired from it have nothing to be updated from.
SKIPPED_NOT_INSTALLED = "not_installed"

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CATALOG_PIN_CATEGORY = "agent_packs"


@dataclass(frozen=True)
class TemplateUpdate:
    """One installed catalog template whose pack file changed at the target."""

    template_id: str
    pack_id: str
    title: str
    from_sha: str | None
    to_sha: str
    # Committer dates of the two catalog versions. ``from_date`` is None only
    # for a row installed before dates were recorded, until ``check_updates``
    # resolves it.
    from_date: datetime | None
    to_date: datetime


@dataclass(frozen=True)
class AgentUpdate:
    """One linked agent whose contract was written from an older pack file.

    ``edited`` is true when the agent's current contract no longer matches
    the one the pack last wrote, so an update would overwrite operator edits.
    ``from_date`` / ``to_date`` are as on ``TemplateUpdate``.
    Not to be confused with ``core.models.AgentUpdate``, the PATCH payload.
    """

    agent_id: str
    name: str
    pack_id: str
    template_title: str
    from_sha: str | None
    to_sha: str
    from_date: datetime | None
    to_date: datetime
    edited: bool


# A plan row that carries a ``from_sha`` / ``from_date`` pair.
_Row = TypeVar("_Row", TemplateUpdate, AgentUpdate)


@dataclass(frozen=True)
class SkippedPack:
    """A pack an update leaves alone, and why. Always reported, never hidden.

    ``kind`` is ``refused`` / ``unavailable`` (the catalog withheld it at the
    target), ``removed`` (no longer in the index) or ``not_installed`` (agents
    are linked to it but no template for it is installed).
    """

    pack_id: str
    title: str
    kind: str
    code: str
    message: str


@dataclass(frozen=True)
class UpdatePlan:
    """What an update to ``target_sha`` changes, computed before any write.

    ``pinned_date`` and ``target_date`` are the two commits' committer dates.
    """

    repo: str
    pinned_sha: str
    target_sha: str
    pinned_date: datetime
    target_date: datetime
    templates: tuple[TemplateUpdate, ...]
    agents: tuple[AgentUpdate, ...]
    skipped: tuple[SkippedPack, ...]


def contract_hash(
    description: str | None,
    what_done_looks_like: str | None,
    communication: dict[str, Any] | None,
) -> str:
    """Hash the part of an agent's setup a pack update overwrites.

    The single definition used at hire, at apply and for the "edited" check,
    so the three can never disagree about what "unchanged" means. Text is
    normalized the way ``AgentCreate`` normalizes it (stripped, empty is
    ``None``) and ``communication`` through ``load_communication_value``, so
    a round trip through the hire form does not read as an edit.

    Args:
        description: The agent's or template's description.
        what_done_looks_like: The done bar (``agents.done_fail_bar``).
        communication: The four-key communication mapping, or ``None``.

    Returns:
        The sha256 hex digest of the canonical JSON of the three fields.

    Raises:
        CommunicationContractError: ``communication`` is not a valid contract.
    """
    payload = {
        "description": normalize_hire_text(description),
        "what_done_looks_like": normalize_hire_text(what_done_looks_like),
        "communication": load_communication_value(communication),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def plan_updates(
    catalog: CatalogListResult,
    templates: list[AgentTemplate],
    agents: list[Agent],
    pinned_sha: str,
    pinned_date: datetime,
) -> UpdatePlan:
    """Decide which templates and agents a move to ``catalog`` would update.

    Pure: no I/O. Only catalog templates take part; a URL template follows
    its own URL ref, and a local one has no pack. A template is listed when its
    stored ``content_hash`` differs from the card's. An agent linked by
    ``pack_id`` is listed when its ``pack_content_hash`` differs from the
    card's and a template for its pack is installed to write from. A pack the
    catalog withheld or dropped is reported in ``skipped`` once, and neither
    its template nor its agents are listed; so is a pack the catalog offers
    that agents are behind on but that has no installed template.

    Each row's ``from_date`` is the date stored with it (``commit_date`` /
    ``pack_commit_date``), which is None for a row written before dates were
    recorded; this function does no I/O to fill it.

    Args:
        catalog: The listing at the target commit.
        templates: Every installed template.
        agents: Every agent.
        pinned_sha: The full SHA the catalog is pinned at now.
        pinned_date: That commit's committer date.

    Returns:
        The ``UpdatePlan`` for ``catalog.commit_sha``.
    """
    target = catalog.commit_sha
    target_date = catalog.committed_at
    cards = {card.entry.id: card for card in catalog.packs}
    withheld = {row.id: row for row in catalog.withheld}
    installed = {
        template.pack_id: template
        for template in templates
        if template.source == "catalog" and template.pack_id
    }
    skipped: dict[str, SkippedPack] = {}

    def _skip_reason(pack_id: str, title: str) -> SkippedPack | None:
        if pack_id in withheld:
            row = withheld[pack_id]
            return SkippedPack(pack_id, row.title, row.kind, row.code, row.message)
        if pack_id not in cards:
            return SkippedPack(
                pack_id,
                title,
                SKIPPED_REMOVED,
                "pack_removed",
                "The catalog no longer lists this pack.",
            )
        return None

    template_updates: list[TemplateUpdate] = []
    for pack_id, template in installed.items():
        reason = _skip_reason(pack_id, template.title)
        if reason is not None:
            skipped[pack_id] = reason
            continue
        if cards[pack_id].content_hash != template.content_hash:
            template_updates.append(TemplateUpdate(
                template_id=template.id,
                pack_id=pack_id,
                title=template.title,
                from_sha=template.commit_sha,
                to_sha=target,
                from_date=template.commit_date,
                to_date=target_date,
            ))

    agent_updates: list[AgentUpdate] = []
    for agent in agents:
        pack_id = agent.pack_id
        # URL-linked agents follow their own URL ref; a skipped pack's agents
        # are already reported through the pack.
        if not pack_id or pack_id in skipped:
            continue
        template = installed.get(pack_id)
        if template is None:
            card = cards.get(pack_id)
            # Behind a pack the catalog offers but nobody installed: there is
            # nothing to write from, which the operator is told. A pack that is
            # neither installed nor offered offers no update to skip.
            if card is not None and agent.pack_content_hash != card.content_hash:
                skipped[pack_id] = SkippedPack(
                    pack_id,
                    card.entry.title,
                    SKIPPED_NOT_INSTALLED,
                    "template_not_installed",
                    "Agents were hired from this pack, but it is not installed. "
                    "Install it to update them.",
                )
            continue
        # An installed template that was not skipped has a card.
        if agent.pack_content_hash == cards[pack_id].content_hash:
            continue
        agent_updates.append(AgentUpdate(
            agent_id=agent.id,
            name=agent.name,
            pack_id=pack_id,
            template_title=template.title,
            from_sha=agent.pack_commit_sha,
            to_sha=target,
            from_date=agent.pack_commit_date,
            to_date=target_date,
            edited=agent_is_edited(agent),
        ))

    return UpdatePlan(
        repo=catalog.repo,
        pinned_sha=pinned_sha,
        target_sha=target,
        pinned_date=pinned_date,
        target_date=target_date,
        templates=tuple(template_updates),
        agents=tuple(agent_updates),
        skipped=tuple(skipped.values()),
    )


def check_updates(*, source: PackSource, catalog_repo: str, pinned_ref: str) -> UpdatePlan:
    """Plan an update from the configured pin to the catalog's HEAD. Writes nothing.

    Every row comes back with both dates. A row whose stored date is NULL
    (written before dates were recorded) has its ``from_sha`` resolved through
    ``source.resolve_commit`` — one call per distinct SHA with
    ``GitHubPackSource``'s cache, and none for a row at the pin, which was
    just resolved — so the review never shows an unknown date.

    Args:
        source: Pack source the catalog is read through.
        catalog_repo: ``owner/repo`` of the catalog.
        pinned_ref: The configured pin (commit SHA, prefix or tag).

    Returns:
        The ``UpdatePlan`` whose ``target_sha`` is HEAD resolved now.

    Raises:
        AgentPackError: The pin or HEAD cannot be resolved, the catalog
            index cannot be read at HEAD, or an undated row's commit cannot
            be resolved — surfaced, never shown as a blank date.
    """
    validate_pin_ref(pinned_ref)
    owner, repo = parse_catalog_repo(catalog_repo)
    pinned = source.resolve_commit(owner, repo, pinned_ref)
    head = source.resolve_head(owner, repo)
    catalog = list_catalog(source=source, catalog_repo=catalog_repo, ref=head.sha)
    import db

    plan = plan_updates(
        catalog, db.list_agent_templates(), db.list_agents(), pinned.sha, pinned.committed_at,
    )

    def _dated(row: _Row) -> _Row:
        if row.from_date is not None or row.from_sha is None:
            return row
        return replace(row, from_date=source.resolve_commit(owner, repo, row.from_sha).committed_at)

    return replace(
        plan,
        templates=tuple(_dated(row) for row in plan.templates),
        agents=tuple(_dated(row) for row in plan.agents),
    )


def apply_updates(
    *,
    source: PackSource,
    catalog_repo: str,
    target_sha: str,
    include_agents: bool,
) -> UpdatePlan:
    """Move the catalog pin to ``target_sha`` and apply what that changes.

    Recomputes the plan against the reviewed ``target_sha`` — never a newer
    HEAD — so what is written is what the operator reviewed. The catalog is
    read first; then, in one transaction, every listed template is
    re-installed from the pack already fetched, the pin is set, and, when
    ``include_agents``, every listed agent is rewritten from its template.

    Args:
        source: Pack source the catalog is read through.
        catalog_repo: ``owner/repo`` of the catalog.
        target_sha: The 40-character commit the preview was computed for.
        include_agents: Whether linked agents are updated too.

    Returns:
        The applied plan. Its ``pinned_sha`` is the new pin (``target_sha``),
        and ``agents`` is empty when ``include_agents`` is false. Templates
        and agent links are written with ``target_sha``'s committer date. A
        row's ``from_date`` is left as stored (None for an undated row): no
        network read happens inside the transaction.

    Raises:
        AgentPackError: ``invalid_source`` for a ``target_sha`` that is not 40
            hex characters, or any fetch error from listing the catalog. Both
            happen before the transaction, so nothing is written.
        sqlite3.Error / RuntimeError: A write failed; the transaction rolls
            every write of this apply back.
    """
    sha = (target_sha or "").strip().lower()
    if not _FULL_SHA_RE.fullmatch(sha):
        raise AgentPackError(
            "Update target must be a full 40-character commit SHA.",
            code="invalid_source",
        )
    catalog = list_catalog(source=source, catalog_repo=catalog_repo, ref=sha)
    cards = {card.entry.id: card for card in catalog.packs}
    import db

    with db.transaction():
        # Planned inside the transaction so the rows it reads are the rows it
        # writes; the network reads are all done above.
        plan = plan_updates(
            catalog, db.list_agent_templates(), db.list_agents(), sha, catalog.committed_at,
        )
        for update in plan.templates:
            _upsert_catalog_template(cards[update.pack_id], catalog.commit_sha, catalog.committed_at)
        db.set_setting(CATALOG_PIN_SETTING, catalog.commit_sha, _CATALOG_PIN_CATEGORY)
        if include_agents:
            for update in plan.agents:
                agent = db.get_agent(update.agent_id)
                template = db.find_agent_template(pack_id=update.pack_id, source_url=None)
                if agent is None or template is None:
                    raise RuntimeError(
                        f"Agent {update.agent_id} or its template {update.pack_id} "
                        "vanished mid-update"
                    )
                apply_template_to_agent(agent, template)
    if not include_agents:
        plan = replace(plan, agents=())
    return plan


def apply_template_to_agent(agent: Agent, template: AgentTemplate) -> Agent:
    """Rewrite an agent's contract from a pack template and record the link.

    The one write path for an agent pack update, shared by Update all and the
    desk. Overwrites ``description``, ``done_fail_bar`` and ``communication``
    only — name, specialty, color, connection, thinking levels and desk are
    never touched — then stamps the template's commit, its date, file hash
    and the contract hash of what was written.

    Opens no transaction of its own: the two writes must land together, so
    the caller wraps this in ``db.transaction()`` (or is already inside one).

    Args:
        agent: The agent to update.
        template: An installed ``catalog`` or ``url`` template.

    Returns:
        The agent as stored after both writes.

    Raises:
        ValueError: ``template`` is a local template, which has no pack.
        RuntimeError: The agent disappeared during the write.
    """
    if template.source not in ("catalog", "url") or not template.commit_sha or not template.content_hash:
        raise ValueError(f"Template {template.id} is not a pinned pack and cannot update an agent")
    import db

    db.update_agent(
        agent.id,
        description=template.description,
        done_fail_bar=template.what_done_looks_like,
        communication=template.communication,
    )
    updated = db.set_agent_pack_link(
        agent.id,
        pack_id=template.pack_id,
        pack_source_url=template.source_url,
        commit_sha=template.commit_sha,
        commit_date=template.commit_date,
        content_hash=template.content_hash,
        contract_hash=template_contract_hash(template),
    )
    if updated is None:
        raise RuntimeError(f"Agent {agent.id} disappeared during its pack update")
    return updated


def template_contract_hash(template: AgentTemplate) -> str:
    """``contract_hash`` of the contract a template writes onto an agent."""
    return contract_hash(template.description, template.what_done_looks_like, template.communication)


def agent_is_edited(agent: Agent) -> bool:
    """Whether a linked agent's contract differs from the one its pack last wrote."""
    return contract_hash(agent.description, agent.done_fail_bar, agent.communication) != agent.pack_contract_hash


def _upsert_catalog_template(
    card: CatalogListPack, commit_sha: str, commit_date: datetime,
) -> AgentTemplate:
    """Re-install one catalog template from a card already fetched.

    The same field mapping ``POST /api/agent-templates`` uses for a catalog
    install, so a template updated here is indistinguishable from one
    re-installed by hand at the same commit.
    """
    import db

    pack = card.pack
    author = pack.pack_author
    return db.upsert_agent_template(
        source="catalog",
        pack_id=card.entry.id,
        source_url=None,
        category=card.entry.category,
        title=card.entry.title,
        specialty=pack.specialty,
        description=pack.description,
        what_done_looks_like=pack.what_done_looks_like,
        tools_hint=list(pack.tools_hint),
        communication=pack.communication.as_dict(),
        author_name=author.name if author else None,
        author_url=author.url if author else None,
        commit_sha=commit_sha,
        commit_date=commit_date,
        content_hash=card.content_hash,
    )
