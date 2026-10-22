"""Virtual Desktop — this install's Docker objects, through the Docker SDK.

The only module that imports ``docker`` (and ``requests``, its transport).
``DesktopHost`` (``docker_host``) decides what runs; this module is how it
talks to Docker:

- concurrency: the SDK's ``requests`` session is not documented as
  thread-safe, so every agent has its own client, used only while holding
  that agent's re-entrant lock, and host-wide calls use another client under
  the host lock;
- naming and labels: ``bossmod-desktop-<install>-<agent_id>`` and its home
  volume ``bossmod-desktop-home-<install>-<agent_id>``, labelled with the
  extension, install and agent;
- container-level steps (boot, helpers, exec, file copies, image build);
- error mapping: SDK failures become ``DockerUnavailable`` (cannot reach
  Docker) or ``DesktopActionError`` naming what was being done.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

import docker
import docker.errors
import requests
from docker import DockerClient

from .desktop_types import (
    ENV_DIR,
    EXEC_ENV,
    HOME,
    HOSTNAME,
    UNAVAILABLE,
    USER,
    Capture,
    DesktopActionError,
    DesktopPathMissing,
    DockerUnavailable,
    ImageBuildError,
    READ_SCRIPT,
    RUN_ROOT,
    RUN_SCRIPT,
    RunOutcome,
    env_tar,
    limits,
    parse_capture,
    parse_run_output,
)
from .settings import DesktopDefaults, DesktopSettings

logger = logging.getLogger(__name__)

LABEL_EXTENSION = "bossmod.extension"
LABEL_INSTALL = "bossmod.install"
LABEL_AGENT = "bossmod.agent_id"
LABEL_IMAGE_VERSION = "bossmod.image_version"
EXTENSION_LABEL = "virtual-desktop"
_READY_POLL_S = 0.25
# The socket timeout must outlast the longest blocking call, vm run (its own
# `timeout` ends the command at run_timeout_s); this is the slack.
_CLIENT_TIMEOUT_MARGIN_S = 30
_LOG_TAIL_LINES = 15
class DockerAccess:
    """Clients, locks, names and container steps for one install's desktops.

    Args:
        defaults: The validated manifest defaults.
        install_id: This install's id (``desktop_types.install_id_for``).
        image_tag: The image every desktop runs.
        client_factory: Builds a client; by default the local engine, with a
            socket timeout that outlasts ``vm run``.
        clock: Monotonic seconds (the boot wait; injectable for tests).
        sleep: Blocking sleep (the boot wait; injectable for tests).
    """

    def __init__(
        self,
        *,
        defaults: DesktopDefaults,
        install_id: str,
        image_tag: str,
        client_factory: Callable[[], DockerClient] | None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._defaults = defaults
        self._install_id = install_id
        self._image_tag = image_tag
        timeout = defaults.run_timeout_s + _CLIENT_TIMEOUT_MARGIN_S
        self._client_factory = client_factory or (lambda: docker.from_env(timeout=timeout))
        self._clock = clock
        self._sleep = sleep
        self._host_lock = threading.Lock()
        self._host_client: DockerClient | None = None
        # Guards the two dicts; never held during a Docker call.
        self._state_lock = threading.Lock()
        self._agent_locks: dict[str, threading.RLock] = {}
        self._clients: dict[str, DockerClient] = {}

    # ── concurrency and clients ──────────────────────────────────────────

    def agent_lock(self, agent_id: str) -> AbstractContextManager[bool]:
        """Return the agent's re-entrant lock (created on first use)."""
        with self._state_lock:
            lock = self._agent_locks.get(agent_id)
            if lock is None:
                lock = self._agent_locks[agent_id] = threading.RLock()
            return lock

    def known_agents(self) -> frozenset[str]:
        """Every agent this process has used a desktop lock for."""
        with self._state_lock:
            return frozenset(self._agent_locks)

    def client(self, agent_id: str) -> DockerClient:
        """The agent's own client, connected on first use; call with its lock held.

        Raises:
            DockerUnavailable: Docker cannot be reached.
        """
        with self._state_lock:
            client = self._clients.get(agent_id)
        if client is None:
            client = self._connect()
            with self._state_lock:
                self._clients[agent_id] = client
        return client

    @contextmanager
    def host(self, what: str) -> Iterator[DockerClient]:
        """Hold the host lock and yield the host-wide client, mapping failures (see ``mapped``)."""
        with self._host_lock:
            if self._host_client is None:
                self._host_client = self._connect()
            with self.mapped(what, None):
                yield self._host_client

    @contextmanager
    def mapped(self, what: str, agent_id: str | None) -> Iterator[None]:
        """Turn SDK failures inside the block into this extension's errors.

        A lost connection drops the client (``None``: the host client) so
        the next call reconnects.

        Raises:
            DesktopActionError: An API error or a timeout, naming ``what``.
            DockerUnavailable: The connection to Docker failed.
        """
        try:
            yield
        except docker.errors.APIError as exc:
            raise DesktopActionError(f"{what}: {exc.explanation or exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            self.drop_client(agent_id)
            raise DockerUnavailable(UNAVAILABLE) from exc
        except (requests.exceptions.Timeout, TimeoutError) as exc:
            self.drop_client(agent_id)
            raise DesktopActionError(f"{what}: Docker did not answer in time") from exc

    def drop_client(self, agent_id: str | None) -> None:
        """Close and forget a client (``None``: the host client, with the host lock held)."""
        if agent_id is None:
            client, self._host_client = self._host_client, None
        else:
            with self._state_lock:
                client = self._clients.pop(agent_id, None)
        if client is not None:
            client.close()

    def close_all(self) -> None:
        """Close every client; the next call connects again."""
        with self._state_lock:
            clients, self._clients = list(self._clients.values()), {}
        with self._host_lock:
            if self._host_client is not None:
                clients.append(self._host_client)
                self._host_client = None
        for client in clients:
            client.close()

    def _connect(self) -> DockerClient:
        try:
            client = self._client_factory()
            client.ping()
        except (docker.errors.DockerException, requests.exceptions.ConnectionError) as exc:
            logger.warning("Virtual Desktop: cannot reach Docker: %s", exc)
            raise DockerUnavailable(UNAVAILABLE) from exc
        return client

    # ── this install's objects (inside ``mapped``) ───────────────────────

    def container_name(self, agent_id: str) -> str:
        """``bossmod-desktop-<install>-<agent_id>``."""
        return f"bossmod-desktop-{self._install_id}-{agent_id}"

    def volume_name(self, agent_id: str) -> str:
        """``bossmod-desktop-home-<install>-<agent_id>``."""
        return f"bossmod-desktop-home-{self._install_id}-{agent_id}"

    def find(self, client: DockerClient, agent_id: str) -> Any | None:
        """The agent's container, or ``None`` when it has none."""
        try:
            return client.containers.get(self.container_name(agent_id))
        except docker.errors.NotFound:
            return None

    def require(self, client: DockerClient, agent_id: str) -> Any:
        """The agent's container; ``DesktopActionError`` when it has none."""
        container = self.find(client, agent_id)
        if container is None:
            raise DesktopActionError("the desktop does not exist; run vm view to start it")
        return container

    def create(self, client: DockerClient, agent_id: str, settings: DesktopSettings) -> Any:
        """Create the agent's container on the current image, its home on the named volume.

        Raises:
            DesktopActionError: The image is missing (setup must run again).
        """
        try:
            return client.containers.create(
                image=self._image_tag,
                name=self.container_name(agent_id),
                hostname=HOSTNAME,
                labels={**self._agent_labels(agent_id), LABEL_IMAGE_VERSION: self._defaults.image_version},
                volumes={self.volume_name(agent_id): {"bind": HOME, "mode": "rw"}},
                shm_size=f"{self._defaults.shm_size_mb}m",
                init=False,  # tini is the image's entrypoint
                **limits(settings),
            )
        except docker.errors.ImageNotFound as exc:
            raise DesktopActionError(
                f"the desktop image {self._image_tag} is missing; the boss can run its setup again in Add → Extensions"
            ) from exc

    def ensure_volume(self, client: DockerClient, agent_id: str) -> None:
        """Create the agent's home volume when it does not exist."""
        try:
            client.volumes.get(self.volume_name(agent_id))
        except docker.errors.NotFound:
            client.volumes.create(name=self.volume_name(agent_id), labels=self._agent_labels(agent_id))

    def remove_volume(self, client: DockerClient, agent_id: str) -> None:
        """Delete the agent's home volume when it exists."""
        try:
            volume = client.volumes.get(self.volume_name(agent_id))
        except docker.errors.NotFound:
            return
        volume.remove(force=True)

    def install_agents(self, client: DockerClient, *, running_only: bool) -> set[str]:
        """The agents this install has containers for (only running ones), or containers or volumes for."""
        labels = {"label": [f"{LABEL_EXTENSION}={EXTENSION_LABEL}", f"{LABEL_INSTALL}={self._install_id}"]}
        owners = [item.labels.get(LABEL_AGENT) for item in client.containers.list(all=not running_only, filters=labels, ignore_removed=True)]
        if not running_only:
            owners += [(item.attrs.get("Labels") or {}).get(LABEL_AGENT) for item in client.volumes.list(filters=labels)]
        return {owner for owner in owners if owner}

    def _agent_labels(self, agent_id: str) -> dict[str, str]:
        return {LABEL_EXTENSION: EXTENSION_LABEL, LABEL_INSTALL: self._install_id, LABEL_AGENT: agent_id}

    # ── container steps (inside ``mapped``) ──────────────────────────────

    def boot(self, container: Any, settings: DesktopSettings) -> None:
        """Apply limits and screen size (works on a stopped container), start, wait for ``bm-ready``.

        Raises:
            DesktopActionError: The desktop stopped while starting (with its
                log tail) or was not ready within ``boot_timeout_s``.
        """
        container.update(**limits(settings))
        if not container.put_archive(ENV_DIR, env_tar(settings)):
            raise DesktopActionError(f"writing {ENV_DIR} on the desktop failed")
        container.start()
        deadline = self._clock() + self._defaults.boot_timeout_s
        while True:
            try:
                result = container.exec_run(["bm-ready"], user=USER, environment=EXEC_ENV, demux=True)
            except docker.errors.APIError as exc:
                container.reload()
                if container.status != "running":
                    raise DesktopActionError(f"the desktop stopped while starting; its log ends:\n{_log_tail(container)}") from exc
                raise
            if result.exit_code == 0:
                return
            if self._clock() >= deadline:
                last = (result.output[1] or b"").decode("utf-8", errors="replace").strip()
                detail = f" ({last})" if last else ""
                raise DesktopActionError(f"the desktop did not finish starting within {self._defaults.boot_timeout_s}s{detail}")
            self._sleep(_READY_POLL_S)

    def capture(self, container: Any) -> Capture:
        """``bm-capture`` (PNG, pointer drawn in) plus ``bm-state`` (screen, pointer, window titles)."""
        return parse_capture(helper(container, ["bm-capture"]), helper(container, ["bm-state"]))

    def run(self, container: Any, command: str, timeout_s: int) -> RunOutcome:
        """Run ``bash -lc <command>`` as the agent in its home, limited to ``timeout_s``; any exit code is returned.

        Three execs: make a fresh folder under ``RUN_ROOT``; run the command
        with its output going to files there (see ``RUN_SCRIPT``); read the
        start of each file and remove the folder (``READ_SCRIPT``).

        Raises:
            DesktopActionError: A step itself failed (not the command).
        """
        folder = f"{RUN_ROOT}/{uuid.uuid4().hex}"
        max_bytes = self._defaults.run_max_output_bytes
        helper(container, ["mkdir", "-p", "--", folder])
        result = container.exec_run(
            ["bash", "-c", RUN_SCRIPT, "_", str(timeout_s), command, f"{folder}/out", f"{folder}/err"],
            user=USER, environment=EXEC_ENV, workdir=HOME, demux=True,
        )
        # The command's own output is in the files; anything here is the
        # outer shell failing to set them up.
        _, setup_errors = result.output
        if setup_errors:
            raise DesktopActionError(f"running the command failed: {setup_errors.decode('utf-8', errors='replace').strip()}")
        raw = helper(container, ["bash", "-c", READ_SCRIPT, "_", folder, str(max_bytes)])
        return parse_run_output(result.exit_code, raw, max_bytes)

    def launch(self, container: Any, command: str) -> None:
        """Start ``command`` detached (``setsid -f bash -lc``) and return at once."""
        container.exec_run(
            ["setsid", "-f", "bash", "-lc", command], user=USER, environment=EXEC_ENV, workdir=HOME, detach=True,
        )

    def put_archive(self, container: Any, dest_dir: str, data: BinaryIO) -> None:
        """Extract a tar stream into ``dest_dir``; ``DesktopPathMissing`` when that folder does not exist."""
        try:
            copied = container.put_archive(dest_dir, data)
        except docker.errors.NotFound as exc:
            raise DesktopPathMissing(f"{dest_dir} does not exist on the desktop") from exc
        if not copied:
            raise DesktopActionError(f"copying to {dest_dir} on the desktop failed")

    def get_archive(self, container: Any, path: str, agent_id: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        """A tar stream of ``path`` (failures while it is read are mapped too) and its stat.

        Raises:
            DesktopPathMissing: ``path`` does not exist on the desktop.
        """
        try:
            chunks, stat = container.get_archive(path)
        except docker.errors.NotFound as exc:
            raise DesktopPathMissing(f"{path} does not exist on the desktop") from exc
        return self._mapped_chunks(chunks, agent_id), stat

    def build(self, client: DockerClient, image_dir: Path, on_output: Callable[[str], None]) -> None:
        """Build the image from ``image_dir``, pulling the base, passing each output chunk on.

        Raises:
            ImageBuildError: The build reported an error.
        """
        # No HTTP timeout: a large download may be silent for long, and the
        # engine reports a failed build in the stream itself.
        for chunk in client.api.build(path=str(image_dir), tag=self._image_tag, decode=True, pull=True, rm=True, timeout=None):
            if "stream" in chunk:
                on_output(chunk["stream"])
            if "error" in chunk:
                raise ImageBuildError(str(chunk["error"]).strip())

    def create_probe(self, client: DockerClient, name: str, settings: DesktopSettings) -> Any:
        """A throwaway container from the image: no volume, no labels."""
        return client.containers.create(
            image=self._image_tag, name=name, hostname=HOSTNAME,
            shm_size=f"{self._defaults.shm_size_mb}m", init=False, **limits(settings),
        )

    def _mapped_chunks(self, chunks: Iterator[bytes], agent_id: str) -> Iterator[bytes]:
        with self.mapped("copying from the desktop", agent_id):
            yield from chunks


def helper(container: Any, argv: list[str]) -> bytes:
    """Run one helper as the agent; return its stdout, failing on a non-zero exit with its stderr."""
    result = container.exec_run(argv, user=USER, environment=EXEC_ENV, demux=True)
    stdout, stderr = result.output
    if result.exit_code != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip() or "no error output"
        raise DesktopActionError(f"{argv[0]} failed (exit {result.exit_code}): {detail}")
    return stdout or b""


def _log_tail(container: Any) -> str:
    return container.logs(tail=_LOG_TAIL_LINES).decode("utf-8", errors="replace").strip() or "(no output)"
