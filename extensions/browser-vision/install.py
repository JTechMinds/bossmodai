"""Browser Vision — one-click browser download and launch probe.

The browser is fetched by Playwright's own installer into the extension data
dir (``PLAYWRIGHT_BROWSERS_PATH``), never the user's default cache, and never
with sudo. ``chromium-headless-shell`` is installed rather than full Chromium:
it is what Playwright launches for headless Chromium and is the smaller
download.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from core.extensions.contract import READY_FILE, SetupError, SetupStatus
from core.extensions.setup_runner import read_setup_status

BROWSER = "chromium-headless-shell"
_BROWSERS_DIRNAME = "ms-playwright"
_LOG_TAIL_LINES = 25

# Launch, open about:blank, screenshot, close; print the versions as JSON on
# the last line. Run in a child process so the app's own environment and
# event loop are untouched.
_PROBE_SCRIPT = """
import json, sys
from importlib.metadata import version
from playwright.sync_api import sync_playwright
timeout = float(sys.argv[1])
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, timeout=timeout)
    page = browser.new_page()
    page.goto("about:blank", timeout=timeout)
    page.screenshot(timeout=timeout)
    info = {"playwright": version("playwright"), "browser": browser.version}
    browser.close()
print(json.dumps(info))
"""


def browsers_dir(data_dir: Path) -> Path:
    """Return where the browser binaries live inside the data dir."""
    return data_dir / _BROWSERS_DIRNAME


def setup_status(data_dir: Path) -> SetupStatus:
    """Return the setup state read from the data dir's marker files."""
    return read_setup_status(data_dir, required=True)


def run_setup(data_dir: Path, log_path: Path, *, probe_timeout_ms: int) -> None:
    """Download the browser, prove it launches, then mark setup ready.

    Blocking. Output from both steps is appended to ``log_path``.
    ``ready.json`` is written only after the launch probe succeeds.

    Args:
        data_dir: The extension data dir (created if missing).
        log_path: The setup log.
        probe_timeout_ms: Launch/navigation timeout for the probe.

    Raises:
        SetupError: The installer exited non-zero, or the probe failed
            (on Linux its message names any missing shared library). The
            message ends with the log tail.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / READY_FILE).unlink(missing_ok=True)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir(data_dir))}

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"$ playwright install {BROWSER}\n")
        log.flush()
        installed = subprocess.run(
            [sys.executable, "-m", "playwright", "install", BROWSER],
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
        [sys.executable, "-c", _PROBE_SCRIPT, str(probe_timeout_ms)],
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
    (data_dir / READY_FILE).write_text(json.dumps(versions), encoding="utf-8")


def _tail(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-_LOG_TAIL_LINES:])
