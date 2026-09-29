"""Browser Vision setup: full Chromium, browser_kind, and the old-shell cleanup (R35)."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from core.extensions.contract import ExtensionContext, SetupError
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("browser-vision")
_PACKAGE = import_package(_ENTRY)
_install = importlib.import_module(f"{_PACKAGE.__name__}.install")

_OUT_OF_DATE = {"state": "missing", "detail": "Setup needs to run again: the installed version is out of date."}


class _Runs:
    """Stands in for ``subprocess.run``: records each command, fakes its outcome."""

    def __init__(self, *, probe_ok: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self._probe_ok = probe_ok

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        self.envs.append(kwargs["env"])
        if command[1:3] == ["-m", "playwright"]:
            return subprocess.CompletedProcess(command, 0)
        if self._probe_ok:
            return subprocess.CompletedProcess(command, 0, stdout='{"playwright": "1.63.0", "browser": "153.0"}\n', stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="launch failed\n")


def _with_old_shell(data_dir: Path) -> tuple[Path, Path]:
    browsers = data_dir / "ms-playwright"
    shell = browsers / "chromium_headless_shell-1243"
    (shell / "chrome-headless-shell-linux64").mkdir(parents=True)
    full = browsers / "chromium-1243"
    full.mkdir(parents=True)
    return shell, full


def test_the_manifest_requires_the_kind_setup_writes() -> None:
    assert dict(_ENTRY.manifest.setup.ready_requires) == {"browser_kind": _install.BROWSER_KIND}
    assert _ENTRY.manifest.setup.label == "Download browser (~200 MB)"


def test_setup_installs_full_chromium_without_the_shell_and_probes_its_channel(monkeypatch, tmp_path: Path) -> None:
    runs = _Runs()
    monkeypatch.setattr(_install.subprocess, "run", runs)
    data_dir = tmp_path / "data"
    _install.run_setup(data_dir, tmp_path / "setup.log", probe_timeout_ms=5000, settle_ms=500)

    install_cmd, probe_cmd = runs.commands
    assert install_cmd == [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"]
    assert probe_cmd[1] == "-c" and probe_cmd[3:] == ["5000", "chromium", "500"]
    assert all(env["PLAYWRIGHT_BROWSERS_PATH"] == str(data_dir / "ms-playwright") for env in runs.envs)
    assert json.loads((data_dir / "ready.json").read_text(encoding="utf-8")) == {
        "playwright": "1.63.0", "browser": "153.0", "browser_kind": "chromium",
    }
    assert _install.setup_status(data_dir, _ENTRY.manifest.setup.ready_requires).state == "ready"


def test_old_shells_are_removed_only_after_success_and_only_in_this_dir(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    shell, full = _with_old_shell(data_dir)
    # A shell somewhere else (e.g. the user's own Playwright cache) is never touched.
    elsewhere = tmp_path / "cache" / "ms-playwright" / "chromium_headless_shell-1243"
    elsewhere.mkdir(parents=True)

    monkeypatch.setattr(_install.subprocess, "run", _Runs(probe_ok=False))
    with pytest.raises(SetupError):
        _install.run_setup(data_dir, tmp_path / "setup.log", probe_timeout_ms=5000, settle_ms=500)
    assert shell.is_dir() and not (data_dir / "ready.json").exists()

    monkeypatch.setattr(_install.subprocess, "run", _Runs())
    _install.run_setup(data_dir, tmp_path / "setup.log", probe_timeout_ms=5000, settle_ms=500)
    assert not shell.exists()
    assert full.is_dir() and elsewhere.is_dir()
    assert f"removed old headless shell {shell}" in (tmp_path / "setup.log").read_text(encoding="utf-8")


def test_an_install_from_before_full_chromium_reads_out_of_date(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ready.json").write_text(json.dumps({"playwright": "1.63.0", "browser": "153.0"}), encoding="utf-8")
    extension = _PACKAGE.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data"), install_dir=install_dir,
    )
    assert extension.setup_status().model_dump() == _OUT_OF_DATE


class _ProbeRecorder:
    """A fake ``playwright.sync_api`` for running the probe script in-process."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        recorder = self

        class _Page:
            def goto(self, url, timeout):
                recorder.calls.append(("goto", url))

            def evaluate(self, script):
                recorder.calls.append(("evaluate", script))

            def screenshot(self, timeout):
                recorder.calls.append(("screenshot",))

        class _Browser:
            version = "153.0"

            def new_page(self):
                return _Page()

            def close(self):
                recorder.calls.append(("close",))

        class _Chromium:
            def launch(self, **options):
                recorder.calls.append(("launch", options))
                return _Browser()

        class _Playwright:
            def __enter__(self):
                return types.SimpleNamespace(chromium=_Chromium())

            def __exit__(self, *exc):
                return False

        self.module = types.ModuleType("playwright.sync_api")
        self.module.sync_playwright = _Playwright


def test_the_probe_waits_for_a_frame_and_the_settle_pause_before_its_screenshot(monkeypatch, capsys) -> None:
    """R35 amendment 2: the probe captures the way BrowserHost does, not straight after goto."""
    recorder = _ProbeRecorder()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", recorder.module)
    monkeypatch.setattr(sys, "argv", ["probe", "5000", "chromium", "500"])
    monkeypatch.setattr("time.sleep", lambda seconds: recorder.calls.append(("sleep", seconds)))

    exec(compile(_install._PROBE_SCRIPT, "probe", "exec"), {"__name__": "__main__"})

    assert [call[0] for call in recorder.calls] == ["launch", "goto", "evaluate", "sleep", "screenshot", "close"]
    assert recorder.calls[0][1] == {"headless": True, "channel": "chromium", "timeout": 5000.0}
    assert "requestAnimationFrame(() => requestAnimationFrame(" in recorder.calls[2][1]
    assert recorder.calls[3] == ("sleep", 0.5)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["browser"] == "153.0"
