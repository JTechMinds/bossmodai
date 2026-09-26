"""Agent-scoped standing prefs: a system-owned store.

Each agent's prefs live in ``<artifacts>/system/standing_prefs/<storage_key>.json``
(``schema_version`` 1), outside every agent path: no virtual mount and no
shell path-jail root reaches it. Only this module reads or writes the file.
Agents change it only through the ``pref`` CLI command, which calls the typed
operations here (``set_standing_pref``, ``remove_standing_pref``,
``list_standing_prefs``); work-turn assembly reads it with
``read_standing_prefs``. ``/me/notes`` stays cold how-to and is never opened here.

A set adds a new id or replaces that same id. Other ids stay. The store is
not compacted and prefs are not dropped to make room. Writes are atomic.

Caps (the warm section is display-only; the store keeps every accepted pref):

- pref text: one line, 160 characters
- sources: 1 to 4, each at most 80 characters
- store text: 4096 bytes total across pref text
- warm line: 140 characters
- warm section: 480 characters

``migrate_workspace_standing_prefs`` moves a valid legacy agent-written
``/me/standing_prefs.json`` into the store once, at startup.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from core.bm_cli import filesystem

logger = logging.getLogger(__name__)

# The legacy agent-written file name inside ``/me``. Only the startup
# migration looks for it; nothing reads it as a store.
STANDING_PREFS_FILENAME = "standing_prefs.json"
SCHEMA_VERSION = 1

ID_MAX_CHARS = 64
TEXT_MAX_CHARS = 160
SOURCE_MAX_CHARS = 80
SOURCES_MAX = 4
STORE_TEXT_BYTE_CAP = 4096
WARM_LINE_CHARS = 140
WARM_SECTION_CHAR_CAP = 480

PrefKind = Literal["preference", "constraint", "style", "tool_bias"]
PREF_KINDS: tuple[str, ...] = get_args(PrefKind)
_ID_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{ID_MAX_CHARS - 1}}}$")


class StandingPref(BaseModel):
    """One typed pref. ``sources`` are ids or paths, not note bodies.

    Each validator raises one sentence that names the field and its limit;
    the ``pref`` command shows that sentence to the agent unchanged.
    """

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
            raise ValueError(
                f'pref id "{token}" must be a short token: 1 to {ID_MAX_CHARS} letters, digits, '
                '".", "_" or "-", starting with a letter or digit'
            )
        return token

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, value: Any) -> Any:
        # Before the Literal check, so the agent reads the allowed list, not pydantic's wording.
        if value not in PREF_KINDS:
            raise ValueError(f'kind "{value}" is not one of: {", ".join(PREF_KINDS)}')
        return value

    @field_validator("text")
    @classmethod
    def _short_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"pref text is empty; give the rule as one line up to {TEXT_MAX_CHARS} characters")
        if "\n" in text or "\r" in text:
            raise ValueError(f"pref text has a line break; the limit is one line up to {TEXT_MAX_CHARS} characters")
        if len(text) > TEXT_MAX_CHARS:
            raise ValueError(f"pref text is {len(text)} characters; the limit is {TEXT_MAX_CHARS} on one line")
        return text

    @field_validator("sources")
    @classmethod
    def _source_pointers(cls, value: list[str]) -> list[str]:
        if not value or len(value) > SOURCES_MAX:
            raise ValueError(f"pref needs 1 to {SOURCES_MAX} sources; got {len(value)}")
        cleaned: list[str] = []
        for index, item in enumerate(value, start=1):
            token = str(item).strip()
            if not token:
                raise ValueError(f"source {index} is empty; each source is one pointer up to {SOURCE_MAX_CHARS} characters")
            if "\n" in token or "\r" in token:
                raise ValueError(f"source {index} has a line break; each source is one line up to {SOURCE_MAX_CHARS} characters")
            if len(token) > SOURCE_MAX_CHARS:
                raise ValueError(f"source {index} is {len(token)} characters; the limit is {SOURCE_MAX_CHARS}")
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


def standing_prefs_file(storage_key: str) -> Path:
    """Return the system-owned prefs file for one agent.

    Creates the shared ``standing_prefs`` root when missing, never the file.

    Raises:
        ValueError: ``storage_key`` is empty or could escape the root.
    """
    key = (storage_key or "").strip()
    if not key or key in {".", ".."} or "/" in key or "\\" in key:
        raise ValueError("standing prefs require an agent storage key")
    return filesystem.standing_prefs_root() / f"{key}.json"


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
    """Load prefs for the warm inject and the channel sticky context.

    Missing is empty. Only the system writes the store, so an unreadable file
    is a real defect: it logs a warning naming the key, the path and the
    reason, and returns empty so the turn still runs. Does not open
    ``/me/notes`` or any other file.
    """
    try:
        path = standing_prefs_file(storage_key)
    except ValueError:
        logger.warning("standing prefs skipped: missing storage key")
        return []
    try:
        return _load_store(path)
    except ValueError as exc:
        logger.warning(
            "standing prefs unreadable for %s at %s; warm inject skipped: %s",
            storage_key,
            path,
            exc,
        )
        return []


def list_standing_prefs(storage_key: str) -> list[StandingPref]:
    """Return every stored pref in order, for ``pref list``.

    Unlike ``read_standing_prefs`` this is strict: the agent asked to see the
    store, so an unreadable store is an error, not an empty list.

    Raises:
        ValueError: Bad storage key, or the store cannot be read or parsed.
    """
    return _load_store(standing_prefs_file(storage_key))


def set_standing_pref(
    storage_key: str,
    *,
    pref_id: str,
    kind: str,
    text: str,
    sources: list[str],
) -> StandingPref:
    """Add a pref, or replace the pref with the same id, and save the store.

    The new pref keeps the position of the one it replaces; a new id goes last.

    Args:
        storage_key: The agent's storage key.
        pref_id: Short token, 1 to ``ID_MAX_CHARS`` characters.
        kind: One of ``PREF_KINDS``.
        text: The rule, one line up to ``TEXT_MAX_CHARS`` characters.
        sources: 1 to ``SOURCES_MAX`` pointers, each up to ``SOURCE_MAX_CHARS``.

    Returns:
        The validated pref as stored.

    Raises:
        ValueError: One sentence naming the failed rule: a field limit, the
            ``STORE_TEXT_BYTE_CAP`` store cap, or a corrupt current store
            (which is never overwritten). The store is unchanged.
        OSError: The atomic write failed. The previous store is intact.
    """
    try:
        pref = StandingPref.model_validate(
            {"id": pref_id, "kind": kind, "text": text, "sources": list(sources)}
        )
    except ValidationError as exc:
        raise ValueError(_first_error_sentence(exc)) from exc
    existing = _read_existing_for_write(storage_key)
    merged = _merge_prefs(existing, [pref])
    _enforce_store_cap(merged)
    _write_store(standing_prefs_file(storage_key), merged)
    return pref


def remove_standing_pref(storage_key: str, pref_id: str) -> None:
    """Remove one pref by id and save the store.

    Raises:
        ValueError: No pref has that id, or the current store is corrupt
            (and is left untouched).
        OSError: The atomic write failed. The previous store is intact.
    """
    token = pref_id.strip()
    existing = _read_existing_for_write(storage_key)
    remaining = [item for item in existing if item.id != token]
    if len(remaining) == len(existing):
        raise ValueError(f'no pref with id "{token}"')
    _write_store(standing_prefs_file(storage_key), remaining)


def migrate_workspace_standing_prefs() -> None:
    """Move legacy agent-written ``/me/standing_prefs.json`` files into the store.

    Runs at startup and is idempotent. For each
    ``agents/<storage_key>/standing_prefs.json``:

    - no system file yet and it parses: written atomically to
      ``standing_prefs_file(key)``, then the workspace copy is deleted;
    - it does not parse: left in place, untouched, with a warning. It is
      never read again; the operator has the agent re-record with ``pref set``;
    - a system file already exists: the workspace copy is left, with a warning.

    Nothing is repaired and no ids are invented.
    """
    for legacy in sorted(filesystem.agents_artifact_root().glob(f"*/{STANDING_PREFS_FILENAME}")):
        key = legacy.parent.name
        target = standing_prefs_file(key)
        if target.exists():
            logger.warning(
                "legacy standing prefs for %s left at %s: the system store %s already exists",
                key,
                legacy,
                target,
            )
            continue
        try:
            document = parse_standing_prefs_document(legacy.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            logger.warning(
                "legacy standing prefs for %s at %s not migrated and never read: %s",
                key,
                legacy,
                exc,
            )
            continue
        _write_store(target, list(document.prefs))
        legacy.unlink()
        logger.info("standing prefs for %s moved into the system store", key)


def render_warm_section(prefs: list[StandingPref]) -> str | None:
    """Render the warm section, or None when there is nothing to inject."""
    if not prefs:
        return None
    header = "# Standing prefs (manage with pref)"
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


def _load_store(path: Path) -> list[StandingPref]:
    """Missing is empty. Unreadable raises ValueError carrying the reason."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"standing prefs store is unreadable: {exc}") from exc
    try:
        document = parse_standing_prefs_document(raw)
    except ValueError as exc:
        raise ValueError(f"standing prefs store is unreadable: {exc}") from exc
    return list(document.prefs)


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
            f"standing prefs would hold {total} bytes of text; the limit is {STORE_TEXT_BYTE_CAP}. "
            "Replace or remove a pref instead of adding past the limit."
        )


