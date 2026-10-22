"""Virtual Desktop ``DesktopHost`` against a fake Docker engine that records every call."""

from __future__ import annotations

import io
import tarfile

import docker.errors
import pytest
import requests

from tests._virtual_desktop_fakes import DEFAULT_SETTINGS, ENTRY, FakeDaemon, module, types

docker_host = module("docker_host")
settings_module = module("settings")
DEFAULTS = settings_module.DesktopDefaults.model_validate(ENTRY.manifest.defaults)
INSTALL = "abc1234567"
AGENT = "agent-1"
NAME = f"bossmod-desktop-{INSTALL}-{AGENT}"
VOLUME = f"bossmod-desktop-home-{INSTALL}-{AGENT}"
LABELS = {"bossmod.extension": "virtual-desktop", "bossmod.install": INSTALL, "bossmod.agent_id": AGENT}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _host(daemon: FakeDaemon, clock: _Clock | None = None):
    clock = clock or _Clock()
    return docker_host.DesktopHost(
        defaults=DEFAULTS, install_id=INSTALL, image_tag="bossmod/virtual-desktop:1",
        client_factory=daemon.factory, clock=clock, sleep=clock.sleep,
    )


def _steps(container) -> list[str]:
    return [call[0] if call[0] != "exec_run" else f"exec {call[1][0]}" for call in container.calls]


def test_the_first_start_creates_the_volume_and_container_with_limits_and_labels() -> None:
    daemon = FakeDaemon()
    report = _host(daemon).ensure_running(AGENT, DEFAULT_SETTINGS)
    assert report.started and not report.recreated
    assert ("volumes.create", VOLUME, LABELS) in daemon.log
    create = next(entry for entry in daemon.log if entry[0] == "containers.create")
    assert create[1] == "bossmod/virtual-desktop:1"
    assert create[2] == {
        "name": NAME,
        "hostname": "desktop",
        "labels": {**LABELS, "bossmod.image_version": "1"},
        "volumes": {VOLUME: {"bind": "/home/agent", "mode": "rw"}},
        "shm_size": "512m",
        "init": False,
        "mem_limit": 2048 * 1024 * 1024,
        "memswap_limit": 2048 * 1024 * 1024,
        "cpu_period": 100_000,
        "cpu_quota": 200_000,
    }


def test_a_start_updates_limits_then_writes_the_screen_then_starts_then_waits_for_ready() -> None:
    daemon = FakeDaemon()
    daemon.ready_after = 2
    clock = _Clock()
    settings = settings_module.DesktopSettings(width=1600, height=900, memory_mb=3072, cpus=1)
    _host(daemon, clock).ensure_running(AGENT, settings)
    container = daemon.containers[NAME]
    assert _steps(container) == ["update", "put_archive", "start", "exec bm-ready", "exec bm-ready", "exec bm-ready"]
    assert container.calls[0][1] == {"mem_limit": 3072 * 1024 * 1024, "memswap_limit": 3072 * 1024 * 1024, "cpu_period": 100_000, "cpu_quota": 100_000}
    _, path, data = container.calls[1]
    assert path == "/etc/bossmod"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        assert tar.extractfile("desktop.env").read() == b"SCREEN_WIDTH=1600\nSCREEN_HEIGHT=900\n"
    exec_kwargs = container.calls[3][2]
    assert exec_kwargs == {"user": "agent", "environment": {"DISPLAY": ":0"}, "demux": True}
    assert clock.sleeps == [0.25, 0.25]


def test_a_running_desktop_is_left_alone_and_settings_are_remembered() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    calls = len(daemon.containers[NAME].calls)
    report = host.ensure_running(AGENT, DEFAULT_SETTINGS)
    assert not report.started
    assert len(daemon.containers[NAME].calls) == calls
    assert host.running_here() == frozenset({AGENT})
    assert host.applied(AGENT) == DEFAULT_SETTINGS


