"""BossMod AI — extension discovery and the enabled set.

Discovery reads only static install files (manifests and prompt files), so it
is cached for the life of the process: a newly dropped folder needs a
restart. The enabled set lives in the ``extensions_enabled`` setting (a JSON
array of ids) and is read live, so the runtime worker sees an operator's
toggle without a restart.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from core.extensions.manifest import MANIFEST_FILE, ExtensionManifest, ManifestError, load_manifest
from core.extensions.paths import extensions_root

logger = logging.getLogger(__name__)

# ``db`` and ``core.config`` are imported inside the functions that use them:
# importing ``db`` loads the CLI runtime, which imports this module to
# register extension commands, so top-level imports would be circular.

ENABLED_SETTING = "extensions_enabled"
SETTING_CATEGORY = "extensions"
_PACKAGE_FILE = "__init__.py"


class ExtensionSettingError(Exception):
    """``extensions_enabled`` is missing or is not a JSON array of ids."""


@dataclass(frozen=True)
class ExtensionEntry:
    """One discovered extension folder.

    Attributes:
        id: The manifest id, or the folder name when the manifest is unusable.
        root: The extension folder.
        manifest: The validated manifest; ``None`` when it could not be read.
        invalid_reason: Why the extension will never load; ``None`` when valid.
    """

    id: str
    root: Path
    manifest: ExtensionManifest | None
    invalid_reason: str | None

    @property
    def valid(self) -> bool:
        """Whether the extension may be loaded and enabled."""
        return self.invalid_reason is None and self.manifest is not None


@dataclass(frozen=True)
class Discovery:
    """Every extension folder found at process start, in folder-name order."""

    entries: tuple[ExtensionEntry, ...]

    def get(self, ext_id: str) -> ExtensionEntry | None:
        """Return the entry with this id, or ``None`` when none has it."""
        return next((entry for entry in self.entries if entry.id == ext_id), None)

    def valid_entries(self) -> tuple[ExtensionEntry, ...]:
        """Return the entries that may be loaded, in discovery order."""
        return tuple(entry for entry in self.entries if entry.valid)


def discover(root: Path, reserved_commands: frozenset[str]) -> Discovery:
    """Scan ``root`` for extension folders and validate each one.

    A folder is an extension when it holds a ``manifest.json``; other folders
    (``__pycache__``, dot folders) are ignored. An extension is invalid, and
    listed with its reason, when its manifest fails, its package or prompt
    file is missing, its id is used by another folder, a required Python
    module is not installed, or its command collides with a core command or
    another extension's.

    Args:
        root: The folder to scan. A missing folder means no extensions.
        reserved_commands: Core command names and aliases.

    Returns:
        The discovery, in folder-name order.
    """
    if not root.is_dir():
        logger.info("No extensions folder at %s; no extensions discovered", root)
        return Discovery(entries=())
    candidates: list[ExtensionEntry] = []
    for folder in sorted(root.iterdir(), key=lambda p: p.name):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        if not (folder / MANIFEST_FILE).is_file():
            continue
        candidates.append(_validate_folder(folder))

    entries = _mark_collisions(candidates, reserved_commands)
    for entry in entries:
        if not entry.valid:
            logger.warning("Extension %s is invalid: %s", entry.id, entry.invalid_reason)
    return Discovery(entries=tuple(entries))


def _validate_folder(folder: Path) -> ExtensionEntry:
    try:
        manifest = load_manifest(folder / MANIFEST_FILE)
    except ManifestError as exc:
        return ExtensionEntry(id=folder.name, root=folder, manifest=None, invalid_reason=str(exc))
    reason: str | None = None
    if not (folder / _PACKAGE_FILE).is_file():
        reason = f"missing {_PACKAGE_FILE}"
    elif manifest.prompt is not None and not (folder / manifest.prompt).is_file():
        reason = f"missing prompt file {manifest.prompt}"
    else:
        missing = [name for name in manifest.requires.python_modules if importlib.util.find_spec(name) is None]
        if missing:
            reason = "missing dependency " + ", ".join(missing)
    return ExtensionEntry(id=manifest.id, root=folder, manifest=manifest, invalid_reason=reason)


def _mark_collisions(
    candidates: list[ExtensionEntry],
    reserved_commands: frozenset[str],
) -> list[ExtensionEntry]:
    """Invalidate duplicate ids and command names; neither side of a clash loads."""
    id_counts: dict[str, int] = {}
    command_counts: dict[str, int] = {}
    for entry in candidates:
        id_counts[entry.id] = id_counts.get(entry.id, 0) + 1
        if entry.valid:
            name = entry.manifest.command.name
            command_counts[name] = command_counts.get(name, 0) + 1

    marked: list[ExtensionEntry] = []
    for entry in candidates:
        reason = entry.invalid_reason
        if reason is None and id_counts[entry.id] > 1:
            reason = f"id {entry.id!r} is used by more than one extension folder"
        if reason is None and entry.manifest is not None:
            name = entry.manifest.command.name
            if name in reserved_commands:
                reason = f"command {name!r} collides with a core command"
            elif command_counts.get(name, 0) > 1:
                reason = f"command {name!r} collides with another extension"
        marked.append(
            entry if reason == entry.invalid_reason
            else ExtensionEntry(id=entry.id, root=entry.root, manifest=entry.manifest, invalid_reason=reason)
        )
    return marked


_discovery: Discovery | None = None
# Re-entrant: importing the core registry below can load the CLI runtime,
# which registers extension handlers and so calls back into get_discovery on
# the same thread.
_discovery_lock = threading.RLock()
# Serialises this process's read-modify-write of the enabled set (the API
# route and a finishing setup thread can both write it).
_enabled_write_lock = threading.Lock()


def get_discovery() -> Discovery:
    """Return this process's discovery, scanning the install folder once.

    Per-folder problems become invalid entries rather than errors.
    """
    global _discovery
    with _discovery_lock:
        if _discovery is None:
            from core.bm_cli.command_registry import CORE_COMMAND_NAMES

            # That import may have loaded the CLI runtime, which already ran
            # discovery through the re-entrant call; keep its result.
            if _discovery is None:
                _discovery = discover(extensions_root(), CORE_COMMAND_NAMES)
        return _discovery


def enabled_ids() -> frozenset[str]:
    """Return the ids the operator enabled, read live from settings.

    Returns:
        The stored ids. Ids with no discovered folder are included; callers
        look ids up in the discovery.

    Raises:
        ExtensionSettingError: The setting is missing or is not a JSON array
            of strings. It is written only by the extensions API, so a bad
            value is a corrupted row, not an operator choice to ignore.
    """
    from core import config

    raw = config.get_live(ENABLED_SETTING)
    if raw is None:
        raise ExtensionSettingError(f"setting {ENABLED_SETTING!r} is missing; it is seeded on init_db")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtensionSettingError(f"setting {ENABLED_SETTING!r} is not JSON: {exc.msg}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExtensionSettingError(f"setting {ENABLED_SETTING!r} must be a JSON array of extension ids")
    return frozenset(value)


def is_enabled(ext_id: str) -> bool:
    """Return whether the operator enabled ``ext_id`` (read live)."""
    return ext_id in enabled_ids()


def set_enabled(ext_id: str, enabled: bool) -> None:
    """Add or remove ``ext_id`` in the enabled set and refresh this process's cache.

    Validation (known, valid, set up) is the caller's; this only writes.

    Args:
        ext_id: The extension id.
        enabled: The new state.

    Raises:
        ExtensionSettingError: The stored setting is unreadable.
    """
    import db
    from core import config

    with _enabled_write_lock:
        current = set(enabled_ids())
        if enabled:
            current.add(ext_id)
        else:
            current.discard(ext_id)
        db.set_setting(ENABLED_SETTING, json.dumps(sorted(current)), SETTING_CATEGORY)
        config.reload()
