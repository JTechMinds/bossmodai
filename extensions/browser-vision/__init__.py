"""Browser Vision — agents browse websites through screenshots and a numbered grid.

Loaded by the BossMod extension host (``core.extensions``) only when the
extension is enabled or being set up.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.extensions.contract import ExtensionContext, LiveViewItem, SetupStatus
from core.models import Agent

from . import install
from .browser_host import BrowserHost
from .commands import (
    BrowserHostLike,
    BrowserVisionCommands,
    BrowserVisionDefaults,
    agent_downloads_dir,
    routed_vision_model,
)
from .screenshots import ScreenshotStore
from .sessions import SessionMarkerError, SessionMarkers, pid_alive

logger = logging.getLogger(__name__)

_SHOTS_DIRNAME = "shots"
_SESSIONS_DIRNAME = "sessions"


def create(ctx: ExtensionContext) -> "BrowserVisionExtension":
    """The host's entry point: build the extension for this process.

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
    """
    return BrowserVisionExtension(ctx)


class BrowserVisionExtension:
    """The ``bv`` command namespace plus its setup and shutdown.

    Args:
        ctx: Manifest and data dir from the host.
        host: The browser; defaults to a real ``BrowserHost`` over the
            browser setup installed.
        install_dir: Where setup installs the browser and writes its
            markers; defaults to ``ctx.data_dir``. Screenshots always go
            under ``ctx.data_dir``.
        vision_model: Returns ``(model, image_capable)`` for an agent.
        downloads_dir: Returns the real folder behind an agent's ``/me/downloads``.

    In the runtime worker (``ctx.runtime_worker``) construction deletes every
    screenshot and session marker an earlier worker left behind.

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
        OSError: The worker could not delete what an earlier worker left.
    """

    def __init__(
        self,
        ctx: ExtensionContext,
        *,
        host: BrowserHostLike | None = None,
        install_dir: Path | None = None,
        vision_model: Callable[[Agent], tuple[str | None, bool]] = routed_vision_model,
        downloads_dir: Callable[[Agent], Path] = agent_downloads_dir,
    ) -> None:
        self._defaults = BrowserVisionDefaults.model_validate(ctx.manifest.defaults)
        self._install_dir = install_dir if install_dir is not None else ctx.data_dir
        self._host: BrowserHostLike = host if host is not None else BrowserHost(
            browsers_path=install.browsers_dir(self._install_dir),
            nav_timeout_ms=self._defaults.nav_timeout_ms,
            action_timeout_ms=self._defaults.action_timeout_ms,
            download_timeout_ms=self._defaults.download_timeout_ms,
            settle_ms=self._defaults.settle_ms,
            wait_poll_ms=self._defaults.wait_poll_ms,
            wait_settle_ms=self._defaults.wait_settle_ms,
        )
        # Nothing here starts the browser: BrowserHost creates its thread and
        # loop on the first command, so the app process can construct this
        # just to read the live view.
        self._shots = ScreenshotStore(ctx.data_dir / _SHOTS_DIRNAME, self._defaults.screenshots_keep)
        self._markers = SessionMarkers(ctx.data_dir / _SESSIONS_DIRNAME)
        # Marker paths already warned about as stale or unreadable (once each).
        self._warned_markers: set[Path] = set()
        if ctx.runtime_worker:
            # No browser session survives its worker, so anything left is from
            # an earlier worker that may have crashed before its shutdown ran.
            # Never in the app process: it shares this dir with a live worker.
            self._shots.clear()
            self._markers.clear()
        self._commands = BrowserVisionCommands(
            defaults=self._defaults,
            host=self._host,
            shots=self._shots,
            markers=self._markers,
            setup_status=self.setup_status,
            vision_model=vision_model,
            downloads_dir=downloads_dir,
        )

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``bv`` command (see ``commands.BrowserVisionCommands.handle``)."""
        return self._commands.handle(ctx, parsed, body)

    def setup_status(self) -> SetupStatus:
        """Return the browser setup state from the install dir's markers."""
        return install.setup_status(self._install_dir)

    def run_setup(self, log_path: Path) -> None:
        """Download the browser and probe it (blocking).

        Raises:
            core.extensions.contract.SetupError: Download or probe failed.
        """
        install.run_setup(self._install_dir, log_path, probe_timeout_ms=self._defaults.nav_timeout_ms)

    def prompt_state(self, agent: Agent) -> str:
        """Tell the agent where its browser really is (``core.extensions.contract.SupportsPromptState``).

        Raises:
            BrowserActionError: The browser thread exists but did not answer.
        """
        return self._commands.state_line(agent.id)

    def live_view(self) -> list[LiveViewItem]:
        """Return the latest screenshot of each agent with a live session, newest first.

        An agent is listed only when its session marker exists, the process
        that wrote it is alive, and the session's folder has a readable
        screenshot; the image is that folder's latest. A marker whose process
        is dead, or that cannot be read, is stale: it is skipped with a
        warning (once per marker path per process) and never deleted here,
        since the app process calls this too; the next worker clears it.
        """
        items: list[LiveViewItem] = []
        for agent_id in self._markers.agents():
            marker_path = self._markers.path(agent_id)
            try:
                marker = self._markers.read(agent_id)
            except SessionMarkerError as exc:
                self._warn_marker_once(marker_path, f"unreadable session marker ({exc})")
                continue
            if marker is None:
                # Removed between listing and reading: the session just ended.
                continue
            if not pid_alive(marker.pid):
                self._warn_marker_once(marker_path, f"stale session marker: process {marker.pid} is gone")
                continue
            found = self._shots.latest(agent_id, marker.session_id)
            if found is None:
                continue
            path, meta = found
            caption = [f"window: {meta.window}", meta.view, f"image {meta.image}", f"marks: {meta.marks}"]
            items.append(LiveViewItem(
                agent_id=agent_id,
                image_path=path,
                taken_at=datetime.fromisoformat(meta.taken_at),
                command=meta.command,
                url=meta.url,
                title=meta.title,
                caption_lines=caption,
            ))
        return sorted(items, key=lambda item: item.taken_at, reverse=True)

    def _warn_marker_once(self, path: Path, reason: str) -> None:
        if path in self._warned_markers:
            return
        self._warned_markers.add(path)
        logger.warning("Browser Vision: ignoring %s, %s", path, reason)

    def shutdown(self) -> None:
        """End every browser session this process has open (see ``BrowserVisionCommands.shutdown``).

        Raises:
            BrowserActionError: The browser did not close cleanly.
            OSError: A screenshot folder cannot be removed.
        """
        self._commands.shutdown()
