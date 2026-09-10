"""Import and export orchestration for agent packs.

Import hydrates hire-form fields only. It never creates or patches an
agent. Catalog imports read catalog.yaml at a pinned ref first, then the
pack file. Export reads an existing agent's profile fields back into a pack.
``describe_pack`` splits stored hire text back into its labeled sections
for a read view; it parses, and stores nothing. ``list_catalog`` runs the same
parse-and-quality gate import runs, so the browse list offers only packs that
would actually install.
"""

from __future__ import annotations

import hashlib
import logging
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
    PACK_KIND_AGENT,
    AgentPack,
    AgentPackError,
    export_agent_pack,
    pack_author_from_company,
    parse_pack_yaml,
)
from core.agent_pack.sections import (
    DESCRIPTION_SECTION_KEYS,
    DONE_SECTION_KEYS,
    extract_labeled_sections,
)
from core.models.agent import Agent
from core.models.settings import AIPersonality

logger = logging.getLogger(__name__)

# The two facts a withheld row can carry, and they are NOT the same fact.
# REFUSED means the pack file was read and its CONTENT was rejected — the
# maintainer has to change the pack. UNAVAILABLE means it was never read at
# all, so nothing whatever is known about its content and the operator's move
# is to try again. Collapsing them tells a maintainer their pack is broken
# because GitHub returned a 502, which is a lie that costs them a debugging
# session.
WITHHELD_REFUSED = "refused"
WITHHELD_UNAVAILABLE = "unavailable"


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


@dataclass(frozen=True)
class CatalogListPack:
    """One browse card, built from a pack that passed the install gate.

    Specialty, description, done bar, tools hint and content hash are all read
    from the one parse ``list_catalog`` already ran, and none of them is
    optional: a row whose pack does not parse — or parses but fails
    ``validate_pack_quality`` — never becomes a card at all, it comes back as a
    ``WithheldPack``. So no field here is ``None`` standing in for "never
    read", and no consumer has to branch on that case.

    ``pack_author`` stays optional because a schema- and quality-valid pack may
    legitimately name no author. ``tools_hint`` is an empty tuple for a pack
    that lists no tools; it can no longer mean "was never read".
    """

    entry: CatalogEntry
    specialty: str
    description: str
    what_done_looks_like: str
    tools_hint: tuple[str, ...]
    content_hash: str
    pack_author: dict[str, str] | None = None


@dataclass(frozen=True)
class WithheldPack:
    """One catalog row the browse list will not offer, and which fact that is.

    Two different things keep a row out of the grid and they must not be told
    as one. A pack that does not parse, or that parses but fails
    ``validate_pack_quality``, was READ and REFUSED: install runs the same
    gate, so listing it would paint a card whose only outcome is an error, and
    the maintainer has to change the pack. A pack whose file could not be
    fetched at all is UNAVAILABLE: nothing is known about its content, the
    cause is as likely a GitHub 502 as anything in the repo, and the operator's
    move is to retry. Both leave the grid — you cannot install what you cannot
    read — and both are reported here, wearing their own ``kind``.

    Reported rather than merely dropped on purpose: the operator owns the
    catalog repo, and a pack that silently vanished from their own catalog is a
    pack that stays broken. Dropping the card is right; dropping the fact is
    the silent fallback this codebase bans.

    Attributes:
        id: The catalog row's stable pack id.
        path: That row's ``packs/<category>/<id>.agent.yaml`` path in the repo,
            so the maintainer is pointed at the file, not just the id.
        category: That row's category slug. ``CatalogListResult.withheld`` is
            flat while ``packs`` is grouped, so without this a per-category
            count of what went missing cannot be derived at all.
        title: The row's display title, named the way the catalog names it.
        kind: ``WITHHELD_REFUSED`` or ``WITHHELD_UNAVAILABLE``. Decided by
            WHICH CALL failed, never by reading ``code`` back: ``fetch_file``
            failing means the bytes never arrived, ``_parse_imported_pack``
            failing means they arrived and were rejected. A classifier over
            codes would have to be updated every time a new code is raised,
            and would be silently wrong until someone noticed.
        code: The ``AgentPackError`` code raised. Content codes are
            ``invalid_yaml``, ``invalid_schema``, ``missing_field``,
            ``unsupported_kind``, ``dangerous_key``, ``pack_too_large`` and
            ``pack_quality``; the transport code is ``fetch_failed``. Stable
            enough for a caller to branch on, but ``kind`` is the fact.
        message: That error's message, verbatim and unedited.
    """

    id: str
    path: str
    category: str
    title: str
    kind: str
    code: str
    message: str


