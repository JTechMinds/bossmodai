"""BossMod AI — Installed agent template model.

A template is a locally-installed, pinned snapshot of an agent pack. It is the
only thing that pre-fills the create-agent form, so the fields are stored flat
rather than as raw YAML: the picker filters on title, specialty and description
per keystroke, and re-parsing YAML for that would buy nothing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from core.agent_pack.service import describe_pack


class AgentTemplate(BaseModel):
    """One installed agent template: a pinned snapshot of a pack.

    ``pack_id`` is set for catalog installs and ``source_url`` for URL
    installs; exactly one of the two is the row's natural key. ``content_hash``
    is the digest of the pack's canonical YAML at ``commit_sha``, so staleness
    is answerable without refetching and without comparing the repo-wide
    catalog pin (which moves for packs that never changed).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: Literal["catalog", "url"]
    pack_id: str | None = None
    source_url: str | None = None
    category: str
    title: str
    specialty: str
    description: str
    what_done_looks_like: str
    personality_hint: str | None = None
    tools_hint: list[str] = Field(default_factory=list)
    author_name: str | None = None
    author_url: str | None = None
    commit_sha: str
    content_hash: str
    installed_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def sections(self) -> dict[str, Any]:
        """The stored hire strings split into their labeled pack sections.

        Derived on read, never stored: ``description`` and
        ``what_done_looks_like`` remain the row's source of truth and are what
        fills the create-agent form. Every consumer of the model — the list
        route, and anything that serializes a row later — gets the same split
        without re-parsing the strings itself.

        Shape and failure modes are ``describe_pack``'s: keys are always
        present, a section the text does not carry is ``None``, and a row whose
        text carries no recognised heading comes back as all-``None`` sections
        rather than an invented one.
        """
        return describe_pack(self.description, self.what_done_looks_like)

    @field_validator("tools_hint", mode="before")
    @classmethod
    def _parse_tools_hint(cls, value: Any) -> Any:
        """Decode the ``tools_hint`` JSON ``TEXT`` column into a list of names.

        Accepts an already-decoded list unchanged so the model can be built in
        Python as well as from a row.

        Raises ``ValueError`` — surfaced by Pydantic as a ``ValidationError`` —
        when the column is NULL, is not valid JSON, or does not decode to a
        list of strings. A corrupt row is reported rather than smoothed into an
        empty list, which would silently drop a pack's tool hints.
        """
        if isinstance(value, list):
            return value
        if value is None:
            raise ValueError(
                "tools_hint is NULL; the column is NOT NULL DEFAULT '[]'"
            )
        if not isinstance(value, str):
            raise ValueError(
                f"tools_hint must be a JSON array string, got {type(value).__name__}"
            )
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"tools_hint is not valid JSON: {exc}") from exc
        if not isinstance(decoded, list):
            raise ValueError("tools_hint must decode to a JSON array")
        if not all(isinstance(item, str) for item in decoded):
            raise ValueError("tools_hint must decode to a JSON array of strings")
        return decoded
