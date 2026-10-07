"""Agent memory: a small, system-owned store shown to the agent on every turn.

Each agent's memory lives in ``<artifacts>/system/standing_prefs/<storage_key>.json``
(``schema_version`` 2), outside every agent path: no virtual mount and no
shell path-jail root reaches it. Only this module reads or writes the file.
The file and settings keep their ``standing_prefs`` names: they are storage
identifiers, and renaming them would need a data migration for no gain.

Agents change their memory only through the ``memory`` CLI command
(``add_memory``, ``replace_memory``, ``remove_memory``, ``list_memories``);
the operator removes one from the desk (``remove_memory`` via the API).
Work-turn assembly and the channel router read it with ``read_memories``.

A memory is ``{id, text}``. The system assigns the id from the document's
``next_id``: ids only grow and are never reused, so removing #2 never turns
#3 into #2, and an agent acting on a list it read a few turns ago cannot hit
the wrong memory. Replace keeps the id and the position. The store is not
compacted and memories are not dropped to make room. Writes are atomic.

Two processes write the store (the runtime worker for the agent's ``memory``
command, the app for the desk's remove), so every read-modify-write holds an
exclusive ``fcntl.flock`` on a sidecar ``<storage_key>.lock`` in the same
root. Atomic replace alone prevents torn files but not lost updates. Reads
take no lock: ``os.replace`` already guarantees they see a whole document.

Fixed invariants, enforced on every parse (including a stored document):

- memory text: non-empty, one line (one line still holds two sentences)
- id: an integer of at least 1, unique in the document
- ``next_id``: greater than every id

Operator limits (Settings → System → Context Window), read live on each save
and render, never on parse:

- ``standing_prefs_line_max_chars``: the longest memory text ``add`` and
  ``replace`` accept, and the most of one memory's text the warm line shows.
  It limits the text only; the ``- n — `` prefix never counts, so a saved
  text always shows whole.
- ``standing_prefs_section_max_chars``: the most characters of the rendered
  warm section (whole lines, prefix included), and the cap on total memory
  text, in characters, across one agent's store at save time.

Lowering a limit never hides a stored memory: the document still parses and
the memory is still injected. A text longer than a lowered line limit is cut
with ``...`` in the warm line only; saves refuse new text over the limit, and
``memory list`` still shows every memory whole.

``migrate_standing_prefs_v1`` converts each ``schema_version`` 1 file (the
retired ``pref`` store: ids, kinds and sources) to v2 once, at startup.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

LINE_MAX_CHARS_SETTING = "standing_prefs_line_max_chars"
SECTION_MAX_CHARS_SETTING = "standing_prefs_section_max_chars"

# The most digits a memory number is budgeted in the warm line prefix. Used
# only for the Settings section minimum (``WARM_PREFIX_MAX_CHARS``).
MEMORY_ID_MAX_DIGITS = 6

# The first line of the rendered warm section. ``render_warm_section`` uses
# it, and Settings counts it (plus its newline) in the section minimum.
WARM_SECTION_HEADER = "# Your memory (shown every turn; manage with memory)"
# The longest ``- n — `` prefix a warm line can carry: the line limit counts
# text only, so the section limit needs this much more room to hold one full
# line.
WARM_PREFIX_MAX_CHARS = len("- ") + MEMORY_ID_MAX_DIGITS + len(" — ")


class MemoryNotFoundError(ValueError):
    """No memory has the requested id. Distinct from a corrupt store."""


class Memory(BaseModel):
    """One memory: a system-assigned id and one line of text.

    Each validator raises one sentence; the ``memory`` command shows that
    sentence to the agent unchanged.
    """

    # Strict: a stored ``true`` or ``"3"`` is a corrupt id, not a coercion.
    model_config = ConfigDict(extra="forbid", strict=True)

    id: int
    text: str

    @field_validator("id")
    @classmethod
    def _positive_id(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"memory id {value} must be at least 1")
        return value

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        # No length check here: this runs on every parse of the stored
        # document, and the length limit is an operator setting. Checking it
        # here would make a lowered limit fail the whole store and silently
        # drop every memory. Saves enforce it (``_checked_text``).
        text = value.strip()
        if not text:
            raise ValueError("memory text is empty; give it as one line")
        if "\n" in text or "\r" in text:
            raise ValueError("memory text has a line break; keep it to one line")
        return text


class MemoryDocument(BaseModel):
    """On-disk document (v2). Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2]
    next_id: int
    memories: list[Memory]

    @model_validator(mode="after")
    def _ids_consistent(self) -> MemoryDocument:
        ids = [item.id for item in self.memories]
        if len(ids) != len(set(ids)):
            raise ValueError("memory ids must be unique")
        if self.next_id < 1:
            raise ValueError(f"next_id {self.next_id} must be at least 1")
        if ids and self.next_id <= max(ids):
            raise ValueError(f"next_id {self.next_id} must be greater than every memory id")
        return self


