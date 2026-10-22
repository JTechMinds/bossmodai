"""Virtual Desktop — one-click setup: build the desktop image and prove it starts.

Setup runs in the app process (the host's setup runner). It reaches Docker
only through ``DesktopHost``; the image build context is this extension's
``image/`` folder. ``ready.json`` records ``image_version``, which the
manifest's ``setup.ready_requires`` demands, so after an image-version bump
the card reads "out of date" and offers setup again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from core.extensions.contract import READY_FILE, SetupError, SetupStatus
from core.extensions.setup_runner import read_setup_status

from .desktop_types import DesktopActionError, DockerUnavailable, ImageBuildError
from .docker_host import DesktopHost
from .settings import DesktopSettings

DOCKER_MISSING = (
    "Docker isn't installed or BossMod can't reach it. Install Docker and make sure your user "
    "can run it (on Linux: add yourself to the docker group, then sign out and back in)."
)
_LOG_TAIL_LINES = 25


def setup_status(data_dir: Path, ready_requires: Mapping[str, str]) -> SetupStatus:
    """Return the setup state from the data dir's markers (the host's own rule).

    Raises:
        OSError: ``ready.json`` exists but cannot be read.
    """
    return read_setup_status(data_dir, required=True, ready_requires=ready_requires)


def run_setup(
    data_dir: Path,
    log_path: Path,
    *,
    host: DesktopHost,
    image_dir: Path,
    image_version: str,
    probe_settings: DesktopSettings,
) -> None:
    """Build the desktop image, start one throwaway desktop from it, then mark setup ready.

    Blocking. The build output is appended to ``log_path``. Steps:

    1. reach Docker (``capacity``, which pings it and reads its version);
    2. build the image from ``image_dir`` (pulling the base);
    3. probe: start a throwaway desktop with ``probe_settings``, wait until
       it is ready and capture its screen (a capture that is not a PNG
       fails); the container is always removed;
    4. write ``ready.json``: ``{"image_version": ..., "docker": <engine version>}``.

    Args:
        data_dir: The extension data dir (created if missing).
        log_path: The setup log.
        host: The desktops' Docker access.
        image_dir: The build context.
        image_version: The version ``ready.json`` records.
        probe_settings: The screen and limits the probe starts with (the
            settings' defaults).

    Raises:
        SetupError: Docker is unreachable, the build failed (with the log
            tail), or the image does not start a working desktop.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / READY_FILE).unlink(missing_ok=True)
    try:
        capacity = host.capacity()
    except DockerUnavailable as exc:
        raise SetupError(DOCKER_MISSING) from exc
    except DesktopActionError as exc:
        raise SetupError(f"{DOCKER_MISSING}\n{exc}") from exc

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"Building the desktop image with Docker {capacity.server_version}\n")
        log.flush()

        def append(text: str) -> None:
            log.write(text)
            log.flush()

        try:
            host.build_image(image_dir, append)
        except ImageBuildError as exc:
            raise SetupError(f"{exc}\n{_tail(log_path)}") from exc
        except (DockerUnavailable, DesktopActionError) as exc:
            raise SetupError(f"Building the desktop image failed: {exc}\n{_tail(log_path)}") from exc
        log.write("$ starting a test desktop\n")

    try:
        capture = host.probe(probe_settings)
    except (DockerUnavailable, DesktopActionError) as exc:
        raise SetupError(f"The desktop image was built but a test desktop did not start: {exc}") from exc
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"test desktop ready: screen {capture.screen[0]}x{capture.screen[1]}\n")
    (data_dir / READY_FILE).write_text(
        json.dumps({"image_version": image_version, "docker": capacity.server_version}), encoding="utf-8",
    )


def _tail(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-_LOG_TAIL_LINES:])