def test_an_old_image_version_is_recreated_keeping_the_home_volume() -> None:
    daemon = FakeDaemon()
    daemon.add_volume(VOLUME, LABELS)
    old = daemon.add_container(NAME, {**LABELS, "bossmod.image_version": "0"}, "exited")
    report = _host(daemon).ensure_running(AGENT, DEFAULT_SETTINGS)
    assert report.started and report.recreated
    assert ("remove", True) in old.calls
    assert VOLUME in daemon.volumes and daemon.removed_volumes == []
    assert daemon.containers[NAME] is not old


def test_a_running_orphan_is_restarted_so_its_settings_are_known() -> None:
    daemon = FakeDaemon()
    orphan = daemon.add_container(NAME, {**LABELS, "bossmod.image_version": "1"}, "running")
    report = _host(daemon).ensure_running(AGENT, DEFAULT_SETTINGS)
    assert report.started
    assert _steps(orphan)[:4] == ["stop", "update", "put_archive", "start"]


def test_a_desktop_that_never_gets_ready_fails_and_is_not_recorded() -> None:
    daemon = FakeDaemon()
    daemon.ready_after = None
    host = _host(daemon)
    with pytest.raises(types.DesktopActionError, match=r"did not finish starting within 60s \(Can't open display\)"):
        host.ensure_running(AGENT, DEFAULT_SETTINGS)
    assert host.running_here() == frozenset()


def test_a_missing_image_says_setup_must_run_again() -> None:
    daemon = FakeDaemon()
    daemon.images.clear()
    with pytest.raises(types.DesktopActionError, match="image bossmod/virtual-desktop:1 is missing"):
        _host(daemon).ensure_running(AGENT, DEFAULT_SETTINGS)


def test_restart_keeps_the_agent_running_and_applies_new_settings() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    bigger = settings_module.DesktopSettings(width=1920, height=1080, memory_mb=4096, cpus=4)
    host.restart(AGENT, bigger)
    assert host.applied(AGENT) == bigger
    assert "stop" in _steps(daemon.containers[NAME])


def test_stop_keeps_the_disk_and_reset_removes_both_then_starts_fresh() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    assert host.stop(AGENT) is True
    assert host.stop(AGENT) is False
    assert NAME in daemon.containers and VOLUME in daemon.volumes
    assert host.running_here() == frozenset() and host.applied(AGENT) is None
    first = daemon.containers[NAME]
    report = host.reset(AGENT, DEFAULT_SETTINGS)
    assert report.started
    assert ("remove", True) in first.calls and daemon.removed_volumes == [VOLUME]
    assert daemon.containers[NAME] is not first and VOLUME in daemon.volumes


def test_helpers_run_as_agent_and_failures_carry_their_stderr() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    capture = host.capture(AGENT)
    assert capture.screen == (1280, 800) and capture.pointer_window == "Desktop" and capture.png.startswith(types.PNG_SIGNATURE)
    host.xdotool(AGENT, ["key", "ctrl+l"])
    assert daemon.containers[NAME].calls[-1][1] == ["xdotool", "key", "ctrl+l"]
    daemon.state = {"screen": [1280, 800]}
    with pytest.raises(types.DesktopActionError, match="bm-state printed an unreadable line"):
        host.capture(AGENT)


def test_run_writes_output_to_files_in_a_fresh_folder_then_reads_and_removes_them() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    container = daemon.containers[NAME]
    before = len(container.calls)
    outcome = host.run(AGENT, "npm run dev > ~/dev.log 2>&1 &", 120)
    assert outcome == types.RunOutcome(exit_code=3, stdout=b"out", stderr=b"err", stdout_size=3, stderr_size=3)
    mkdir, run, read = (call[1:] for call in container.calls[before:])
    [folder] = daemon.run_folders
    assert folder.startswith("/tmp/bm-run/") and len(folder) == len("/tmp/bm-run/") + 32
    assert mkdir[0] == ["mkdir", "-p", "--", folder]
    assert run == (
        ["bash", "-c", types.RUN_SCRIPT, "_", "120", "npm run dev > ~/dev.log 2>&1 &", f"{folder}/out", f"{folder}/err"],
        {"user": "agent", "environment": {"DISPLAY": ":0"}, "workdir": "/home/agent", "demux": True},
    )
    assert read[0] == ["bash", "-c", types.READ_SCRIPT, "_", folder, "20000"]
    # Each run gets its own folder.
    host.run(AGENT, "true", 120)
    assert len(set(daemon.run_folders)) == 2


