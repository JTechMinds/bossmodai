"""Catalog index for agent packs (git + PRs, not a storefront).

Default repo: ``JTechMinds/BossMod_AgentMP``. Layout::

    catalog.yaml
    packs/
      engineering/
        code-auditor.agent.yaml
      product/
        feature-planner.agent.yaml

Folder is a category slug. File stem is the stable pack id
(``<id>.agent.yaml``). The app reads ``catalog.yaml`` first (id, kind,
path, category, title) at a pinned commit or tag, then fetches that path.
There is no ``Profiles/`` wrapper. Category in the index must match the
folder (catalog CI should enforce the same).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from core.agent_pack.schema import (
    MAX_PACK_BYTES,
    PACK_KIND_AGENT,
    RESERVED_PACK_KINDS,
    AgentPackError,
)

CATALOG_INDEX_PATH = "catalog.yaml"
CATALOG_PACKS_ROOT = "packs"
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_TITLE_MAX_LEN = 80
_AGENT_PACK_PATH_RE = re.compile(
    r"^packs/(?P<category>[a-z][a-z0-9-]{0,62})/"
    r"(?P<pack_id>[a-z][a-z0-9-]{0,62})\.agent\.yaml$"
)


@dataclass(frozen=True)
class CatalogEntry:
    """One row from catalog.yaml. Not a hire; import still hydrates pack fields."""

    id: str
    kind: str
    path: str
    category: str
    title: str


@dataclass(frozen=True)
class CatalogIndex:
    """Parsed catalog.yaml. Lookup is by pack id or path."""

    entries: tuple[CatalogEntry, ...]

    def by_id(self) -> dict[str, CatalogEntry]:
        return {entry.id: entry for entry in self.entries}

    def by_path(self) -> dict[str, CatalogEntry]:
        return {entry.path: entry for entry in self.entries}


def parse_catalog_yaml(raw: str | bytes) -> CatalogIndex:
    """Parse catalog.yaml. Data-only; does not install packs."""
    text = _utf8_text(raw)
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AgentPackError("Catalog YAML is not valid data.", code="invalid_catalog") from exc
    if not isinstance(loaded, dict):
        raise AgentPackError(
            "catalog.yaml must be a mapping with a packs list.",
            code="invalid_catalog",
        )
    packs = loaded.get("packs")
    if not isinstance(packs, list) or not packs:
        raise AgentPackError(
            "catalog.yaml must list packs (id, kind, path, category, title).",
            code="invalid_catalog",
        )
    entries: list[CatalogEntry] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in packs:
        entry = _parse_entry(item)
        if entry.id in seen_ids:
            raise AgentPackError(
                f"Catalog pack id {entry.id!r} is duplicated.",
                code="invalid_catalog",
            )
        if entry.path in seen_paths:
            raise AgentPackError(
                f"Catalog pack path {entry.path!r} is duplicated.",
                code="invalid_catalog",
            )
        seen_ids.add(entry.id)
        seen_paths.add(entry.path)
        entries.append(entry)
    return CatalogIndex(entries=tuple(entries))


def resolve_catalog_entry(
    index: CatalogIndex,
    *,
    pack_id: str | None = None,
    path: str | None = None,
) -> CatalogEntry:
    """Resolve one catalog row by stable id and/or path."""
    wanted_id = (pack_id or "").strip() or None
    wanted_path = _normalize_index_path(path) if path else None
    if wanted_id is None and wanted_path is None:
        raise AgentPackError(
            "Catalog import requires a pack id or path listed in catalog.yaml.",
            code="invalid_source",
        )
    by_id = index.by_id()
    by_path = index.by_path()
    entry: CatalogEntry | None = None
    if wanted_id is not None:
        entry = by_id.get(wanted_id)
        if entry is None:
            raise AgentPackError(
                f"Pack id {wanted_id!r} is not in catalog.yaml at this pin.",
                code="catalog_miss",
            )
    if wanted_path is not None:
        path_entry = by_path.get(wanted_path)
        if path_entry is None:
            raise AgentPackError(
                f"Pack path {wanted_path!r} is not in catalog.yaml at this pin.",
                code="catalog_miss",
            )
        if entry is not None and path_entry.id != entry.id:
            raise AgentPackError(
                "Catalog pack id and path do not refer to the same entry.",
                code="invalid_source",
            )
        entry = path_entry
    assert entry is not None
    if entry.kind != PACK_KIND_AGENT:
        raise AgentPackError(
            f"Pack kind {entry.kind!r} is reserved. v1 imports agent packs only.",
            code="unsupported_kind",
        )
    return entry


def validate_catalog_pack_path(path: str) -> tuple[str, str, str]:
    """Return (path, category, pack_id) for ``packs/<category>/<id>.agent.yaml``."""
    cleaned = _normalize_index_path(path)
    if "profiles" in {part.lower() for part in cleaned.split("/")}:
        raise AgentPackError(
            "Catalog paths must not use a Profiles/ wrapper.",
            code="invalid_catalog",
        )
    match = _AGENT_PACK_PATH_RE.fullmatch(cleaned)
    if match is None:
        raise AgentPackError(
            "Catalog pack path must be packs/<category-slug>/<id>.agent.yaml.",
            code="invalid_catalog",
        )
    return cleaned, match.group("category"), match.group("pack_id")


def _parse_entry(item: Any) -> CatalogEntry:
    if not isinstance(item, dict):
        raise AgentPackError(
            "Each catalog.yaml pack must be a mapping.",
            code="invalid_catalog",
        )
    pack_id = _required_slug(item.get("id"), field_name="id")
    kind = _required_kind(item.get("kind"))
    title = _required_title(item.get("title"))
    category = _required_slug(item.get("category"), field_name="category")
    path, folder, file_id = validate_catalog_pack_path(str(item.get("path") or ""))
    if kind == PACK_KIND_AGENT:
        if folder != category:
            raise AgentPackError(
                f"Catalog category {category!r} must match folder {folder!r}.",
                code="catalog_category_mismatch",
            )
        if file_id != pack_id:
            raise AgentPackError(
                f"Catalog pack id {pack_id!r} must match file {file_id!r}.",
                code="invalid_catalog",
            )
    return CatalogEntry(id=pack_id, kind=kind, path=path, category=category, title=title)


def _required_kind(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentPackError("Catalog pack kind is required.", code="invalid_catalog")
    kind = value.strip().lower()
    if kind == PACK_KIND_AGENT:
        return PACK_KIND_AGENT
    if kind in RESERVED_PACK_KINDS:
        return kind
    raise AgentPackError(
        f"Catalog pack kind must be {PACK_KIND_AGENT!r}.",
        code="invalid_catalog",
    )


def _required_slug(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentPackError(
            f"Catalog pack {field_name} is required.",
            code="invalid_catalog",
        )
    slug = value.strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise AgentPackError(
            f"Catalog {field_name} must be a lowercase slug, not a display name.",
            code="invalid_catalog",
        )
    return slug


def _required_title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentPackError("Catalog pack title is required.", code="invalid_catalog")
    title = " ".join(value.split())
    if len(title) > _TITLE_MAX_LEN:
        raise AgentPackError("Catalog pack title is too long.", code="invalid_catalog")
    return title


def _normalize_index_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise AgentPackError("Catalog pack path is invalid.", code="invalid_catalog")
    return "/".join(parts)


def _utf8_text(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        if len(raw) > MAX_PACK_BYTES:
            raise AgentPackError("Catalog exceeds the 64KiB size limit.", code="pack_too_large")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentPackError("Catalog must be UTF-8 YAML.", code="invalid_catalog") from exc
    if len(raw.encode("utf-8")) > MAX_PACK_BYTES:
        raise AgentPackError("Catalog exceeds the 64KiB size limit.", code="pack_too_large")
    return raw