class _V1Pref(BaseModel):
    """One retired v1 pref, read only by ``migrate_standing_prefs_v1``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    kind: str
    text: str
    sources: list[str]


class _V1Document(BaseModel):
    """The retired v1 document, read only by ``migrate_standing_prefs_v1``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    prefs: list[_V1Pref]


def standing_prefs_file(storage_key: str) -> Path:
    """Return the system-owned memory file for one agent.

    Creates the shared ``standing_prefs`` root when missing, never the file.

    Raises:
        ValueError: ``storage_key`` is empty or could escape the root.
    """
    # Imported here, not at module top: ``core.bm_cli`` eagerly imports its
    # runtime, which reaches ``command_registry`` → this module while it is
    # still initialising, so a top-level import breaks a cold import of this
    # module (and of ``runtime_core``). A call-time import also reads the
    # module attributes tests monkeypatch (``_SYSTEM_ROOT``).
    from core.bm_cli import filesystem

    key = (storage_key or "").strip()
    if not key or key in {".", ".."} or "/" in key or "\\" in key:
        raise ValueError("agent memory requires an agent storage key")
    return filesystem.standing_prefs_root() / f"{key}.json"


def line_max_chars() -> int:
    """Return the operator's memory text limit, read live from Settings.

    ``standing_prefs_line_max_chars`` is the longest text a save accepts and
    the most of one memory's text the warm line shows. It limits the text
    only; the ``- n — `` prefix never counts.

    Raises:
        config.ConfigError: The setting is missing, not an integer, or below 1.
    """
    return _positive_int_setting(LINE_MAX_CHARS_SETTING)


def section_max_chars() -> int:
    """Return the operator's warm section and store cap, read live from Settings.

    ``standing_prefs_section_max_chars`` caps the rendered warm section
    (whole lines) and, at save time, the total characters of memory text in
    one agent's store.

    Raises:
        config.ConfigError: The setting is missing, not an integer, or below 1.
    """
    return _positive_int_setting(SECTION_MAX_CHARS_SETTING)


def _positive_int_setting(key: str) -> int:
    # Call-time import: ``core.config`` imports ``db``, which reaches
    # ``core.bm_cli`` and the same cycle described in ``standing_prefs_file``.
    from core import config

    value = config.require_int(key)
    # The API refuses values below 1; a raw DB write could still store one,
    # and a zero or negative cap would render nonsense instead of failing.
    if value < 1:
        raise config.ConfigError(f"Setting '{key}' must be at least 1: {value}")
    return value


