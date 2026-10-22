"""Test helper: Virtual Desktop without Docker or the network.

``FakeHost`` is a ``DesktopHostLike`` for the commands and the extension.
``FakeDaemon`` is the engine behind every fake SDK client, so ``DesktopHost``
and ``DockerAccess`` run for real against it, with every call recorded.
"""

from __future__ import annotations

import importlib
import io
import json
import shlex
import threading
from pathlib import Path
from typing import Any

import docker.errors
from docker.models.containers import ExecResult
from PIL import Image

import db
from core.bm_cli.types import CliExecutionContext, ParsedCliCommand
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

ENTRY = get_discovery().get("virtual-desktop")
PACKAGE = import_package(ENTRY)


def module(name: str) -> Any:
    """One of the extension's modules, e.g. ``module("commands")``."""
    return importlib.import_module(f"{PACKAGE.__name__}.{name}")


types = module("desktop_types")
settings_module = module("settings")
DEFAULT_SETTINGS = settings_module.settings_from_config(ENTRY.manifest.agent_config, {})


def png(width: int, height: int, shade: int = 250) -> bytes:
    """A plain PNG of the given size."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (shade, shade, shade)).save(buffer, format="PNG")
    return buffer.getvalue()


def cli_runner(extension_or_commands: Any, agent: Any):
    """Return ``run(raw, body=None)`` that sends one CLI line to ``handle`` for ``agent``."""

    def run(raw: str, body: str | None = None):
        tokens = shlex.split(raw)
        ctx = CliExecutionContext(agent=agent, state=db.get_agent_state(agent.id), cwd="/me")
        return extension_or_commands.handle(ctx, ParsedCliCommand(raw=raw, name=tokens[0], args=tuple(tokens[1:])), body)

    return run


class FakeHost:
    """A ``DesktopHostLike`` that records calls; screenshots are plain PNGs of the screen size.

    Set ``fail[<method>] = exc`` to make the next call of that method raise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.lock = threading.RLock()
        self.started: set[str] = set()
        self.settings: dict[str, Any] = {}
        self.recreate_next = False
        self.boot_seconds = 6.1
        self.pointer = (640, 400)
        self.pointer_window = "Desktop"
        self.active_window = ""
        # Successive capture shades (for vm wait); the last one repeats.
        self.shades = [250]
        self.run_outcome = types.RunOutcome(exit_code=0, stdout=b"", stderr=b"", stdout_size=0, stderr_size=0)
        self.archive: bytes = b""
        self.uploads: list[tuple[str, bytes]] = []
        self.missing: set[str] = set()
        self.capacity_value = types.HostCapacity(cpus=4, memory_mb=16000, server_version="29.8.1")
        self.fail: dict[str, Exception] = {}

    def _maybe_fail(self, name: str) -> None:
        exc = self.fail.pop(name, None)
        if exc is not None:
            raise exc

    def agent_lock(self, agent_id):
        return self.lock

    def ensure_running(self, agent_id, settings):
        self.calls.append(("ensure_running", agent_id, settings))
        self._maybe_fail("ensure_running")
        if agent_id in self.started:
            return types.BootReport(started=False, recreated=False, seconds=0.0)
        self.started.add(agent_id)
        self.settings[agent_id] = settings
        recreated, self.recreate_next = self.recreate_next, False
        return types.BootReport(started=True, recreated=recreated, seconds=self.boot_seconds)

    def restart(self, agent_id, settings):
        self.calls.append(("restart", agent_id, settings))
        self._maybe_fail("restart")
        self.started.add(agent_id)
        self.settings[agent_id] = settings
        return types.BootReport(started=True, recreated=False, seconds=3.2)

    def stop(self, agent_id):
        self.calls.append(("stop", agent_id))
        self._maybe_fail("stop")
        was = agent_id in self.started
        self.started.discard(agent_id)
        self.settings.pop(agent_id, None)
        return was

    def applied(self, agent_id):
        return self.settings.get(agent_id)

    def reset(self, agent_id, settings):
        self.calls.append(("reset", agent_id, settings))
        self.started.add(agent_id)
        self.settings[agent_id] = settings
        return types.BootReport(started=True, recreated=False, seconds=7.0)

    def running_here(self):
        return frozenset(self.started)

    def capture(self, agent_id):
        self.calls.append(("capture", agent_id))
        self._maybe_fail("capture")
        current = self.settings[agent_id]
        shade = self.shades.pop(0) if len(self.shades) > 1 else self.shades[0]
        return types.Capture(
            png=png(current.width, current.height, shade),
            screen=(current.width, current.height),
            pointer=self.pointer,
            pointer_window=self.pointer_window,
            active_window=self.active_window,
        )

    def xdotool(self, agent_id, args):
        self.calls.append(("xdotool", list(args)))
        self._maybe_fail("xdotool")

    def run(self, agent_id, command, timeout_s):
        self.calls.append(("run", command, timeout_s))
        return self.run_outcome

    def launch(self, agent_id, command):
        self.calls.append(("launch", command))

    def put_archive(self, agent_id, dest_dir, tar_bytes_file):
        self.calls.append(("put_archive", dest_dir))
        if dest_dir in self.missing:
            raise types.DesktopPathMissing(f"{dest_dir} does not exist on the desktop")
        self.uploads.append((dest_dir, tar_bytes_file.read()))

    def get_archive(self, agent_id, path):
        self.calls.append(("get_archive", path))
        if path in self.missing:
            raise types.DesktopPathMissing(f"{path} does not exist on the desktop")
        return iter([self.archive]), {"name": Path(path).name}

    def capacity(self):
        self.calls.append(("capacity",))
        self._maybe_fail("capacity")
        return self.capacity_value

    def reconcile(self, existing_agent_ids):
        self.calls.append(("reconcile", existing_agent_ids))
        self._maybe_fail("reconcile")
        for agent_id in list(self.started):
            if agent_id not in existing_agent_ids:
                self.started.discard(agent_id)
                self.settings.pop(agent_id, None)

    def shutdown(self):
        self.calls.append(("shutdown",))
        self.started.clear()
        self.settings.clear()

    def names(self) -> list[str]:
        """The called method names, in order."""
        return [call[0] for call in self.calls]


