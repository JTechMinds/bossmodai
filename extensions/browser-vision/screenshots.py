"""Browser Vision — where screenshots are kept, per agent, with pruning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


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

    def store(self, agent_id: str, png: bytes) -> Path:
        """Write one screenshot, prune the agent's folder, and return the file.

        Raises:
            OSError: The file cannot be written.
        """
        folder = self._root / agent_id
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = folder / f"{stamp}.png"
        suffix = 1
        while path.exists():
            path = folder / f"{stamp}_{suffix}.png"
            suffix += 1
        path.write_bytes(png)
        self.prune(agent_id)
        return path

    def prune(self, agent_id: str) -> None:
        """Delete all but the newest ``keep`` screenshots of one agent."""
        folder = self._root / agent_id
        if not folder.is_dir():
            return
        shots = sorted(folder.glob("*.png"), key=lambda item: item.name)
        for old in shots[: max(0, len(shots) - self._keep)]:
            old.unlink(missing_ok=True)
