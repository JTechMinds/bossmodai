"""Virtual Desktop setup: reach Docker, build the image, prove a desktop starts, write ready.json (no Docker)."""

from __future__ import annotations

import json
from pathlib import Path

import docker.errors
import pytest

from core.extensions.contract import ExtensionContext, SetupError
from tests._virtual_desktop_fakes import DEFAULT_SETTINGS, ENTRY, PACKAGE, FakeDaemon, module

install = module("install")
docker_host = module("docker_host")
settings_module = module("settings")
DEFAULTS = settings_module.DesktopDefaults.model_validate(ENTRY.manifest.defaults)
IMAGE_DIR = Path(PACKAGE.__file__).with_name("image")


def _host(daemon: FakeDaemon | None = None, factory=None):
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return docker_host.DesktopHost(
        defaults=DEFAULTS, install_id="abc1234567", image_tag="bossmod/virtual-desktop:1",
        client_factory=factory or daemon.factory, clock=lambda: now[0], sleep=sleep,
    )


def _run(tmp_path: Path, host) -> Path:
    log = tmp_path / "setup.log"
    log.write_text("", encoding="utf-8")
    install.run_setup(tmp_path / "data", log, host=host, image_dir=IMAGE_DIR, image_version="1", probe_settings=DEFAULT_SETTINGS)
    return log


def test_setup_builds_probes_and_writes_ready(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    log = _run(tmp_path, _host(daemon))
    build = next(entry for entry in daemon.log if entry[0] == "build")[1]
    assert build == {"path": str(IMAGE_DIR), "tag": "bossmod/virtual-desktop:1", "decode": True, "pull": True, "rm": True, "timeout": None}
    assert json.loads((tmp_path / "data" / "ready.json").read_text(encoding="utf-8")) == {"image_version": "1", "docker": "29.8.1"}
    text = log.read_text(encoding="utf-8")
    assert "Successfully built 1" in text and "test desktop ready: screen 1280x800" in text
    assert install.setup_status(tmp_path / "data", ENTRY.manifest.setup.ready_requires).state == "ready"


def test_the_probe_is_a_throwaway_container_removed_afterwards(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    _run(tmp_path, _host(daemon))
    create = next(entry for entry in daemon.log if entry[0] == "containers.create")[2]
    assert create["name"].startswith("bossmod-desktop-probe-")
    assert "volumes" not in create and "labels" not in create
    assert daemon.containers == {} and daemon.removed == [create["name"]]


def test_unreachable_docker_says_how_to_fix_it(tmp_path: Path) -> None:
    def refuse():
        raise docker.errors.DockerException("connection refused")

    with pytest.raises(SetupError) as raised:
        _run(tmp_path, _host(factory=refuse))
    assert str(raised.value) == install.DOCKER_MISSING
    assert "add yourself to the docker group" in str(raised.value)
    assert not (tmp_path / "data" / "ready.json").exists()


def test_a_build_error_fails_setup_with_the_log_tail(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    daemon.build_chunks = [{"stream": "Step 5/17 : RUN curl node\n"}, {"error": "The command returned a non-zero code: 22"}]
    with pytest.raises(SetupError) as raised:
        _run(tmp_path, _host(daemon))
    message = str(raised.value)
    assert message.startswith("The command returned a non-zero code: 22\n")
    assert "Step 5/17 : RUN curl node" in message
    assert not any(entry[0] == "containers.create" for entry in daemon.log)
    assert not (tmp_path / "data" / "ready.json").exists()


def test_a_probe_that_never_gets_ready_is_removed_and_fails_setup(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    daemon.ready_after = None
    with pytest.raises(SetupError, match="a test desktop did not start: the desktop did not finish starting within 60s"):
        _run(tmp_path, _host(daemon))
    assert len(daemon.removed) == 1 and daemon.containers == {}
    assert not (tmp_path / "data" / "ready.json").exists()


def test_the_extension_runs_setup_with_its_image_folder_and_default_screen(monkeypatch, tmp_path: Path) -> None:
    seen = {}
    monkeypatch.setattr(install, "run_setup", lambda data_dir, log_path, **kwargs: seen.update(kwargs, data_dir=data_dir))
    extension = PACKAGE.VirtualDesktopExtension(
        ExtensionContext(manifest=ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: None),
        host=_host(FakeDaemon()),
    )
    extension.run_setup(tmp_path / "setup.log")
    assert seen["image_dir"] == IMAGE_DIR and (IMAGE_DIR / "Dockerfile").is_file()
    assert seen["image_version"] == "1" and seen["probe_settings"] == DEFAULT_SETTINGS
    assert seen["data_dir"] == tmp_path


def test_the_image_and_the_manifest_agree_on_the_contract_version() -> None:
    dockerfile = (IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert 'LABEL bossmod.image_version="1"' in dockerfile
    assert DEFAULTS.image_version == "1" and DEFAULTS.image_tag == "bossmod/virtual-desktop:1"
    for script in ("bm-desktop-start", "bm-ready", "bm-capture", "bm-state"):
        assert (IMAGE_DIR / "bin" / script).is_file()


def test_launched_programs_join_the_session_quietly_and_ready_means_the_whole_desktop() -> None:
    dockerfile = (IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "NO_AT_BRIDGE=1" in dockerfile
    assert "COPY bm-session-env.desktop /etc/xdg/autostart/bm-session-env.desktop" in dockerfile
    entry = (IMAGE_DIR / "bm-session-env.desktop").read_text(encoding="utf-8").splitlines()
    assert "Exec=/usr/local/bin/bm-desktop-start --session-env" in entry and "Type=Application" in entry
    start = (IMAGE_DIR / "bin" / "bm-desktop-start").read_text(encoding="utf-8")
    assert "printf 'export SESSION_MANAGER=%q\\n' \"$SESSION_MANAGER\" >> \"$SESSION_ENV\"" in start
    ready = (IMAGE_DIR / "bin" / "bm-ready").read_text(encoding="utf-8")
    assert "--class xfce4-panel" in ready and "--class xfdesktop" in ready
