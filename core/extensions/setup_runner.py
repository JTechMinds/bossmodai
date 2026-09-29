"""BossMod AI — run an extension's setup in the background and read its state.

The runner owns the lock and error markers; the extension owns ``ready.json``
(see ``contract``). Status is read from those files alone, so any process can
report it without importing the extension.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Mapping

from core.extensions.contract import (
    READY_FILE,
    SETUP_ERROR_FILE,
    SETUP_LOCK_FILE,
    SETUP_LOG_FILE,
    SetupError,
    SetupStatus,
)
from core.extensions.loader import load_extension
from core.extensions.paths import extension_data_dir
from core.extensions.registry import ExtensionEntry, set_enabled

logger = logging.getLogger(__name__)

OUT_OF_DATE_DETAIL = "Setup needs to run again: the installed version is out of date."


class SetupAlreadyRunning(Exception):
    """A live process already holds this extension's setup lock."""


def read_setup_status(data_dir: Path, *, required: bool, ready_requires: Mapping[str, str]) -> SetupStatus:
    """Derive setup state from the marker files in ``data_dir``.

    Args:
        data_dir: The extension's data dir (may not exist yet).
        required: The manifest's ``setup.required``.
        ready_requires: The manifest's ``setup.ready_requires``: keys
            ``ready.json`` must hold with these exact string values.

    Returns:
        ``not_required`` when no setup exists; else ``installing`` while a
        live pid holds the lock, ``failed`` for a dead pid ("interrupted") or
        a recorded error, ``ready`` once ``ready.json`` exists and meets
        ``ready_requires``, ``missing`` with ``OUT_OF_DATE_DETAIL`` when it
        exists but does not (or is not a JSON object), otherwise ``missing``.

    Raises:
        OSError: ``ready.json`` exists but cannot be read.
    """
    if not required:
        return SetupStatus(state="not_required")
    lock = data_dir / SETUP_LOCK_FILE
    if lock.exists():
        pid = _lock_pid(lock)
        if pid is not None and _pid_alive(pid):
            return SetupStatus(state="installing")
        return SetupStatus(state="failed", detail="Setup was interrupted before it finished. Retry to start again.")
    error = data_dir / SETUP_ERROR_FILE
    if error.exists():
        return SetupStatus(state="failed", detail=error.read_text(encoding="utf-8").strip() or "Setup failed.")
    ready = data_dir / READY_FILE
    if not ready.exists():
        return SetupStatus(state="missing")
    if ready_requires and not _meets(ready, ready_requires):
        return SetupStatus(state="missing", detail=OUT_OF_DATE_DETAIL)
    return SetupStatus(state="ready")


def _meets(ready: Path, ready_requires: Mapping[str, str]) -> bool:
    """Return whether ``ready.json`` holds every required key with its exact value.

    Unparseable JSON is an install this version cannot vouch for, so it
    fails the check (the operator sees "out of date" and re-runs setup).
    """
    try:
        marker = json.loads(ready.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(marker, dict):
        return False
    return all(marker.get(key) == value for key, value in ready_requires.items())


def entry_setup_status(entry: ExtensionEntry) -> SetupStatus:
    """Return one discovered extension's setup state without importing it.

    Raises:
        ValueError: The entry has no manifest (an unreadable folder).
    """
    if entry.manifest is None:
        raise ValueError(f"extension {entry.id} has no manifest")
    setup = entry.manifest.setup
    return read_setup_status(extension_data_dir(entry.id), required=setup.required, ready_requires=setup.ready_requires)


def start_setup(entry: ExtensionEntry, *, enable_on_success: bool) -> None:
    """Take the setup lock, then run the extension's setup on a daemon thread.

    The lock is taken before this returns, so a status read right after it
    already says ``installing``. On success the thread enables the extension
    when asked; on failure it writes ``setup.error``. The lock is released
    either way.

    Args:
        entry: A valid discovered extension whose manifest requires setup.
        enable_on_success: Add the id to ``extensions_enabled`` once ready.

    Raises:
        SetupAlreadyRunning: A live process holds the lock.
        core.extensions.loader.ExtensionLoadError: The extension cannot load.
        ValueError: The extension does not require setup.
    """
    if entry.manifest is None or not entry.manifest.setup.required:
        raise ValueError(f"extension {entry.id} has no setup step")
    extension = load_extension(entry)
    data_dir = extension_data_dir(entry.id)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = data_dir / SETUP_LOCK_FILE
    _acquire_lock(lock)
    (data_dir / SETUP_ERROR_FILE).unlink(missing_ok=True)
    log_path = data_dir / SETUP_LOG_FILE
    log_path.write_text("", encoding="utf-8")

    def run() -> None:
        try:
            extension.run_setup(log_path)
        except SetupError as exc:
            logger.error("Extension %s setup failed: %s", entry.id, exc)
            _write_error(data_dir, str(exc))
        except Exception as exc:
            # A bug, not a reported failure: keep the traceback in the log and
            # still record it, so the card shows failed instead of hanging.
            logger.exception("Extension %s setup crashed", entry.id)
            _write_error(data_dir, f"Setup crashed: {exc}")
        else:
            logger.info("Extension %s setup finished", entry.id)
            if enable_on_success:
                set_enabled(entry.id, True)
        finally:
            lock.unlink(missing_ok=True)

    threading.Thread(target=run, name=f"extension-setup-{entry.id}", daemon=True).start()


def _acquire_lock(lock: Path) -> None:
    """Create ``lock`` with this pid, replacing it only when its owner is dead."""
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pid = _lock_pid(lock)
            if pid is not None and _pid_alive(pid):
                raise SetupAlreadyRunning(f"setup is already running (pid {pid})") from None
            # The owner died mid-setup; its lock is stale.
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return
    raise SetupAlreadyRunning("another process took the setup lock at the same moment")


def _lock_pid(lock: Path) -> int | None:
    """Return the pid in a lock file, or ``None`` when it is gone or not a number."""
    try:
        text = lock.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return int(text) if text.isdigit() else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists but belongs to another user: alive.
        return True
    return True


def _write_error(data_dir: Path, message: str) -> None:
    (data_dir / SETUP_ERROR_FILE).write_text(message, encoding="utf-8")