def _write_store(path: Path, prefs: list[StandingPref]) -> None:
    """Write the whole document atomically: a same-directory temp file, then ``os.replace``."""
    payload = _dump(StandingPrefsDocument(schema_version=SCHEMA_VERSION, prefs=prefs))
    partial = path.with_name(f"{path.name}.tmp")
    try:
        partial.write_text(payload, encoding="utf-8")
        os.replace(partial, path)
    except BaseException:
        # Our own half-written temp file, never the store; the error still propagates.
        partial.unlink(missing_ok=True)
        raise


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
    return f"more: {omitted} not shown — run pref list"


def _compose(header: str, lines: list[str], *, more: str | None) -> str:
    parts = [header, *lines]
    if more:
        parts.append(more)
    return "\n".join(parts)


def _error_sentence(error: Any) -> str:
    # A validator's ValueError is already one sentence; pydantic's msg would
    # prefix it with "Value error, ".
    if error.get("type") == "value_error":
        ctx = error.get("ctx") or {}
        if "error" in ctx:
            return str(ctx["error"])
    return str(error.get("msg") or "invalid")


def _first_error_sentence(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid pref"
    first = errors[0]
    if first.get("type") == "value_error":
        return _error_sentence(first)
    loc = ".".join(str(part) for part in first.get("loc", ())) or "pref"
    return f"{loc}: {_error_sentence(first)}"


def _brief_validation(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid document"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ())) or "document"
    return f"{loc}: {_error_sentence(first)}"
