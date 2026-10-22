"""Virtual Desktop — the desktops' shared types, errors and pure helpers.

Kept apart from ``docker_host`` (the only module that imports ``docker``),
so the commands, setup and tests use them without the SDK, and
``DesktopHostLike`` lets tests drive the commands with a fake host.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Protocol

from .settings import DesktopSettings

# The desktop's account and display (image/Dockerfile, image/bin/*).
HOME = "/home/agent"
USER = "agent"
HOSTNAME = "desktop"
EXEC_ENV = {"DISPLAY": ":0"}
# Where bm-desktop-start reads the screen size from.
ENV_DIR = "/etc/bossmod"
ENV_FILE = "desktop.env"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
UNAVAILABLE = "Docker isn't running or BossMod can't reach it."
# Docker's CPU quota is per period: cpus × period.
CPU_PERIOD = 100_000

# vm run's output goes to files in a fresh folder here, not through the
# exec's pipes: the exec then returns as soon as the command's own shell
# exits, and a program it left running in the background (`… &`) keeps
# running, writing to a file that is gone once the output was read.
RUN_ROOT = "/tmp/bm-run"
# $1 time limit (s), $2 the command, $3 stdout file, $4 stderr file. The
# exit code is the command's (124: the time limit stopped it).
RUN_SCRIPT = 'timeout "$1" bash -lc "$2" >"$3" 2>"$4"'
# $1 the run folder, $2 how many bytes of each stream to return. Prints
# "<stdout size> <stderr size>\n", then exactly min(size, $2) bytes of each
# (a background program may still be writing to them); always removes the folder.
READ_SCRIPT = (
    'set -eu; trap \'rm -rf -- "$1"\' EXIT; '
    'o=$(stat -c %s -- "$1/out"); e=$(stat -c %s -- "$1/err"); '
    'printf "%s %s\\n" "$o" "$e"; '
    'head -c "$(( o < $2 ? o : $2 ))" -- "$1/out"; '
    'head -c "$(( e < $2 ? e : $2 ))" -- "$1/err"'
)


class DockerUnavailable(Exception):
    """Docker is not running or cannot be reached; the message is a plain sentence."""


class DesktopActionError(Exception):
    """A Docker call or a desktop helper failed; the message names what and why."""


class DesktopPathMissing(DesktopActionError):
    """A path given for a file copy does not exist on the desktop."""


class ImageBuildError(DesktopActionError):
    """The image build reported an error (its text is the message)."""


@dataclass(frozen=True)
class BootReport:
    """What a start, restart or reset did.

    Attributes:
        started: The desktop was started by this call.
        recreated: The container was rebuilt on a newer image (home kept).
        seconds: How long the call took.
    """

    started: bool
    recreated: bool
    seconds: float


@dataclass(frozen=True)
class Capture:
    """One screenshot and the desktop state taken with it (screen pixels).

    Attributes:
        png: The PNG, real pointer drawn in.
        screen: ``(width, height)``.
        pointer: ``(x, y)`` of the real pointer.
        pointer_window: Title of the window under the pointer ("" when none).
        active_window: Title of the focused window ("" when none).
    """

    png: bytes
    screen: tuple[int, int]
    pointer: tuple[int, int]
    pointer_window: str
    active_window: str


@dataclass(frozen=True)
class RunOutcome:
    """A finished ``vm run``.

    Attributes:
        exit_code: The command's exit code (124: its time limit stopped it).
        stdout: The start of its standard output, at most ``run_max_output_bytes``.
        stderr: The start of its error output, at most ``run_max_output_bytes``.
        stdout_size: The full size of its standard output, in bytes.
        stderr_size: The full size of its error output, in bytes.
    """

    exit_code: int
    stdout: bytes
    stderr: bytes
    stdout_size: int
    stderr_size: int


@dataclass(frozen=True)
class HostCapacity:
    """What the Docker host has: CPUs, memory (MB) and the engine version."""

    cpus: int
    memory_mb: int
    server_version: str


class DesktopHostLike(Protocol):
    """What the commands and the extension need from the desktops (``DesktopHost`` or a test fake)."""

    def agent_lock(self, agent_id: str) -> AbstractContextManager[Any]: ...
    def ensure_running(self, agent_id: str, settings: DesktopSettings) -> BootReport: ...
    def restart(self, agent_id: str, settings: DesktopSettings) -> BootReport: ...
    def stop(self, agent_id: str) -> bool: ...
    def applied(self, agent_id: str) -> DesktopSettings | None: ...
    def reset(self, agent_id: str, settings: DesktopSettings) -> BootReport: ...
    def running_here(self) -> frozenset[str]: ...
    def capture(self, agent_id: str) -> Capture: ...
    def xdotool(self, agent_id: str, args: list[str]) -> None: ...
    def run(self, agent_id: str, command: str, timeout_s: int) -> RunOutcome: ...
    def launch(self, agent_id: str, command: str) -> None: ...
    def put_archive(self, agent_id: str, dest_dir: str, tar_bytes_file: BinaryIO) -> None: ...
    def get_archive(self, agent_id: str, path: str) -> tuple[Iterator[bytes], dict[str, Any]]: ...
    def capacity(self) -> HostCapacity: ...
    def reconcile(self, existing_agent_ids: frozenset[str]) -> None: ...
    def shutdown(self) -> None: ...


def install_id_for(data_dir: Path) -> str:
    """Return the 10-hex-char id of one BossMod install, from its extension data dir."""
    return hashlib.sha256(str(data_dir.resolve()).encode("utf-8")).hexdigest()[:10]


def limits(settings: DesktopSettings) -> dict[str, int]:
    """Docker's memory and CPU ceilings; swap equals memory, so the memory cap is the whole cap."""
    memory = settings.memory_mb * 1024 * 1024
    return {"mem_limit": memory, "memswap_limit": memory, "cpu_period": CPU_PERIOD, "cpu_quota": settings.cpus * CPU_PERIOD}


