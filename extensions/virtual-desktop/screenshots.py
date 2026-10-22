"""Virtual Desktop — the screenshots the agents were sent, per agent, with pruning.

``shots/<agent_id>/<utc-ts>.png`` with a JSON sidecar of the same stem. The
sidecar holds the exact values of the agent's result lines (``screen``,
``image``, ``pointer``, the window title) and the pid of the worker that
took it. The app process serves the operator's live view from these files
alone, and a pid that is no longer alive marks leftovers of a dead worker.

Screenshots are deleted when the desktop stops or resets, and at the
worker's boot reconcile, so nothing shows a stopped desktop as current.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PARTIAL_SUFFIX = ".partial"


@dataclass(frozen=True)
class ShotMeta:
    """What one screenshot showed, as the agent's result described it.

    Attributes:
        command: The ``vm`` command as the agent typed it.
        window: The focused window's title, verbatim ("" when none).
        screen: The ``screen`` line's value, e.g. ``1280x800``.
        image: The ``image`` line's value, e.g. ``1568x882 (scale ×0.8167)``.
        pointer: The ``pointer`` line's value.
        taken_at: ISO-8601 UTC capture time.
        pid: The process that took it (the runtime worker).
    """

    command: str
    window: str
    screen: str
    image: str
    pointer: str
    taken_at: str
    pid: int


class ShotStore:
    """Writes and reads ``<root>/<agent_id>/<utc-ts>.png`` plus sidecars.

    Args:
        root: The shots folder (inside the extension data dir).
        keep: How many screenshots to keep per agent (at least 1).

    Raises:
        ValueError: ``keep`` is below 1 (the file just stored would be pruned).
    """

    def __init__(self, root: Path, keep: int) -> None:
        if keep < 1:
            raise ValueError("screenshots_keep must be at least 1")
        self._root = root
        self._keep = keep

    def store(self, agent_id: str, png: bytes, meta: ShotMeta) -> Path:
        """Write one screenshot and its sidecar, prune, and return the PNG's path.

        The sidecar is written first and each file is renamed into place, so
        a reader in another process never sees a PNG without a complete
        sidecar or a half-written file.

        Raises:
            OSError: A file cannot be written.
        """
        folder = self._root / agent_id
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = folder / f"{stamp}.png"
        suffix = 1
        while path.exists():
            path = folder / f"{stamp}_{suffix}.png"
            suffix += 1
        _write_atomic(path.with_suffix(".json"), json.dumps(asdict(meta)).encode("utf-8"))
        _write_atomic(path, png)
        self._prune(folder)
        return path

    def latest(self, agent_id: str) -> tuple[Path, ShotMeta] | None:
        """Return the agent's newest screenshot that has a readable sidecar.

        A PNG whose sidecar is missing or malformed is skipped with a
        warning, so one bad file cannot blank the live view.

        Returns:
            ``(png path, meta)``, or ``None`` when the agent has none.
        """
        folder = self._root / agent_id
        if not folder.is_dir():
            return None
        for png in sorted(folder.glob("*.png"), key=lambda item: item.name, reverse=True):
            sidecar = png.with_suffix(".json")
            try:
                meta = _read_meta(sidecar)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Virtual Desktop: skipping %s, its sidecar is unreadable (%s)", png, exc)
                continue
            return png, meta
        return None

    def delete(self, agent_id: str) -> None:
        """Delete every screenshot of one agent (its desktop stopped or was reset).

        Raises:
            OSError: A file cannot be removed.
        """
        folder = self._root / agent_id
        if folder.is_dir():
            shutil.rmtree(folder)

    def agents(self) -> list[str]:
        """Return the ids of agents that have a screenshot folder."""
        if not self._root.is_dir():
            return []
        return sorted(item.name for item in self._root.iterdir() if item.is_dir())

    def _prune(self, folder: Path) -> None:
        shots = sorted(folder.glob("*.png"), key=lambda item: item.name)
        for old in shots[: max(0, len(shots) - self._keep)]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    """Return whether process ``pid`` exists.

    ``os.kill(pid, 0)`` sends no signal; it only checks. A process owned by
    another user raises ``PermissionError`` but exists, so it counts as alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_atomic(path: Path, data: bytes) -> None:
    partial = path.with_name(path.name + _PARTIAL_SUFFIX)
    partial.write_bytes(data)
    os.replace(partial, path)


def _read_meta(sidecar: Path) -> ShotMeta:
    """Read and type-check one sidecar.

    Raises:
        OSError: It cannot be read.
        ValueError: It is not JSON, a field has the wrong type, or
            ``taken_at`` is not an ISO time.
        TypeError: Fields are missing or unknown.
    """
    raw = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("the sidecar is not a JSON object")
    meta = ShotMeta(**raw)
    for item in fields(ShotMeta):
        expected = int if item.name == "pid" else str
        value = getattr(meta, item.name)
        if not isinstance(value, expected) or isinstance(value, bool):
            raise ValueError(f"{item.name} is not a {expected.__name__}")
    if meta.pid <= 0:
        raise ValueError("pid must be positive")
    datetime.fromisoformat(meta.taken_at)
    return meta