def parse_memory_document(raw: str) -> MemoryDocument:
    """Parse one v2 memory document. Invalid JSON or schema raises ValueError."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("memory store must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("memory store must be a JSON object")
    try:
        return MemoryDocument.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"memory store schema rejected: {_brief_validation(exc)}") from exc


def read_memories(storage_key: str) -> list[Memory]:
    """Load memories for the warm inject and the channel sticky context.

    Lenient: missing is empty. Only the system writes the store, so an
    unreadable file is a real defect: it logs a warning naming the key, the
    path and the reason, and returns empty so the turn still runs. Does not
    open ``/me/notes`` or any other file.
    """
    try:
        path = standing_prefs_file(storage_key)
    except ValueError:
        logger.warning("agent memory skipped: missing storage key")
        return []
    try:
        return list(_load_document(path).memories)
    except ValueError as exc:
        logger.warning(
            "agent memory unreadable for %s at %s; warm inject skipped: %s",
            storage_key,
            path,
            exc,
        )
        return []


def list_memories(storage_key: str) -> list[Memory]:
    """Return every stored memory in order, for ``memory list`` and the desk.

    Unlike ``read_memories`` this is strict: the caller asked to see the
    store, so an unreadable store is an error, not an empty list.

    Raises:
        ValueError: Bad storage key, or the store cannot be read or parsed.
    """
    return list(_load_document(standing_prefs_file(storage_key)).memories)


def add_memory(storage_key: str, text: str) -> Memory:
    """Save a new memory under the next id and append it to the store.

    Args:
        storage_key: The agent's storage key.
        text: One line, at most ``line_max_chars()`` characters after strip.

    Returns:
        The memory as stored, carrying its assigned id.

    Raises:
        ValueError: One sentence naming the failed rule: empty text, a line
            break, the text limit, growth past the ``section_max_chars()``
            store cap, or a corrupt current store (which is never
            overwritten). The store is unchanged.
        config.ConfigError: A limit setting is missing or invalid.
        OSError: The lock or the atomic write failed. The previous store is
            intact.
    """
    clean = _checked_text(text)
    path = standing_prefs_file(storage_key)
    with _store_lock(storage_key):
        document = _read_existing_for_write(path)
        memory = Memory(id=document.next_id, text=clean)
        merged = [*document.memories, memory]
        _enforce_store_cap(document.memories, merged)
        _write_store(path, _document(next_id=document.next_id + 1, memories=merged))
    return memory


def replace_memory(storage_key: str, memory_id: int, text: str) -> Memory:
    """Replace one memory's text, keeping its id and its position.

    Raises:
        MemoryNotFoundError: No memory has ``memory_id``.
        ValueError: The text or store-cap rules from ``add_memory``, or a
            corrupt current store (never overwritten).
        config.ConfigError: A limit setting is missing or invalid.
        OSError: The lock or the atomic write failed.
    """
    clean = _checked_text(text)
    path = standing_prefs_file(storage_key)
    with _store_lock(storage_key):
        document = _read_existing_for_write(path)
        index = _index_of(document.memories, memory_id)
        memory = Memory(id=memory_id, text=clean)
        merged = list(document.memories)
        merged[index] = memory
        _enforce_store_cap(document.memories, merged)
        _write_store(path, _document(next_id=document.next_id, memories=merged))
    return memory


def remove_memory(storage_key: str, memory_id: int) -> Memory:
    """Remove one memory by id. ``next_id`` is untouched, so the id is never reused.

    Returns:
        The memory that was removed, so the caller can say what went.

    Raises:
        MemoryNotFoundError: No memory has ``memory_id``.
        ValueError: The current store is corrupt (and is left untouched).
        OSError: The lock or the atomic write failed.
    """
    path = standing_prefs_file(storage_key)
    with _store_lock(storage_key):
        document = _read_existing_for_write(path)
        index = _index_of(document.memories, memory_id)
        remaining = list(document.memories)
        removed = remaining.pop(index)
        _write_store(path, _document(next_id=document.next_id, memories=remaining))
    return removed


def migrate_standing_prefs_v1() -> None:
    """Convert each v1 store (the retired ``pref`` shape) to v2 once.

    Runs at startup and is idempotent. For each ``standing_prefs/*.json``:

    - ``schema_version`` 2: skipped;
    - ``schema_version`` 1 and it parses: ids 1..n in stored order, each
      ``text`` kept exactly, ``kind`` and ``sources`` dropped, ``next_id =
      n + 1``, written atomically under the store lock;
    - anything else (bad JSON, an unknown version, a v1 that does not parse):
      left untouched, with a warning naming the key and the path. Nothing is
      invented or repaired.
    """
    # Call-time import: see ``standing_prefs_file`` for the import cycle.
    from core.bm_cli import filesystem

    for path in sorted(filesystem.standing_prefs_root().glob("*.json")):
        key = path.stem
        with _store_lock(key):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                logger.warning("agent memory for %s at %s not migrated: unreadable: %s", key, path, exc)
                continue
            version = payload.get("schema_version") if isinstance(payload, dict) else None
            if version == SCHEMA_VERSION:
                continue
            if version != 1:
                logger.warning(
                    "agent memory for %s at %s not migrated: schema_version %r is not 1 or 2",
                    key,
                    path,
                    version,
                )
                continue
            try:
                legacy = _V1Document.model_validate(payload)
                memories = [
                    Memory(id=index, text=pref.text) for index, pref in enumerate(legacy.prefs, start=1)
                ]
                document = _document(next_id=len(memories) + 1, memories=memories)
            except ValidationError as exc:
                logger.warning(
                    "agent memory for %s at %s not migrated: v1 schema rejected: %s",
                    key,
                    path,
                    _brief_validation(exc),
                )
                continue
            _write_store(path, document)
            logger.info("agent memory for %s converted from v1 to v2 (%d memories)", key, len(memories))


def render_warm_section(memories: list[Memory]) -> str | None:
    """Render the warm section, or None when there is nothing to inject.

    Reads ``line_max_chars()`` and ``section_max_chars()`` at render time, so
    a Settings change applies to the next turn. Whole lines are kept in store
    order until the section cap; the rest are counted in a ``more:`` line that
    points at ``memory list``.

    Raises:
        config.ConfigError: A limit setting is missing or invalid.
    """
    if not memories:
        return None
    line_limit = line_max_chars()
    section_limit = section_max_chars()
    header = WARM_SECTION_HEADER
    lines = [_warm_line(memory, line_limit) for memory in memories]
    kept: list[str] = []
    for line in lines:
        if len(_compose(header, kept + [line], more=None)) <= section_limit:
            kept.append(line)
            continue
        break
    omitted = len(lines) - len(kept)
    if omitted == 0:
        return _compose(header, kept, more=None)
    more = _more_line(omitted)
    while kept and len(_compose(header, kept, more=more)) > section_limit:
        kept.pop()
        omitted = len(lines) - len(kept)
        more = _more_line(omitted)
    rendered = _compose(header, kept, more=more)
    if len(rendered) <= section_limit:
        return rendered
    return rendered[:section_limit].rstrip()


@contextmanager
def _store_lock(storage_key: str) -> Iterator[None]:
    """Hold an exclusive, blocking ``flock`` on ``<storage_key>.lock`` until exit.

    The lock file sits beside the store and is created when missing; deleting
    the whole root (reset, delete-all) removes it with the store.
    """
    lock_path = standing_prefs_file(storage_key).with_suffix(".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _checked_text(text: str) -> str:
    """Validate a text for a save: the parse invariants, then the live limit."""
    try:
        clean = Memory(id=1, text=text).text
    except ValidationError as exc:
        raise ValueError(_first_error_sentence(exc)) from exc
    # The operator limit applies at save time only; parsing never checks it.
    limit = line_max_chars()
    if len(clean) > limit:
        raise ValueError(
            f"memory is {len(clean)} characters; the limit is {limit}. Keep it to 1–2 short sentences."
        )
    return clean


def _index_of(memories: list[Memory], memory_id: int) -> int:
    for index, memory in enumerate(memories):
        if memory.id == memory_id:
            return index
    raise MemoryNotFoundError(f"no memory #{memory_id}")


def _document(*, next_id: int, memories: list[Memory]) -> MemoryDocument:
    return MemoryDocument(schema_version=SCHEMA_VERSION, next_id=next_id, memories=memories)


def _load_document(path: Path) -> MemoryDocument:
    """Missing is an empty document. Unreadable raises ValueError carrying the reason."""
    if not path.exists():
        return _document(next_id=1, memories=[])
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"memory store is unreadable: {exc}") from exc
    try:
        return parse_memory_document(raw)
    except ValueError as exc:
        raise ValueError(f"memory store is unreadable: {exc}") from exc


def _read_existing_for_write(path: Path) -> MemoryDocument:
    if not path.exists():
        return _document(next_id=1, memories=[])
    if not path.is_file():
        raise ValueError("memory store path is not a file; refusing to overwrite")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("memory store is unreadable; refusing to overwrite") from exc
    try:
        return parse_memory_document(raw)
    except ValueError as exc:
        raise ValueError("memory store is unreadable; refusing to overwrite") from exc


def _enforce_store_cap(existing: list[Memory], merged: list[Memory]) -> None:
    # Characters, not bytes: the cap is the section cap, which counts characters.
    cap = section_max_chars()
    old_total = sum(len(item.text) for item in existing)
    new_total = sum(len(item.text) for item in merged)
    # Only growth past the cap is refused. After the operator lowers the
    # section limit below a store's total, a save that shrinks or keeps the
    # total must still pass, or the agent could not tidy its memory without
    # removing some first.
    if new_total > cap and new_total > old_total:
        raise ValueError(
            f"memory would grow to {new_total} characters; the limit is {cap}. "
            "Replace or remove a memory first."
        )


def _write_store(path: Path, document: MemoryDocument) -> None:
    """Write the whole document atomically: a same-directory temp file, then ``os.replace``."""
    payload = _dump(document)
    partial = path.with_name(f"{path.name}.tmp")
    try:
        partial.write_text(payload, encoding="utf-8")
        os.replace(partial, path)
    except BaseException:
        # Our own half-written temp file, never the store; the error still propagates.
        partial.unlink(missing_ok=True)
        raise


def _dump(document: MemoryDocument) -> str:
    payload = document.model_dump()
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _warm_line(memory: Memory, limit: int) -> str:
    # ``limit`` bounds the text only. The prefix never counts, so a text that
    # saved under the limit always shows whole.
    prefix = f"- {memory.id} — "
    text = memory.text
    if len(text) > limit:
        # Only after the operator lowered the limit below a stored memory:
        # it still shows, cut, and ``memory list`` shows it whole.
        if limit <= 3:
            return f"{prefix}{text[:limit]}"
        return f"{prefix}{text[: limit - 3].rstrip()}..."
    return f"{prefix}{text}"


def _more_line(omitted: int) -> str:
    return f"more: {omitted} not shown — run memory list"


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
        return "invalid memory"
    first = errors[0]
    if first.get("type") == "value_error":
        return _error_sentence(first)
    loc = ".".join(str(part) for part in first.get("loc", ())) or "memory"
    return f"{loc}: {_error_sentence(first)}"


def _brief_validation(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid document"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ())) or "document"
    return f"{loc}: {_error_sentence(first)}"