def test_run_returns_the_start_of_long_output_with_its_full_size() -> None:
    daemon = FakeDaemon()
    daemon.run_exit, daemon.run_stdout, daemon.run_stderr = 0, b"x" * 25_000, b""
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    outcome = host.run(AGENT, "yes x | head -c 25000", 120)
    assert outcome.stdout == b"x" * 20_000 and outcome.stdout_size == 25_000
    assert outcome.stderr == b"" and outcome.stderr_size == 0


def test_run_reports_a_shell_that_could_not_set_up_its_output_files() -> None:
    daemon = FakeDaemon()
    daemon.run_setup_error = b"bash: /tmp/bm-run/x/out: No such file or directory\n"
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    with pytest.raises(types.DesktopActionError, match="running the command failed: bash: /tmp/bm-run/x/out: No such file"):
        host.run(AGENT, "ls", 120)


@pytest.mark.parametrize("raw", [b"", b"12\nabc", b"x y\n", b"3 0\nab", b"3 0\nabcd"])
def test_run_output_of_the_wrong_shape_is_an_error(raw: bytes) -> None:
    with pytest.raises(types.DesktopActionError, match="reading the command's output failed"):
        types.parse_run_output(0, raw, 20_000)


def test_the_run_scripts_redirect_the_command_and_frame_what_they_read(tmp_path) -> None:
    """The two scripts, run by a real bash: a background child does not hold the run open."""
    import os
    import subprocess
    import time

    folder = tmp_path / "run"
    folder.mkdir()
    # An empty home: no developer's ~/.profile adds output to the login shell.
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    began = time.monotonic()
    done = subprocess.run(
        ["bash", "-c", types.RUN_SCRIPT, "_", "20", "echo hello; echo oops >&2; (sleep 5; echo late) & exit 7",
         str(folder / "out"), str(folder / "err")],
        capture_output=True, check=False, env=env,
    )
    assert done.returncode == 7 and done.stdout == b"" and done.stderr == b""
    assert time.monotonic() - began < 3
    raw = subprocess.run(["bash", "-c", types.READ_SCRIPT, "_", str(folder), "3"], capture_output=True, check=True, env=env).stdout
    outcome = types.parse_run_output(7, raw, 3)
    assert (outcome.stdout, outcome.stdout_size, outcome.stderr, outcome.stderr_size) == (b"hel", 6, b"oop", 5)
    assert not folder.exists()


def test_launch_starts_the_program_detached() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    host.launch(AGENT, "firefox-esr https://example.com")
    assert daemon.containers[NAME].calls[-1][1:] == (
        ["setsid", "-f", "bash", "-lc", "firefox-esr https://example.com"],
        {"user": "agent", "environment": {"DISPLAY": ":0"}, "workdir": "/home/agent", "detach": True},
    )


def test_archive_copies_name_a_missing_path() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)
    daemon.missing_paths.add("/home/agent/nope")
    with pytest.raises(types.DesktopPathMissing, match="/home/agent/nope does not exist on the desktop"):
        host.put_archive(AGENT, "/home/agent/nope", io.BytesIO(b"x"))
    with pytest.raises(types.DesktopPathMissing):
        host.get_archive(AGENT, "/home/agent/nope")
    chunks, _ = host.get_archive(AGENT, "/home/agent/Desktop")
    assert b"".join(chunks) == b"tar-bytes"


def test_each_agent_gets_its_own_client() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running("a", DEFAULT_SETTINGS)
    host.ensure_running("b", DEFAULT_SETTINGS)
    host.capture("a")
    assert len(daemon.clients) == 2


def test_docker_unreachable_is_one_plain_sentence() -> None:
    def refuse():
        raise docker.errors.DockerException("Error while fetching server API version")

    host = docker_host.DesktopHost(defaults=DEFAULTS, install_id=INSTALL, image_tag="t", client_factory=refuse)
    with pytest.raises(types.DockerUnavailable, match=r"^Docker isn't running or BossMod can't reach it\.$"):
        host.ensure_running(AGENT, DEFAULT_SETTINGS)
    with pytest.raises(types.DockerUnavailable):
        host.capacity()


