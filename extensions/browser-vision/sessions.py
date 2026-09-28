"""Browser Vision — one marker file per open browser session.

The runtime worker owns the browsers, but the operator's live view is served
by the app process, which cannot ask the worker's browser thread anything.
So while a session is open the worker keeps ``sessions/<agent_id>.json`` in
the extension data dir, and the app lists an agent only when its marker's
process is alive. A marker left by a process that died is stale: it is never
treated as live, and the next worker clears it at start.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SessionMarker(BaseModel):
    """What the marker file of one open session holds.

    Attributes:
        session_id: The host's id for the session (its screenshot folder name).
        pid: The process that owns the browser.
        opened_at: ISO-8601 UTC time the session opened.
        url: The page URL after the latest action.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    pid: int = Field(gt=0)
    opened_at: str
    url: str


class SessionMarkerError(ValueError):
    """A marker file exists but cannot be read as a ``SessionMarker``."""


class SessionMarkers:
    """Reads and writes ``<root>/<agent_id>.json`` markers.

    Args:
        root: The sessions folder (inside the extension data dir).
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, agent_id: str) -> Path:
        """The marker file of one agent (it may not exist)."""
        return self._root / f"{agent_id}.json"

    def write(self, agent_id: str, marker: SessionMarker) -> None:
        """Write (or replace) one agent's marker atomically.

        The app process reads markers while the worker writes them, so the
        file is written aside and renamed into place: a reader sees the old
        marker or the new one, never half of one.

        Raises:
            OSError: The file cannot be written.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        target = self.path(agent_id)
        partial = target.with_name(f".{target.name}.partial")
        partial.write_text(marker.model_dump_json(), encoding="utf-8")
        os.replace(partial, target)

    def read(self, agent_id: str) -> SessionMarker | None:
        """Return one agent's marker, or ``None`` when it has none.

        Raises:
            SessionMarkerError: The file exists but is unreadable or malformed.
        """
        path = self.path(agent_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SessionMarkerError(f"{path}: {exc}") from exc
        try:
            return SessionMarker.model_validate_json(text)
        except ValidationError as exc:
            raise SessionMarkerError(f"{path}: {exc}") from exc

    def remove(self, agent_id: str) -> None:
        """Delete one agent's marker (its session ended); absent is fine."""
        self.path(agent_id).unlink(missing_ok=True)

    def agents(self) -> list[str]:
        """Return the ids of agents that have a marker."""
        if not self._root.is_dir():
            return []
        return sorted(item.stem for item in self._root.glob("*.json") if item.is_file())

    def clear(self) -> None:
        """Delete every marker (no session outlives its worker).

        Raises:
            OSError: The folder cannot be removed.
        """
        if self._root.is_dir():
            shutil.rmtree(self._root)


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
