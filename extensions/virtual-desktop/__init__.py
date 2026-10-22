"""Virtual Desktop — each agent's own Linux desktop (a Docker container), used by screenshot, mouse and keyboard.

Ownership invariant: only the runtime worker starts, stops, restarts, resets
or removes desktops. The app process creates this extension too, but only to
verify settings (it reads the Docker host's capacity) and to read screenshot
files for the live view: creating it starts nothing and makes no Docker call.

A desktop starts on the agent's first ``vm`` command and runs until the
agent stops, restarts or resets it, the operator turns the agent off or
changes its screen, memory or CPUs (``agent_config_changed``), the agent is
deleted or the worker boots (``reconcile_agents``), or the worker shuts down
(``shutdown``). Its files and programs persist across all but a reset and the
agent's deletion. Loaded by the BossMod extension host (``core.extensions``)
only while enabled or being set up.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.extensions.contract import (
    AgentConfigError,
    ExtensionContext,
    LiveViewFact,
    LiveViewItem,
    SetupStatus,
    WakeBatch,
)
from core.models import Agent

from . import install
from .commands import VirtualDesktopCommands, routed_vision_model
from .desktop_types import DesktopActionError, DockerUnavailable, install_id_for
from .docker_host import DesktopHost
from .idle import IdleTracker
from .screenshots import ShotStore, pid_alive
from .settings import DesktopDefaults, reminder_minutes_from_config, settings_from_config

logger = logging.getLogger(__name__)

_SHOTS_DIRNAME = "shots"
_IMAGE_DIRNAME = "image"
SETTINGS_UNREADABLE = "Its desktop settings can't be read."
IDLE_CHECK_FAILED = "Its desktop idle check failed."


def create(ctx: ExtensionContext) -> "VirtualDesktopExtension":
    """The host's entry point: build the extension for this process (starts nothing).

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
        ValueError: The manifest has no ``agent_config`` block.
    """
    return VirtualDesktopExtension(ctx)


class VirtualDesktopExtension:
    """The ``vm`` command, setup, live view, per-agent settings, idle reminder and lifecycle hooks.

    Args:
        ctx: Manifest, data dir and the agent-settings reader from the host.
        host: The desktops; defaults to a ``DesktopHost`` for this install
            (a fake in tests).
        vision_model: Returns ``(model, image_capable)`` for an agent.
        clock: Monotonic seconds (idle reminder, ``vm wait``).
        sleep: Blocking sleep (settle pauses, ``vm wait``).

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
        ValueError: The manifest has no ``agent_config`` block.
    """

    def __init__(
        self,
        ctx: ExtensionContext,
        *,
        host: DesktopHost | None = None,
        vision_model: Callable[[Agent], tuple[str | None, bool]] = routed_vision_model,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._defaults = DesktopDefaults.model_validate(ctx.manifest.defaults)
        spec = ctx.manifest.agent_config
        if spec is None:
            raise ValueError("the Virtual Desktop manifest has no agent_config block")
        self._spec = spec
        self._data_dir = ctx.data_dir
        self._ready_requires = ctx.manifest.setup.ready_requires
        self._read_agent_config = ctx.read_agent_config
        # Constructing the host makes no Docker call: clients connect on first use.
        self._host = host if host is not None else DesktopHost(
            defaults=self._defaults, install_id=install_id_for(ctx.data_dir), image_tag=self._defaults.image_tag,
        )
        self._shots = ShotStore(ctx.data_dir / _SHOTS_DIRNAME, self._defaults.screenshots_keep)
        self._idle = IdleTracker(clock)
        self._commands = VirtualDesktopCommands(
            defaults=self._defaults,
            host=self._host,
            shots=self._shots,
            idle=self._idle,
            setup_status=self.setup_status,
            read_agent_config=ctx.read_agent_config,
            config_spec=spec,
            vision_model=vision_model,
            clock=clock,
            sleep=sleep,
        )
        # Screenshot folders already warned about as a dead process's (once each).
        self._warned: set[Path] = set()

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``vm`` command (see ``commands.VirtualDesktopCommands.handle``)."""
        return self._commands.handle(ctx, parsed, body)

    def setup_status(self) -> SetupStatus:
        """Return the setup state from the data dir's markers."""
        return install.setup_status(self._data_dir, self._ready_requires)

    def run_setup(self, log_path: Path) -> None:
        """Build the desktop image and prove it starts (blocking; see ``install.run_setup``).

        Raises:
            core.extensions.contract.SetupError: Docker, the build or the test desktop failed.
        """
        install.run_setup(
            self._data_dir,
            log_path,
            host=self._host,
            image_dir=Path(__file__).with_name(_IMAGE_DIRNAME),
            image_version=self._defaults.image_version,
            probe_settings=settings_from_config(self._spec, {}),
        )

    def shutdown(self) -> None:
        """Stop every running desktop of this install, then delete every agent's screenshots.

        The host calls this only in the runtime worker, which is required:
        it stops desktops by label, including ones this process did not start.

        Raises:
            DockerUnavailable, DesktopActionError: Stopping failed (the
                screenshots are deleted anyway).
            OSError: A screenshot folder cannot be removed.
        """
        try:
            self._host.shutdown()
        finally:
            for agent_id in self._shots.agents():
                self._shots.delete(agent_id)

    def prompt_state(self, agent: Agent) -> str:
        """The agent's desktop state for its prompt, from memory (no Docker call)."""
        return self._commands.state_line(agent.id)

    def live_view(self) -> list[LiveViewItem]:
        """Each agent's latest screenshot whose sidecar is readable and whose worker is alive, newest first.

        Leftovers of a dead worker are skipped with a warning (once per
        folder per process) and never deleted here: the app process calls
        this too, and the next worker's boot reconcile clears them.
        """
        items: list[LiveViewItem] = []
        for agent_id in self._shots.agents():
            found = self._shots.latest(agent_id)
            if found is None:
                continue
            path, meta = found
            if not pid_alive(meta.pid):
                if path.parent not in self._warned:
                    self._warned.add(path.parent)
                    logger.warning("Virtual Desktop: ignoring %s, its worker (process %s) is gone", path.parent, meta.pid)
                continue
            items.append(LiveViewItem(
                agent_id=agent_id,
                image_path=path,
                taken_at=datetime.fromisoformat(meta.taken_at),
                command=meta.command,
                url=None,
                title=meta.window,
                facts=[
                    LiveViewFact(label="screen", value=meta.screen),
                    LiveViewFact(label="image", value=meta.image),
                    LiveViewFact(label="pointer", value=meta.pointer),
                ],
            ))
        return sorted(items, key=lambda item: item.taken_at, reverse=True)

    def verify_agent_config(self, values: Mapping[str, str]) -> str:
        """Check the specs against what the Docker host has (app process, on save).

        Returns:
            e.g. ``Ready: 1280×800 screen, up to 2048 MB of 16000 MB and 2 of 4 CPUs.``

        Raises:
            AgentConfigError: Docker cannot be reached, or the specs exceed the host.
        """
        settings = settings_from_config(self._spec, values)
        try:
            capacity = self._host.capacity()
        except (DockerUnavailable, DesktopActionError) as exc:
            raise AgentConfigError(str(exc)) from exc
        if settings.cpus > capacity.cpus:
            noun = "CPU" if capacity.cpus == 1 else "CPUs"
            raise AgentConfigError(f"This computer has {capacity.cpus} {noun}; choose {capacity.cpus} or fewer.")
        if settings.memory_mb > capacity.memory_mb:
            raise AgentConfigError(f"This computer has {capacity.memory_mb} MB of memory; choose {capacity.memory_mb} MB or less.")
        return (
            f"Ready: {settings.width}×{settings.height} screen, up to {settings.memory_mb} MB of "
            f"{capacity.memory_mb} MB and {settings.cpus} of {capacity.cpus} CPUs."
        )

    def agent_config_changed(self, agent_id: str) -> None:
        """Apply one agent's saved or removed settings (runtime worker, under the agent's lock).

        Removed (the agent was turned off): its running desktop stops, disk
        kept, and its screenshots go. Saved: a running desktop restarts only
        when its screen, memory or CPUs differ from what it was started with;
        reminder-only changes and stopped desktops need nothing (the reminder
        is read at each idle check; specs apply at the next start).

        Raises:
            ValueError: The stored settings are corrupt.
            DockerUnavailable, DesktopActionError: The stop or restart failed.
        """
        with self._host.agent_lock(agent_id):
            stored = self._read_agent_config(agent_id)
            if stored is None:
                # Only this worker runs desktops; one it did not start was
                # stopped by the boot reconcile.
                if agent_id in self._host.running_here() and self._host.stop(agent_id):
                    logger.info("Virtual Desktop: stopped agent %s's desktop, it was turned off", agent_id)
                self._shots.delete(agent_id)
                self._idle.forget(agent_id)
                self._commands.forget(agent_id)
                return
            settings = settings_from_config(self._spec, stored)
            applied = self._host.applied(agent_id)
            if applied is None or applied == settings:
                return
            self._host.restart(agent_id, settings)
            self._commands.forget(agent_id)
            self._idle.touch(agent_id)
            logger.info("Virtual Desktop: restarted agent %s's desktop with its new settings", agent_id)

    def poll_wake(self, agent_id: str) -> WakeBatch | None:
        """The power-down reminder when this agent's running desktop has been idle long enough.

        In memory only (no Docker call); ``None`` for a stopped desktop, a
        reminder of 0 minutes, or a stretch already reminded.

        Raises:
            ValueError: The stored settings are corrupt.
        """
        if agent_id not in self._host.running_here():
            return None
        stored = self._read_agent_config(agent_id)
        if stored is None:
            return None
        return self._idle.due(agent_id, reminder_minutes_from_config(self._spec, stored))

    def commit_wake(self, batch: WakeBatch) -> None:
        """The reminder was delivered: this idle stretch is not reminded again."""
        self._idle.mark_reminded(batch.agent_id, batch.cursor)

    def skip_wake(self, agent_id: str) -> None:
        """On vacation: the current idle stretch counts as reminded."""
        self._idle.skip(agent_id)

    def describe_wake_error(self, exc: Exception) -> str:
        """The operator's sentence for a ``poll_wake`` failure (a corrupt settings row is the only expected one)."""
        return SETTINGS_UNREADABLE if isinstance(exc, ValueError) else IDLE_CHECK_FAILED

    def reconcile_agents(self, existing_agent_ids: frozenset[str]) -> None:
        """Drop desktops of deleted agents and stop unowned ones (see ``DesktopHost.reconcile``).

        Then, even if that failed, delete the screenshots of every agent
        without a desktop running in this process.

        Raises:
            DockerUnavailable, DesktopActionError: The reconcile failed.
            OSError: A screenshot folder cannot be removed.
        """
        before = self._host.running_here()
        try:
            self._host.reconcile(existing_agent_ids)
        finally:
            running = self._host.running_here()
            for agent_id in self._shots.agents():
                if agent_id not in running:
                    self._shots.delete(agent_id)
            for agent_id in before - running:
                self._idle.forget(agent_id)
                self._commands.forget(agent_id)
