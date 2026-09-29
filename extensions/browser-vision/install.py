"""Browser Vision — one-click browser download and launch probe.

The browser is fetched by Playwright's own installer into the extension data
dir (``PLAYWRIGHT_BROWSERS_PATH``), never the user's default cache, and never
with sudo. Full Chromium is installed (``--no-shell``: not the stripped
``chromium-headless-shell``, which renders differently and is widely treated
as a bot) and launched with ``channel="chromium"``, which runs it in Chrome's
new headless mode: the same program as windowed Chrome, off-screen.
Automation signals are left as they are.

``ready.json`` records ``browser_kind``; the manifest's
``setup.ready_requires`` demands it, so an install from before full Chromium
reads as out of date and the operator is offered setup again. After a
successful setup, the old headless-shell folders in this extension's own
browsers dir are deleted.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from core.extensions.contract import READY_FILE, SetupError, SetupStatus
from core.extensions.setup_runner import read_setup_status

logger = logging.getLogger(__name__)

# What setup installs and BrowserHost launches: Playwright's full Chromium.
BROWSER = "chromium"
BROWSER_CHANNEL = "chromium"
BROWSER_KIND = "chromium"
_INSTALL_ARGS = ("install", "--no-shell", BROWSER)
# The headless-shell installs this setup replaces (Playwright's folder names).
_OLD_SHELL_GLOB = "chromium_headless_shell-*"
_BROWSERS_DIRNAME = "ms-playwright"
_LOG_TAIL_LINES = 25

# Launch, open about:blank, wait for a rendered frame and the settle pause,
# screenshot, close; print the versions as JSON on the last line. The wait
# mirrors BrowserHost, which never captures right after a navigation: on full
# Chromium the first screenshot straight after a fresh launch can fail
# ("Unable to capture screenshot"), which says nothing about the install.
# Run in a child process so the app's own environment and event loop are
# untouched. Arguments: timeout ms, channel, settle ms.
_PROBE_SCRIPT = """
import json, sys, time
from importlib.metadata import version
from playwright.sync_api import sync_playwright
timeout = float(sys.argv[1])
settle_s = float(sys.argv[3]) / 1000
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel=sys.argv[2], timeout=timeout)
    page = browser.new_page()
    page.goto("about:blank", timeout=timeout)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    time.sleep(settle_s)
    page.screenshot(timeout=timeout)
    info = {"playwright": version("playwright"), "browser": browser.version}
    browser.close()
print(json.dumps(info))
"""


def browsers_dir(data_dir: Path) -> Path:
    """Return where the browser binaries live inside the data dir."""
    return data_dir / _BROWSERS_DIRNAME


def setup_status(data_dir: Path, ready_requires: Mapping[str, str]) -> SetupStatus:
    """Return the setup state read from the data dir's marker files.

    Uses the host's own rule (``read_setup_status``) with the manifest's
    ``setup.ready_requires``, so this and the Extensions card never disagree.

    Raises:
        OSError: ``ready.json`` exists but cannot be read.
    """
    return read_setup_status(data_dir, required=True, ready_requires=ready_requires)


def run_setup(data_dir: Path, log_path: Path, *, probe_timeout_ms: int, settle_ms: int) -> None:
    """Download the browser, prove it launches, then mark setup ready.

    Blocking. Output from both steps is appended to ``log_path``.
    ``ready.json`` (versions plus ``browser_kind``) is written only after
    the launch probe succeeds; only then are old headless-shell folders
    under this data dir's browsers dir deleted (and logged).

    Args:
        data_dir: The extension data dir (created if missing).
        log_path: The setup log.
        probe_timeout_ms: Launch/navigation timeout for the probe.
        settle_ms: The pause the probe takes before its screenshot, as
            ``BrowserHost`` does after an action (the manifest's ``settle_ms``).

    Raises:
        SetupError: The installer exited non-zero, or the probe failed
            (on Linux its message names any missing shared library). The
            message ends with the log tail.
        OSError: An old headless-shell folder could not be deleted.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / READY_FILE).unlink(missing_ok=True)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir(data_dir))}

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"$ playwright {' '.join(_INSTALL_ARGS)}\n")
        log.flush()
        installed = subprocess.run(
            [sys.executable, "-m", "playwright", *_INSTALL_ARGS],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    if installed.returncode != 0:
        raise SetupError(
            f"Browser download failed (exit {installed.returncode}).\n{_tail(log_path)}"
        )

    probe = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT, str(probe_timeout_ms), BROWSER_CHANNEL, str(settle_ms)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ launch probe\n")
        log.write(probe.stdout)
        log.write(probe.stderr)
    if probe.returncode != 0:
        raise SetupError(f"The browser was downloaded but could not start.\n{_tail(log_path)}")
    lines = probe.stdout.strip().splitlines()
    try:
        versions = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        versions = None
    if not isinstance(versions, dict):
        raise SetupError(f"The launch probe did not report its versions.\n{_tail(log_path)}")
    (data_dir / READY_FILE).write_text(json.dumps({**versions, "browser_kind": BROWSER_KIND}), encoding="utf-8")
    _remove_old_shells(browsers_dir(data_dir), log_path)


def _remove_old_shells(browsers: Path, log_path: Path) -> None:
    """Delete headless-shell folders directly inside ``browsers`` (this extension's own dir only)."""
    for folder in sorted(browsers.glob(_OLD_SHELL_GLOB)):
        if not folder.is_dir():
            continue
        shutil.rmtree(folder)
        logger.info("Browser Vision setup: removed the old headless shell %s", folder)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"removed old headless shell {folder}\n")


def _tail(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-_LOG_TAIL_LINES:])
