"""Browser Vision — where screenshots are kept, per agent, with pruning.

Each PNG has a JSON sidecar with the same stem describing what produced it
(the command, page, grid line and image size). The live view reads these
files directly, so nothing about screenshots lives in memory or the DB.
"""

from __future__ import annotations

import json
import logging
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
        grid: The same text as the result's grid line.
        image: The image size sent to the model, ``WxH``.
        focus: The focus line when zoomed, else ``None``.
        taken_at: ISO-8601 UTC capture time.
    """

    command: str
    url: str
    title: str
    window: str
    grid: str
    image: str
    focus: str | None
    taken_at: str


class ScreenshotStore:
    """Writes screenshots to ``<root>/<agent_id>/<utc-ts>.png`` and keeps the newest few.

    Only the newest screenshot in a model's context is ever sent as an image;
    older files are kept so a resumed turn can still resolve its latest one.

    Args:
        root: The shots folder (inside the extension data dir).
        keep: How many screenshots to keep per agent (at least 1).

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

    def store(self, agent_id: str, png: bytes, meta: ShotMeta) -> Path:
        """Write one screenshot and its sidecar, prune the agent's folder, return the PNG.

        The sidecar is written first, so a PNG that exists always has one
        unless something outside this store removed it.

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
        path.with_suffix(".json").write_text(json.dumps(asdict(meta)), encoding="utf-8")
        path.write_bytes(png)
        self.prune(agent_id)
        return path

    def prune(self, agent_id: str) -> None:
        """Delete all but the newest ``keep`` screenshots of one agent, with their sidecars."""
        folder = self._root / agent_id
        if not folder.is_dir():
            return
        shots = sorted(folder.glob("*.png"), key=lambda item: item.name)
        for old in shots[: max(0, len(shots) - self._keep)]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)

    def latest(self, agent_id: str) -> tuple[Path, ShotMeta] | None:
        """Return the agent's newest screenshot that has a readable sidecar.

        A PNG whose sidecar is missing or corrupt is skipped with a warning
        rather than raised, so one bad file cannot blank the live view; the
        next older screenshot is returned instead.

        Returns:
            ``(png path, meta)``, or ``None`` when the agent has none.
        """
        folder = self._root / agent_id
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

    def agents(self) -> list[str]:
        """Return the ids of agents that have a screenshot folder."""
        if not self._root.is_dir():
            return []
        return sorted(item.name for item in self._root.iterdir() if item.is_dir())