# ── a fake Docker engine for DesktopHost / DockerAccess ──────────────────


class FakeContainer:
    """One container: records its calls; helpers answer from the daemon's script."""

    def __init__(self, daemon: "FakeDaemon", name: str, kwargs: dict[str, Any]) -> None:
        self.daemon = daemon
        self.name = name
        self.kwargs = kwargs
        self.labels = dict(kwargs.get("labels") or {})
        self.status = "created"
        self.calls: list[tuple] = []

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def put_archive(self, path, data):
        self.calls.append(("put_archive", path, data if isinstance(data, bytes) else data.read()))
        if path in self.daemon.missing_paths:
            raise docker.errors.NotFound(f"{path} not found")
        return True

    def start(self):
        self.calls.append(("start",))
        self.status = "running"

    def stop(self, timeout=None):
        self.calls.append(("stop", timeout))
        self.status = "exited"

    def remove(self, force=False):
        self.calls.append(("remove", force))
        self.daemon.removed.append(self.name)
        self.daemon.containers.pop(self.name, None)

    def reload(self):
        self.calls.append(("reload",))

    def logs(self, tail=None):
        return b"bm-desktop-start: something went wrong"

    def exec_run(self, cmd, **kwargs):
        self.calls.append(("exec_run", list(cmd), kwargs))
        if self.status != "running":
            raise docker.errors.APIError(f"container {self.name} is not running")
        return self.daemon.answer(self, list(cmd), kwargs)

    def get_archive(self, path):
        self.calls.append(("get_archive", path))
        if path in self.daemon.missing_paths:
            raise docker.errors.NotFound(f"{path} not found")
        return iter([b"tar-bytes"]), {"name": path}


class FakeVolume:
    def __init__(self, daemon: "FakeDaemon", name: str, labels: dict[str, str]) -> None:
        self.daemon = daemon
        self.name = name
        self.attrs = {"Labels": labels}

    def remove(self, force=False):
        self.daemon.volumes.pop(self.name, None)
        self.daemon.removed_volumes.append(self.name)


class _Containers:
    def __init__(self, daemon: "FakeDaemon") -> None:
        self._daemon = daemon

    def get(self, name):
        self._daemon.log.append(("containers.get", name))
        container = self._daemon.containers.get(name)
        if container is None:
            raise docker.errors.NotFound(f"No such container: {name}")
        return container

    def create(self, image=None, **kwargs):
        self._daemon.log.append(("containers.create", image, kwargs))
        if image not in self._daemon.images:
            raise docker.errors.ImageNotFound(f"No such image: {image}")
        container = FakeContainer(self._daemon, kwargs["name"], kwargs)
        self._daemon.containers[kwargs["name"]] = container
        return container

    def list(self, all=False, filters=None, ignore_removed=False):
        self._daemon.log.append(("containers.list", all, filters, ignore_removed))
        return [
            item for item in self._daemon.containers.values()
            if labelled(item.labels, filters) and (all or item.status == "running")
        ]


