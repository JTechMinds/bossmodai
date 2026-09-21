"""Agent-scoped standing prefs.

Durable file: ``/me/standing_prefs.json`` (``schema_version`` 1) inside the
agent artifact directory. Work-turn assembly reads this file only. ``/me/notes``
stays cold how-to and is never opened here.

A write adds a new id or replaces that same id. Other ids stay. The store is
not compacted and prefs are not dropped to make room.

Caps (the warm section is display-only; the file keeps every accepted sticky):

- sticky text: 160 characters
- sources: 1 to 4, each at most 80 characters
- store text: 4096 bytes total across sticky text
- warm line: 140 characters
- warm section: 480 characters
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from core.bm_cli import filesystem

logger = logging.getLogger(__name__)

STANDING_PREFS_FILENAME = "standing_prefs.json"
STANDING_PREFS_PATH = "/me/standing_prefs.json"
SCHEMA_VERSION = 1

TEXT_MAX_CHARS = 160
SOURCE_MAX_CHARS = 80
SOURCES_MAX = 4
STORE_TEXT_BYTE_CAP = 4096
WARM_LINE_CHARS = 140
WARM_SECTION_CHAR_CAP = 480

PrefKind = Literal["preference", "constraint", "style", "tool_bias"]
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StandingPref(BaseModel):
    """One typed sticky. ``sources`` are ids or paths, not note bodies."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: PrefKind
    text: str
    sources: list[str]

    @field_validator("id")
    @classmethod
    def _id_token(cls, value: str) -> str:
        token = value.strip()
        if _ID_RE.fullmatch(token) is None:
            raise ValueError("pref id must be a short token")
        return token

    @field_validator("text")
    @classmethod
    def _short_text(cls, value: str) -> str:
        text = value.strip()
        if not text or "\n" in text or "\r" in text or len(text) > TEXT_MAX_CHARS:
            raise ValueError(f"pref text must be one line up to {TEXT_MAX_CHARS} characters")
        return text

    @field_validator("sources")
    @classmethod
    def _source_pointers(cls, value: list[str]) -> list[str]:
        if not value or len(value) > SOURCES_MAX:
            raise ValueError(f"pref sources must list 1 to {SOURCES_MAX} pointers")
        cleaned: list[str] = []
        for item in value:
            token = str(item).strip()
            if not token or "\n" in token or "\r" in token or len(token) > SOURCE_MAX_CHARS:
                raise ValueError(f"each source must be one pointer up to {SOURCE_MAX_CHARS} characters")
            cleaned.append(token)
        return cleaned


class StandingPrefsDocument(BaseModel):
    """On-disk document. Unknown keys and kinds are rejected."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    prefs: list[StandingPref]

    @field_validator("prefs")
    @classmethod
    def _unique_ids(cls, value: list[StandingPref]) -> list[StandingPref]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("pref ids must be unique")
        return value


def is_standing_prefs_path(virtual_path: str) -> bool:
    """True when a virtual CLI path is the standing-prefs file."""
    from core.bm_cli.virtual_fs import normalize_cli_path

    return normalize_cli_path("/", virtual_path) == STANDING_PREFS_PATH


def standing_prefs_file(storage_key: str) -> Path:
    """Return the prefs file path without creating the agent directory."""
    key = (storage_key or "").strip()
    if not key or key in {".", ".."} or "/" in key or "\\" in key:
        raise ValueError("standing prefs require an agent storage key")
    # Read path only. Do not create the agent directory just to look.
    return filesystem._AGENTS_ROOT / key / STANDING_PREFS_FILENAME


def parse_standing_prefs_document(raw: str) -> StandingPrefsDocument:
    """Parse one prefs document. Invalid JSON or schema raises ValueError."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("standing prefs must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("standing prefs must be a JSON object")
    try:
        return StandingPrefsDocument.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"standing prefs schema rejected: {_brief_validation(exc)}") from exc


def read_standing_prefs(storage_key: str) -> list[StandingPref]:
    """Load prefs for inject. Missing is empty. Unreadable is empty and unread further.

    Does not open ``/me/notes`` or any other file.
    """
    try:
        path = standing_prefs_file(storage_key)
    except ValueError:
        logger.warning("standing prefs skipped: missing storage key")
        return []
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        document = parse_standing_prefs_document(raw)
    except (OSError, UnicodeError, ValueError):
        logger.warning("standing prefs unreadable; warm inject skipped")
        return []
    return list(document.prefs)