def test_a_lost_connection_is_unavailable_and_reconnects_next_time() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running(AGENT, DEFAULT_SETTINGS)

    def broken(*args, **kwargs):
        raise requests.exceptions.ConnectionError("socket closed")

    daemon.containers[NAME].exec_run = broken
    with pytest.raises(types.DockerUnavailable):
        host.capture(AGENT)
    assert daemon.clients[0].closed
    del daemon.containers[NAME].exec_run
    host.capture(AGENT)
    assert len(daemon.clients) == 2


def test_capacity_reads_cpus_memory_and_version() -> None:
    assert _host(FakeDaemon()).capacity() == types.HostCapacity(cpus=4, memory_mb=16384, server_version="29.8.1")


def test_reconcile_removes_deleted_agents_and_stops_unowned_desktops() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running("mine", DEFAULT_SETTINGS)
    gone_labels = {**LABELS, "bossmod.agent_id": "gone"}
    stray_labels = {**LABELS, "bossmod.agent_id": "stray"}
    daemon.add_container(f"bossmod-desktop-{INSTALL}-gone", {**gone_labels, "bossmod.image_version": "1"}, "running")
    daemon.add_volume(f"bossmod-desktop-home-{INSTALL}-gone", gone_labels)
    daemon.add_volume(f"bossmod-desktop-home-{INSTALL}-volume-only", {**LABELS, "bossmod.agent_id": "volume-only"})
    stray = daemon.add_container(f"bossmod-desktop-{INSTALL}-stray", {**stray_labels, "bossmod.image_version": "1"}, "running")
    other_install = daemon.add_container("bossmod-desktop-zzz-gone", {**gone_labels, "bossmod.install": "zzz"}, "running")

    host.reconcile(frozenset({"mine", "stray"}))

    assert f"bossmod-desktop-{INSTALL}-gone" in daemon.removed
    assert set(daemon.removed_volumes) == {f"bossmod-desktop-home-{INSTALL}-gone", f"bossmod-desktop-home-{INSTALL}-volume-only"}
    assert stray.status == "exited"
    assert daemon.containers[f"bossmod-desktop-{INSTALL}-mine"].status == "running"
    assert other_install.status == "running" and other_install.calls == []
    host.reconcile(frozenset({"mine", "stray"}))  # idempotent
    assert host.running_here() == frozenset({"mine"})


def test_shutdown_stops_every_running_desktop_of_this_install_by_label() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running("mine", DEFAULT_SETTINGS)
    leftover = daemon.add_container(
        f"bossmod-desktop-{INSTALL}-leftover", {**LABELS, "bossmod.agent_id": "leftover", "bossmod.image_version": "1"}, "running",
    )
    other_install = daemon.add_container("bossmod-desktop-zzz-x", {**LABELS, "bossmod.install": "zzz"}, "running")
    host.shutdown()
    listing = next(entry for entry in daemon.log if entry[0] == "containers.list")
    assert listing[1:] == (False, {"label": ["bossmod.extension=virtual-desktop", f"bossmod.install={INSTALL}"]}, True)
    assert daemon.containers[f"bossmod-desktop-{INSTALL}-mine"].status == "exited"
    assert leftover.status == "exited"
    assert other_install.status == "running"
    assert host.running_here() == frozenset()
    assert all(client.closed for client in daemon.clients)


def test_shutdown_stops_the_others_when_one_fails_then_raises() -> None:
    daemon = FakeDaemon()
    host = _host(daemon)
    host.ensure_running("a", DEFAULT_SETTINGS)
    host.ensure_running("b", DEFAULT_SETTINGS)

    def refuse(timeout=None):
        raise docker.errors.APIError("cannot stop")

    daemon.containers[f"bossmod-desktop-{INSTALL}-a"].stop = refuse
    with pytest.raises(types.DesktopActionError, match="shutdown failed for the desktops of agents a"):
        host.shutdown()
    assert daemon.containers[f"bossmod-desktop-{INSTALL}-b"].status == "exited"