def env_tar(settings: DesktopSettings) -> bytes:
    """A tar holding ``desktop.env`` (the screen size ``bm-desktop-start`` reads)."""
    body = f"SCREEN_WIDTH={settings.width}\nSCREEN_HEIGHT={settings.height}\n".encode("ascii")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        member = tarfile.TarInfo(ENV_FILE)
        member.size = len(body)
        member.mode = 0o644
        tar.addfile(member, io.BytesIO(body))
    return buffer.getvalue()


def parse_capture(png: bytes, state_line: bytes) -> Capture:
    """Combine ``bm-capture``'s PNG and ``bm-state``'s JSON line.

    Raises:
        DesktopActionError: Not a PNG, or the state line is malformed.
    """
    if not png.startswith(PNG_SIGNATURE):
        raise DesktopActionError("bm-capture did not return a PNG image")
    try:
        state = json.loads(state_line)
        screen = (int(state["screen"][0]), int(state["screen"][1]))
        pointer = (int(state["pointer"][0]), int(state["pointer"][1]))
        pointer_window, active_window = state["pointer_window"], state["active_window"]
        if not isinstance(pointer_window, str) or not isinstance(active_window, str):
            raise TypeError("window titles must be strings")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DesktopActionError(f"bm-state printed an unreadable line ({exc}): {state_line[:200]!r}") from exc
    return Capture(png=png, screen=screen, pointer=pointer, pointer_window=pointer_window, active_window=active_window)


def parse_run_output(exit_code: int, raw: bytes, max_bytes: int) -> RunOutcome:
    """Split ``READ_SCRIPT``'s output into the two streams' starts and full sizes.

    Raises:
        DesktopActionError: The output does not have the expected shape.
    """
    header, _, rest = raw.partition(b"\n")
    try:
        out_size, err_size = (int(part) for part in header.split())
    except ValueError as exc:
        raise DesktopActionError(f"reading the command's output failed: unexpected header {header[:80]!r}") from exc
    out_len, err_len = min(out_size, max_bytes), min(err_size, max_bytes)
    if len(rest) != out_len + err_len:
        raise DesktopActionError(f"reading the command's output failed: expected {out_len + err_len} bytes, got {len(rest)}")
    return RunOutcome(
        exit_code=exit_code, stdout=rest[:out_len], stderr=rest[out_len:], stdout_size=out_size, stderr_size=err_size,
    )
