"""Virtual Desktop — which desktops run, with what, and who owns them.

``DesktopHost`` is the only owner of desktop lifecycles in a process; Docker
itself is reached through ``docker_access.DockerAccess`` (the only module
that imports the SDK). Rules it keeps:

- a desktop starts lazily and keeps running until stopped, restarted,
  reset, reconciled away, or shut down with the worker;
- the settings a running desktop was started with are remembered, so a
  settings save can restart it only when they differ;
- a container whose image version label is out of date is recreated on the
  new image, keeping the home volume;
- a running container this process did not start is an orphan of a dead
  worker: reconcile stops it, and a command restarts it with known settings.

Every method that talks to Docker raises ``DockerUnavailable`` when Docker
cannot be reached and ``DesktopActionError`` when a call fails; nothing is
retried silently. Per-agent methods take the agent's lock themselves.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from .desktop_types import BootReport, Capture, DesktopActionError, DockerUnavailable, HostCapacity, RunOutcome
from .docker_access import LABEL_IMAGE_VERSION, DockerAccess, helper
from .settings import DesktopDefaults, DesktopSettings

logger = logging.getLogger(__name__)


class DesktopHost:
    """Starts, stops, resets and drives the desktop containers of one install.

    Args:
        defaults: The validated manifest defaults.
        install_id: This install's id (``desktop_types.install_id_for``).
        image_tag: The image every desktop runs.
        client_factory: Builds a ``docker.DockerClient`` (a fake in tests);
            by default the local engine.
        clock: Monotonic seconds (injectable for tests).
        sleep: Blocking sleep (injectable for tests).
    """

    def __init__(
        self,
        *,
        defaults: DesktopDefaults,
        install_id: str,
        image_tag: str,
        client_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._defaults = defaults
        self._clock = clock
        self._docker = DockerAccess(
            defaults=defaults, install_id=install_id, image_tag=image_tag,
            client_factory=client_factory, clock=clock, sleep=sleep,
        )
        self._state_lock = threading.Lock()
        # Desktops this process started and still runs, with their settings.
        self._started: set[str] = set()
        self._applied: dict[str, DesktopSettings] = {}

    def agent_lock(self, agent_id: str) -> AbstractContextManager[bool]:
        """Return the agent's re-entrant lock; hold it to keep a sequence of calls together."""
        return self._docker.agent_lock(agent_id)

    def running_here(self) -> frozenset[str]:
        """Return the agents whose desktop this process started and still runs (no Docker call)."""
        with self._state_lock:
            return frozenset(self._started)

    def applied(self, agent_id: str) -> DesktopSettings | None:
        """Return the settings the agent's running desktop was started with, or ``None``."""
        with self._state_lock:
            return self._applied.get(agent_id)

    # ── lifecycle ────────────────────────────────────────────────────────

    def ensure_running(self, agent_id: str, settings: DesktopSettings) -> BootReport:
        """Make sure the agent's desktop runs, creating, recreating or starting it as needed.

        Creates the home volume and container when missing; recreates the
        container (volume kept) when its image version is not
        ``defaults.image_version``; stops and starts again a running orphan.
        Fails when the image is missing or the desktop is not ready within
        ``boot_timeout_s``.
        """
        with self.agent_lock(agent_id):
            began = self._clock()
            client = self._docker.client(agent_id)
            recreated = False
            with self._docker.mapped("starting the desktop", agent_id):
                container = self._docker.find(client, agent_id)
                if container is not None and container.labels.get(LABEL_IMAGE_VERSION) != self._defaults.image_version:
                    container.remove(force=True)
                    container, recreated = None, True
                if container is None:
                    self._docker.ensure_volume(client, agent_id)
                    container = self._docker.create(client, agent_id, settings)
                elif container.status == "running":
                    if agent_id in self.running_here():
                        return BootReport(started=False, recreated=False, seconds=0.0)
                    container.stop(timeout=self._defaults.stop_timeout_s)
                self._boot_tracked(agent_id, container, settings)
            return BootReport(started=True, recreated=recreated, seconds=self._clock() - began)

    def restart(self, agent_id: str, settings: DesktopSettings) -> BootReport:
        """Stop the agent's desktop and start it with ``settings`` (files and programs kept).

        The agent stays in ``running_here`` throughout, so a ``shutdown``
        arriving meanwhile waits for the restart, then stops it.
        """
        with self.agent_lock(agent_id):
            began = self._clock()
            client = self._docker.client(agent_id)
            with self._docker.mapped("restarting the desktop", agent_id):
                container = self._docker.require(client, agent_id)
                container.stop(timeout=self._defaults.stop_timeout_s)
                self._boot_tracked(agent_id, container, settings)
            return BootReport(started=True, recreated=False, seconds=self._clock() - began)

    def stop(self, agent_id: str) -> bool:
        """Power the agent's desktop down, disk kept; return whether it was running."""
        with self.agent_lock(agent_id):
            client = self._docker.client(agent_id)
            with self._docker.mapped("stopping the desktop", agent_id):
                container = self._docker.find(client, agent_id)
                was_running = container is not None and container.status == "running"
                if was_running:
                    container.stop(timeout=self._defaults.stop_timeout_s)
            self._forget(agent_id)
            return was_running

    def reset(self, agent_id: str, settings: DesktopSettings) -> BootReport:
        """Delete the agent's container and home volume, then start a fresh desktop."""
        with self.agent_lock(agent_id):
            self._remove(agent_id, "resetting the desktop")
            return self.ensure_running(agent_id, settings)

    # ── using a running desktop ──────────────────────────────────────────

    def capture(self, agent_id: str) -> Capture:
        """Screenshot the desktop with the real pointer drawn in, plus its state."""
        with self._container(agent_id, "taking a screenshot") as container:
            return self._docker.capture(container)

    def xdotool(self, agent_id: str, args: list[str]) -> None:
        """Run ``xdotool <args>`` on the desktop; a non-zero exit fails with its stderr."""
        with self._container(agent_id, "using the mouse or keyboard") as container:
            helper(container, ["xdotool", *args])

    def run(self, agent_id: str, command: str, timeout_s: int) -> RunOutcome:
        """Run ``bash -lc <command>`` as the agent in its home folder, limited to ``timeout_s``.

        Returns when the command's own shell exits; a program it left
        running in the background (``… &``) keeps running. A non-zero exit
        is the command's own result (124: the time limit stopped it). Each
        stream comes back cut to ``run_max_output_bytes``, with its full size.
        """
        with self._container(agent_id, "running the command") as container:
            return self._docker.run(container, command, timeout_s)

    def launch(self, agent_id: str, command: str) -> None:
        """Start ``command`` detached on the desktop and return at once."""
        with self._container(agent_id, "launching the program") as container:
            self._docker.launch(container, command)

    def put_archive(self, agent_id: str, dest_dir: str, tar_bytes_file: BinaryIO) -> None:
        """Extract a tar stream into ``dest_dir`` on the desktop; owners come from the tar.

        Raises:
            DesktopPathMissing: ``dest_dir`` does not exist on the desktop.
        """
        with self._container(agent_id, "copying to the desktop") as container:
            self._docker.put_archive(container, dest_dir, tar_bytes_file)

    def get_archive(self, agent_id: str, path: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        """Return a tar stream of ``path`` on the desktop and Docker's stat of it.

        Read the stream while holding ``agent_lock``: it uses the agent's client.

        Raises:
            DesktopPathMissing: ``path`` does not exist on the desktop.
        """
        with self._container(agent_id, "copying from the desktop") as container:
            return self._docker.get_archive(container, path, agent_id)

    # ── host-wide ────────────────────────────────────────────────────────

    def capacity(self) -> HostCapacity:
        """Return the Docker host's CPUs, memory (MB) and engine version."""
        with self._docker.host("reading the Docker host's capacity") as client:
            info = client.info()
        try:
            return HostCapacity(
                cpus=int(info["NCPU"]),
                memory_mb=int(info["MemTotal"]) // (1024 * 1024),
                server_version=str(info["ServerVersion"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DesktopActionError(f"Docker's host information is incomplete ({exc!r})") from exc

    def build_image(self, image_dir: Path, on_output: Callable[[str], None]) -> None:
        """Build the desktop image from ``image_dir``, pulling the base; each output chunk goes to ``on_output``.

        Raises:
            ImageBuildError: The build reported an error.
        """
        with self._docker.host("building the desktop image") as client:
            self._docker.build(client, image_dir, on_output)

    def probe(self, settings: DesktopSettings) -> Capture:
        """Start a throwaway desktop from the image (no volume), capture it once; it is removed in ``finally``."""
        with self._docker.host("trying the desktop image") as client:
            container = self._docker.create_probe(client, f"bossmod-desktop-probe-{os.getpid()}", settings)
            try:
                self._docker.boot(container, settings)
                return self._docker.capture(container)
            finally:
                container.remove(force=True)

    def reconcile(self, existing_agent_ids: frozenset[str]) -> None:
        """Remove desktops of agents that no longer exist; stop running ones this process did not start.

        This install's containers and volumes are listed by label; each
        agent's fate is decided under its lock, re-checking ``running_here``
        there, so a desktop a concurrent ``vm`` command just started is never
        stopped. One agent's failure is logged, the rest still run, then it
        is raised. Idempotent.
        """
        with self._docker.host("listing desktops") as client:
            agents = self._docker.install_agents(client, running_only=False)
        self._each_agent(agents, "reconcile", lambda agent_id: self._reconcile_one(agent_id, existing_agent_ids))

    def shutdown(self) -> None:
        """Stop every running desktop of this install, then close the clients.

        The running set is a label query (a crashed worker's leftovers stop
        too) joined with every agent this process worked with. Each stops
        under its agent's lock, so an operation in progress (e.g. a settings
        restart) finishes first and is then stopped: no desktop outlives the
        worker. One failure does not keep the others running.
        """
        with self._docker.host("listing running desktops") as client:
            agents = self._docker.install_agents(client, running_only=True)
        self._each_agent(agents | self.running_here() | self._docker.known_agents(), "shutdown", self._stop_at_shutdown)
        self._docker.close_all()

    # ── internals ────────────────────────────────────────────────────────

    @contextmanager
    def _container(self, agent_id: str, what: str) -> Iterator[Any]:
        """Hold the agent's lock and yield its container, mapping failures."""
        with self.agent_lock(agent_id):
            client = self._docker.client(agent_id)
            with self._docker.mapped(what, agent_id):
                yield self._docker.require(client, agent_id)

    def _boot_tracked(self, agent_id: str, container: Any, settings: DesktopSettings) -> None:
        """Boot, then record the agent as running here with ``settings``."""
        try:
            self._docker.boot(container, settings)
        except Exception:
            # Not usable: the next command treats a container left running
            # as an orphan and boots it again.
            self._forget(agent_id)
            raise
        with self._state_lock:
            self._started.add(agent_id)
            self._applied[agent_id] = settings

    def _remove(self, agent_id: str, what: str) -> None:
        """Delete the agent's container and home volume (call with its lock held)."""
        client = self._docker.client(agent_id)
        with self._docker.mapped(what, agent_id):
            container = self._docker.find(client, agent_id)
            if container is not None:
                container.remove(force=True)
            self._forget(agent_id)
            self._docker.remove_volume(client, agent_id)

    def _reconcile_one(self, agent_id: str, existing_agent_ids: frozenset[str]) -> None:
        with self.agent_lock(agent_id):
            if agent_id not in existing_agent_ids:
                self._remove(agent_id, "removing a deleted agent's desktop")
                self._docker.drop_client(agent_id)
                logger.info("Virtual Desktop: removed the desktop of deleted agent %s", agent_id)
                return
            if agent_id in self.running_here():
                return
            client = self._docker.client(agent_id)
            with self._docker.mapped("stopping an unowned desktop", agent_id):
                container = self._docker.find(client, agent_id)
                if container is not None and container.status == "running":
                    container.stop(timeout=self._defaults.stop_timeout_s)
                    logger.info("Virtual Desktop: stopped agent %s's desktop, which no running worker owns", agent_id)

    def _stop_at_shutdown(self, agent_id: str) -> None:
        if self.stop(agent_id):
            logger.info("Virtual Desktop: stopped agent %s's desktop at shutdown", agent_id)

    def _each_agent(self, agent_ids: frozenset[str] | set[str], what: str, action: Callable[[str], None]) -> None:
        """Run ``action`` per agent; log each failure and carry on, then raise naming them."""
        failed: list[str] = []
        for agent_id in sorted(agent_ids):
            try:
                action(agent_id)
            except (DockerUnavailable, DesktopActionError):
                logger.exception("Virtual Desktop: %s failed for agent %s", what, agent_id)
                failed.append(agent_id)
        if failed:
            raise DesktopActionError(f"{what} failed for the desktops of agents {', '.join(failed)}")

    def _forget(self, agent_id: str) -> None:
        with self._state_lock:
            self._started.discard(agent_id)
            self._applied.pop(agent_id, None)
