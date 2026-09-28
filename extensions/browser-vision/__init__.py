"""Browser Vision — agents browse websites through screenshots and a numbered grid.

Loaded by the BossMod extension host (``core.extensions``) only when the
extension is enabled or being set up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.extensions.contract import ExtensionContext, SetupStatus
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

_SHOTS_DIRNAME = "shots"


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

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
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
        )
        self._commands = BrowserVisionCommands(
            defaults=self._defaults,
            host=self._host,
            shots=ScreenshotStore(ctx.data_dir / _SHOTS_DIRNAME, self._defaults.screenshots_keep),
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

    def shutdown(self) -> None:
        """Close every agent's browser session and forget their view settings."""
        self._commands.reset()
        self._host.shutdown()