@dataclass(frozen=True)
class CatalogListResult:
    """Pinned catalog index for the Add agent browse door.

    ``packs`` is the offered set and nothing else, so categories, counts, cards
    and every later consumer are derived from an already-filtered list by
    construction — none of them has to remember to filter. ``withheld`` is
    every agent row that did not become a card, each carrying the reason and
    the ``kind`` of reason.

    ONE list carrying an explicit ``kind``, not one list per kind. The headline
    an operator reads is "how many rows are missing from this grid", which is
    ``len(withheld)`` here and a sum of two lengths in the two-list shape; a
    lookup by pack id — "is the pack behind this installed template one of
    them, and which kind?" — is one scan here and two there; catalog order
    survives across both kinds in one pass; and a third kind later adds a
    ``kind`` value rather than a third top-level key every consumer must learn.
    The cost is that a caller wanting only refusals filters, which is the one
    line the two-list shape would have saved.
    """

    repo: str
    requested_ref: str
    commit_sha: str
    packs: tuple[CatalogListPack, ...]
    withheld: tuple[WithheldPack, ...]


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


def list_catalog(
    *,
    source: PackSource,
    catalog_repo: str,
    ref: str,
) -> CatalogListResult:
    """Read catalog.yaml at a pinned ref and return the offered browse cards.

    Fetches each listed agent pack once — one index fetch plus one fetch per
    agent row — then runs the gate install runs: schema parse plus
    ``validate_pack_quality``. A row that fails either is withheld rather than
    listed, and comes back in ``CatalogListResult.withheld`` with the error's
    code and message; only a row that would actually install becomes a card.

    The fetch and the gate are caught SEPARATELY, because they are separate
    facts. A row whose file could not be read is ``WITHHELD_UNAVAILABLE`` —
    nothing is known about its content, and the operator retries. A row that
    was read and rejected is ``WITHHELD_REFUSED`` — the maintainer fixes the
    pack. Telling a maintainer their pack is broken because raw.githubusercontent
    returned a 502 is a false accusation, and one try block around both calls
    is the only thing that could produce it.

    The gate is here, at the source, and not in each consumer: categories,
    counts and cards are all built from ``packs``, so a filter every consumer
    had to remember would eventually be forgotten by one of them, and browse
    would go back to offering packs install refuses.

    Every card field — ``pack_author``, specialty, description, the done bar,
    the tools hint and the content hash — still comes out of that one parse.
    ``validate_pack_quality`` is a pure function over the parsed pack, so
    validating costs no further fetch. Does not create or patch an agent.

    Args:
        source: Pack source the index and each pack file are read through.
        catalog_repo: ``owner/repo`` of the catalog to browse.
        ref: A pinned commit SHA or tag; floating refs are rejected.

    Returns:
        A ``CatalogListResult`` whose ``packs`` are installable and whose
        ``withheld`` rows are not, each carrying its ``kind``, its code and
        the reason verbatim.

    Failure modes:
        Raises ``AgentPackError`` when the ref is floating or unresolvable, or
        when the index itself cannot be fetched or parsed — the browse list is
        unanswerable then. One bad pack never raises: it is withheld, logged at
        warning level, and the rest of the catalog is still served.
    """
    validate_pin_ref(ref)
    owner, repo = parse_catalog_repo(catalog_repo)
    sha = source.resolve_commit_sha(owner, repo, ref)
    index_text = source.fetch_file(owner, repo, CATALOG_INDEX_PATH, sha)
    index = parse_catalog_yaml(index_text, allow_empty=True)
    cards: list[CatalogListPack] = []
    withheld: list[WithheldPack] = []
    for entry in index.entries:
        if entry.kind != PACK_KIND_AGENT:
            continue
        try:
            raw = source.fetch_file(owner, repo, entry.path, sha)
        except AgentPackError as exc:
            # Nothing was read, so nothing is claimed about the content.
            withheld.append(_withhold(entry, exc, WITHHELD_UNAVAILABLE, sha))
            continue
        try:
            pack = _parse_imported_pack(raw)
        except AgentPackError as exc:
            # The bytes arrived and this app rejected them: a fact about the
            # pack, and one the maintainer has to act on.
            withheld.append(_withhold(entry, exc, WITHHELD_REFUSED, sha))
            continue
        cards.append(
            CatalogListPack(
                entry=entry,
                specialty=pack.specialty,
                description=pack.description,
                what_done_looks_like=pack.what_done_looks_like,
                tools_hint=pack.tools_hint,
                content_hash=pack_content_hash(pack),
                pack_author=pack.pack_author.as_dict() if pack.pack_author else None,
            )
        )
    return CatalogListResult(
        repo=f"{owner}/{repo}",
        requested_ref=ref,
        commit_sha=sha,
        packs=tuple(cards),
        withheld=tuple(withheld),
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


def pack_content_hash(pack: AgentPack) -> str:
    """Return the sha256 hex digest of a pack's canonical YAML.

    ``AgentPack.to_yaml()`` is a canonical ``safe_dump`` with a fixed key
    order and no tags, so the digest is stable across fetches and is the
    identity an installed template is compared against to decide whether it is
    stale. Commit SHAs cannot answer that: the catalog pin is repo-wide, so
    comparing SHAs would mark every installed template stale on any pin bump,
    including packs whose file never changed.

    Takes a parsed pack; raises nothing of its own. A pack that could not be
    parsed has no canonical form and therefore no hash.
    """
    return hashlib.sha256(pack.to_yaml().encode("utf-8")).hexdigest()


def describe_pack(description: str, what_done_looks_like: str) -> dict[str, Any]:
    """Parsed description / done sections for a browse or detail view.

    ``validate_pack_quality`` requires every imported pack to carry Mission,
    In scope, Out of scope and Handoff inside ``description`` and a Fail
    examples section inside ``what_done_looks_like``, so a reader has structure
    to render. This splits that structure out server-side with the same parser
    the quality gate reads, so no consumer re-splits the strings itself. The
    two hire strings stay the source of truth: this is a view of them, it is
    never stored, and nothing round-trips through it.

    Args:
        description: A pack's or template's ``description`` hire string.
        what_done_looks_like: The matching ``what_done_looks_like`` hire string.

    Returns:
        ``{"description": {...}, "done": {...}}``. Both halves carry
        ``preamble`` — the unlabeled lead-in, ``""`` when the text starts on a
        heading — plus one key per section: ``mission``, ``in_scope``,
        ``out_of_scope`` and ``handoff`` under ``description``, and
        ``fail_examples`` under ``done``. Keys are always present; a section
        the text does not carry is ``None``. Bodies are returned stripped and
        verbatim, headings removed.

    Failure modes:
        Raises nothing of its own and never invents a section. Text carrying no
        recognised heading — a thin pack that never went through quality, or
        one whose labels are spelled in a way ``canonical_section_label`` does
        not know — comes back as all-``None`` sections with the whole text as
        ``preamble``; the caller decides what that means. A non-string argument
        raises ``AttributeError`` from the parser rather than being coerced.
    """
    desc_preamble, desc_sections = extract_labeled_sections(description)
    done_preamble, done_sections = extract_labeled_sections(what_done_looks_like)
    parsed_description: dict[str, Any] = {"preamble": desc_preamble}
    for key in DESCRIPTION_SECTION_KEYS:
        parsed_description[key] = desc_sections.get(key)
    parsed_done: dict[str, Any] = {"preamble": done_preamble}
    for key in DONE_SECTION_KEYS:
        parsed_done[key] = done_sections.get(key)
    return {"description": parsed_description, "done": parsed_done}


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


def _withhold(
    entry: CatalogEntry,
    exc: AgentPackError,
    kind: str,
    sha: str,
) -> WithheldPack:
    """Log one refused-or-unavailable catalog row and build its report.

    The catalog is the operator's own repo, so a row it ships that this app
    will not offer is a fact they need twice over: in the server log next to
    the pin it happened at, and in the response the browse door renders. Never
    swallowed, and never softened — ``message`` is the error's own text.

    Args:
        entry: The catalog index row that did not become a card.
        exc: The error the fetch or the gate raised.
        kind: ``WITHHELD_UNAVAILABLE`` when the fetch failed,
            ``WITHHELD_REFUSED`` when the parse-and-quality gate did. Passed in
            by the call site that caught it rather than derived from
            ``exc.code``, so a new code is classified correctly on the day it
            is introduced instead of on the day someone updates a lookup table.
        sha: The commit the catalog was read at, for the log line.

    Returns:
        The ``WithheldPack`` to append to the result.
    """
    logger.warning(
        "Catalog pack %s (%s) %s at %s: %s [%s]",
        entry.id,
        entry.path,
        kind,
        sha,
        exc,
        exc.code,
    )
    return WithheldPack(
        id=entry.id,
        path=entry.path,
        category=entry.category,
        title=entry.title,
        kind=kind,
        code=exc.code,
        message=str(exc),
    )


def _parse_imported_pack(raw: str | bytes) -> AgentPack:
    """Schema-parse a pack and require senior quality sections.

    The single definition of "installable", shared by both doors: browse
    withholds a pack this refuses and install rejects it. Split into two
    definitions they would drift, and the browse list would go back to offering
    cards that error on Install.
    """
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