class _Volumes:
    def __init__(self, daemon: "FakeDaemon") -> None:
        self._daemon = daemon

    def get(self, name):
        volume = self._daemon.volumes.get(name)
        if volume is None:
            raise docker.errors.NotFound(f"No such volume: {name}")
        return volume

    def create(self, name=None, labels=None):
        self._daemon.log.append(("volumes.create", name, labels))
        self._daemon.volumes[name] = FakeVolume(self._daemon, name, labels or {})
        return self._daemon.volumes[name]

    def list(self, filters=None):
        self._daemon.log.append(("volumes.list", filters))
        return [item for item in self._daemon.volumes.values() if labelled(item.attrs["Labels"], filters)]


class _Api:
    def __init__(self, daemon: "FakeDaemon") -> None:
        self._daemon = daemon

    def build(self, **kwargs):
        self._daemon.log.append(("build", kwargs))
        yield from self._daemon.build_chunks


class FakeClient:
    """One SDK client; all of them share one daemon."""

    def __init__(self, daemon: "FakeDaemon") -> None:
        self._daemon = daemon
        self.containers = _Containers(daemon)
        self.volumes = _Volumes(daemon)
        self.api = _Api(daemon)
        self.closed = False

    def ping(self):
        return True

    def info(self):
        return dict(self._daemon.info)

    def close(self):
        self.closed = True


class FakeDaemon:
    """The engine every ``FakeClient`` talks to.

    Attributes:
        ready_after: How many ``bm-ready`` calls fail before one succeeds
            (``None``: it never gets ready).
    """

    def __init__(self) -> None:
        self.containers: dict[str, FakeContainer] = {}
        self.volumes: dict[str, FakeVolume] = {}
        self.images = {"bossmod/virtual-desktop:1"}
        self.removed: list[str] = []
        self.removed_volumes: list[str] = []
        self.missing_paths: set[str] = set()
        self.log: list[tuple] = []
        self.clients: list[FakeClient] = []
        self.ready_after: int | None = 0
        self.ready_calls = 0
        self.info = {"NCPU": 4, "MemTotal": 16 * 1024 * 1024 * 1024, "ServerVersion": "29.8.1"}
        self.build_chunks: list[dict[str, str]] = [{"stream": "Step 1/2 : FROM debian\n"}, {"stream": "Successfully built 1\n"}]
        self.state = {"screen": [1280, 800], "pointer": [640, 400], "pointer_window": "Desktop", "active_window": ""}
        # vm run: what the command exits with and writes to its two files,
        # what the outer shell says on its own stderr, and the run folders made.
        self.run_exit = 3
        self.run_stdout = b"out"
        self.run_stderr = b"err"
        self.run_setup_error: bytes | None = None
        self.run_folders: list[str] = []

    def factory(self) -> FakeClient:
        """The ``client_factory`` to hand ``DesktopHost``."""
        client = FakeClient(self)
        self.clients.append(client)
        return client

    def answer(self, container: FakeContainer, cmd: list[str], kwargs: dict[str, Any]) -> ExecResult:
        if cmd[0] == "bm-ready":
            self.ready_calls += 1
            ready = self.ready_after is not None and self.ready_calls > self.ready_after
            return ExecResult(0 if ready else 1, (b"", b"" if ready else b"Can't open display"))
        if cmd[0] == "bm-capture":
            return ExecResult(0, (png(*self.state["screen"]), None))
        if cmd[0] == "bm-state":
            return ExecResult(0, (json.dumps(self.state).encode("utf-8"), None))
        if cmd[0] == "mkdir":
            self.run_folders.append(cmd[-1])
            return ExecResult(0, (None, None))
        if cmd[:3] == ["bash", "-c", types.RUN_SCRIPT]:
            return ExecResult(1 if self.run_setup_error else self.run_exit, (None, self.run_setup_error))
        if cmd[:3] == ["bash", "-c", types.READ_SCRIPT]:
            limit = int(cmd[5])
            header = f"{len(self.run_stdout)} {len(self.run_stderr)}\n".encode("ascii")
            return ExecResult(0, (header + self.run_stdout[:limit] + self.run_stderr[:limit], None))
        return ExecResult(0, (None, None))

    def add_container(self, name: str, labels: dict[str, str], status: str) -> FakeContainer:
        """A container that already exists (e.g. left by an earlier worker)."""
        container = FakeContainer(self, name, {"labels": labels, "name": name})
        container.status = status
        self.containers[name] = container
        return container

    def add_volume(self, name: str, labels: dict[str, str]) -> FakeVolume:
        self.volumes[name] = FakeVolume(self, name, labels)
        return self.volumes[name]


def labelled(labels: dict[str, str], filters: dict[str, list[str]] | None) -> bool:
    """Whether ``labels`` has every ``key=value`` of a Docker label filter."""
    wanted = dict(item.split("=", 1) for item in (filters or {}).get("label", []))
    return all(labels.get(key) == value for key, value in wanted.items())