def prepare_standing_prefs_write(storage_key: str, content: str) -> str:
    """Validate a write and merge it onto the current store.

    Incoming ids replace that sticky. Ids absent from the write stay.
    A cap miss or a corrupt current file raises and leaves the file untouched.
    """
    incoming = parse_standing_prefs_document(content)
    existing = _read_existing_for_write(storage_key)
    merged = _merge_prefs(existing, incoming.prefs)
    _enforce_store_cap(merged)
    return _dump(StandingPrefsDocument(schema_version=SCHEMA_VERSION, prefs=merged))


def render_warm_section(prefs: list[StandingPref]) -> str | None:
    """Render the warm section, or None when there is nothing to inject."""
    if not prefs:
        return None
    header = f"# Standing prefs ({STANDING_PREFS_PATH})"
    lines = [_sticky_line(pref) for pref in prefs]
    kept: list[str] = []
    for line in lines:
        if len(_compose(header, kept + [line], more=None)) <= WARM_SECTION_CHAR_CAP:
            kept.append(line)
            continue
        break
    omitted = len(lines) - len(kept)
    if omitted == 0:
        return _compose(header, kept, more=None)
    more = _more_line(omitted)
    while kept and len(_compose(header, kept, more=more)) > WARM_SECTION_CHAR_CAP:
        kept.pop()
        omitted = len(lines) - len(kept)
        more = _more_line(omitted)
    rendered = _compose(header, kept, more=more)
    if len(rendered) <= WARM_SECTION_CHAR_CAP:
        return rendered
    return rendered[:WARM_SECTION_CHAR_CAP].rstrip()


def _read_existing_for_write(storage_key: str) -> list[StandingPref]:
    path = standing_prefs_file(storage_key)
    if not path.exists():
        return []
    if not path.is_file():
        raise ValueError("standing prefs path is not a file; refusing to overwrite")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("standing prefs store is unreadable; refusing to overwrite") from exc
    try:
        document = parse_standing_prefs_document(raw)
    except ValueError as exc:
        raise ValueError("standing prefs store is unreadable; refusing to overwrite") from exc
    return list(document.prefs)


def _merge_prefs(existing: list[StandingPref], incoming: list[StandingPref]) -> list[StandingPref]:
    by_id = {item.id: item for item in existing}
    order = [item.id for item in existing]
    for item in incoming:
        if item.id not in by_id:
            order.append(item.id)
        by_id[item.id] = item
    return [by_id[item_id] for item_id in order]


def _enforce_store_cap(prefs: list[StandingPref]) -> None:
    total = sum(len(item.text.encode("utf-8")) for item in prefs)
    if total > STORE_TEXT_BYTE_CAP:
        raise ValueError(
            f"standing prefs store is {total} bytes; cap is {STORE_TEXT_BYTE_CAP}. "
            "Replace a pref instead of adding past the cap."
        )


def _dump(document: StandingPrefsDocument) -> str:
    payload = document.model_dump()
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _sticky_line(pref: StandingPref) -> str:
    prefix = f"- {pref.kind} {pref.id} — "
    suffix = f" sources: {', '.join(pref.sources)}"
    line = f"{prefix}{pref.text}{suffix}"
    if len(line) <= WARM_LINE_CHARS:
        return line
    budget = WARM_LINE_CHARS - len(prefix) - len(suffix)
    if budget >= 4:
        clipped = pref.text if len(pref.text) <= budget else pref.text[: budget - 3].rstrip() + "..."
        return f"{prefix}{clipped}{suffix}"
    short = f"{prefix}{pref.text}"
    if len(short) <= WARM_LINE_CHARS:
        return short
    if WARM_LINE_CHARS <= 3:
        return prefix[:WARM_LINE_CHARS]
    return short[: WARM_LINE_CHARS - 3].rstrip() + "..."


def _more_line(omitted: int) -> str:
    return f"more: {STANDING_PREFS_PATH} ({omitted})"


def _compose(header: str, lines: list[str], *, more: str | None) -> str:
    parts = [header, *lines]
    if more:
        parts.append(more)
    return "\n".join(parts)


def _brief_validation(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid document"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ())) or "document"
    message = str(first.get("msg") or "invalid")
    return f"{loc}: {message}"
