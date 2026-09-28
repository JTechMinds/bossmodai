"""Browser Vision — where screenshots are kept, per browser session, with pruning.

Screenshots belong to the browser session that took them and are deleted
when it ends, so nothing (the model's context, the operator's live view)
can show an old page as if the browser were still on it.

Each PNG has a JSON sidecar with the same stem describing what produced it
(the command, page, view line, image size and mark count). The live view reads these
files directly, so nothing about screenshots lives in memory or the DB.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShotMeta:
    """What produced one screenshot, as the agent's result text described it.

    Attributes:
        command: The ``bv`` command as the agent typed it.
        url: Page URL at capture time.
        title: Page title at capture time.
        window: The window line, e.g. ``desktop 1280x800``.
        view: The result's view line (``view: full page`` or the zoom path).
        image: The image size sent to the model, ``WxH``.
        marks: How many element marks the screenshot has.
        taken_at: ISO-8601 UTC capture time.
    """

    command: str
    url: str
    title: str
    window: str
    view: str
    image: str
    marks: int
    taken_at: str


class ScreenshotStore:
    """Writes screenshots to ``<root>/<agent_id>/<session_id>/<utc-ts>.png``.

    Pruning keeps the newest few inside one session's folder. Only the
    newest screenshot in a model's context is ever sent as an image; older
    files are kept so a resumed turn can still resolve its latest one while
    the session lives.

    Args:
        root: The shots folder (inside the extension data dir).
        keep: How many screenshots to keep per session (at least 1).

    Raises:
        ValueError: ``keep`` is below 1 — the file just stored would be pruned.
    """

    def __init__(self, root: Path, keep: int) -> None:
        if keep < 1:
            raise ValueError("screenshots_keep must be at least 1")
        self._root = root
        self._keep = keep

    @property
    def root(self) -> Path:
        """The shots folder."""
        return self._root

    def session_dir(self, agent_id: str, session_id: str) -> Path:
        """The folder one session's screenshots live in (not created here)."""
        return self._root / agent_id / session_id

    def store(self, agent_id: str, session_id: str, png: bytes, meta: ShotMeta) -> Path:
        """Write one screenshot and its sidecar, prune the session's folder, return the PNG.

        The sidecar is written first, so a PNG that exists always has one
        unless something outside this store removed it.

        Raises:
            OSError: A file cannot be written.
        """
        folder = self.session_dir(agent_id, session_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = folder / f"{stamp}.png"
        suffix = 1
        while path.exists():
            path = folder / f"{stamp}_{suffix}.png"
            suffix += 1
        path.with_suffix(".json").write_text(json.dumps(asdict(meta)), encoding="utf-8")
        path.write_bytes(png)
        self.prune(agent_id, session_id)
        return path

    def prune(self, agent_id: str, session_id: str) -> None:
        """Delete all but the newest ``keep`` screenshots of one session, with their sidecars."""
        folder = self.session_dir(agent_id, session_id)
        if not folder.is_dir():
            return
        shots = sorted(folder.glob("*.png"), key=lambda item: item.name)
        for old in shots[: max(0, len(shots) - self._keep)]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)

    def latest(self, agent_id: str, session_id: str) -> tuple[Path, ShotMeta] | None:
        """Return the session's newest screenshot that has a readable sidecar.

        A PNG whose sidecar is missing or corrupt is skipped with a warning
        rather than raised, so one bad file cannot blank the live view; the
        next older screenshot is returned instead.

        Returns:
            ``(png path, meta)``, or ``None`` when the session has none.
        """
        folder = self.session_dir(agent_id, session_id)
        if not folder.is_dir():
            return None
        for png in sorted(folder.glob("*.png"), key=lambda item: item.name, reverse=True):
            sidecar = png.with_suffix(".json")
            try:
                meta = ShotMeta(**json.loads(sidecar.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Browser Vision: skipping %s, its sidecar is unreadable (%s)", png, exc)
                continue
            return png, meta
        return None

    def delete_session(self, agent_id: str, session_id: str) -> None:
        """Delete one ended session's folder, and the agent's folder once it is empty.

        Raises:
            OSError: A file cannot be removed.
        """
        folder = self.session_dir(agent_id, session_id)
        if folder.is_dir():
            shutil.rmtree(folder)
        agent_dir = self._root / agent_id
        if agent_dir.is_dir() and not any(agent_dir.iterdir()):
            agent_dir.rmdir()

    def clear(self) -> None:
        """Delete every agent's session folders (no session outlives its worker).

        Raises:
            OSError: A file cannot be removed.
        """
        if not self._root.is_dir():
            return
        for agent_dir in self._root.iterdir():
            if agent_dir.is_dir():
                shutil.rmtree(agent_dir)
