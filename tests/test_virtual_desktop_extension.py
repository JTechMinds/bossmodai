"""Virtual Desktop extension: creation, live view, settings verify/apply, reconcile and shutdown (no Docker)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from core.extensions.contract import (
    ConfigError,
    ExtensionContext,
    SupportsAgentConfig,
    SupportsAgentConfigChanged,
    SupportsAgentReconcile,
    SupportsLiveView,
    SupportsPromptState,
    SupportsWake,
)
from tests._virtual_desktop_fakes import DEFAULT_SETTINGS, ENTRY, PACKAGE, FakeHost, module, png, types

docker_access = module("docker_access")
screenshots = module("screenshots")
settings_module = module("settings")


def _extension(tmp_path: Path, configs: dict, host: FakeHost | None = None):
    return PACKAGE.VirtualDesktopExtension(
        ExtensionContext(manifest=ENTRY.manifest, data_dir=tmp_path, read_agent_config=configs.get),
        host=host or FakeHost(),
    )


def _meta(pid: int, *, command: str = "vm view", window: str = "Mozilla Firefox", taken_at: str = "2026-10-22T12:00:00+00:00"):
    return screenshots.ShotMeta(
        command=command, window=window, screen="1280x800", image="1280x800",
        pointer='(412, 488) px = (322, 611)‰ — over "Mozilla Firefox"', taken_at=taken_at, pid=pid,
    )


def _finished_pid() -> int:
    """The pid of a process that has exited (a dead worker's)."""
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


def test_create_makes_no_docker_call(monkeypatch, tmp_path: Path) -> None:
    def no_docker(*args, **kwargs):
        raise AssertionError("creating the extension must not reach Docker")

    monkeypatch.setattr(docker_access.docker, "from_env", no_docker)
    extension = PACKAGE.create(ExtensionContext(manifest=ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: None))
    for protocol in (SupportsLiveView, SupportsAgentConfig, SupportsWake, SupportsPromptState,
                     SupportsAgentConfigChanged, SupportsAgentReconcile):
        assert isinstance(extension, protocol)
    assert extension.live_view() == []
    assert extension.poll_wake("agent-1") is None
    assert extension.setup_status().state == "missing"


def test_the_live_view_lists_each_agents_latest_screenshot_of_a_live_worker(tmp_path: Path) -> None:
    extension = _extension(tmp_path, {})
    store = screenshots.ShotStore(tmp_path / "shots", 20)
    store.store("a1", png(4, 4), _meta(os.getpid(), command="vm view"))
    latest = store.store("a1", png(4, 4), _meta(os.getpid(), command="vm click", taken_at="2026-10-22T12:05:00+00:00"))
    store.store("a2", png(4, 4), _meta(os.getpid(), window="", taken_at="2026-10-22T12:01:00+00:00"))
    items = extension.live_view()
    assert [item.agent_id for item in items] == ["a1", "a2"]
    first = items[0]
    assert first.image_path == latest and first.command == "vm click" and first.url is None
    assert first.title == "Mozilla Firefox"
    assert [(fact.label, fact.value) for fact in first.facts] == [
        ("screen", "1280x800"), ("image", "1280x800"), ("pointer", '(412, 488) px = (322, 611)‰ — over "Mozilla Firefox"'),
    ]
    assert items[1].title == ""


def test_the_live_view_skips_a_dead_workers_screenshots_with_one_warning(tmp_path: Path, caplog) -> None:
    extension = _extension(tmp_path, {})
    store = screenshots.ShotStore(tmp_path / "shots", 20)
    store.store("a1", png(4, 4), _meta(_finished_pid()))
    with caplog.at_level(logging.WARNING):
        assert extension.live_view() == []
        assert extension.live_view() == []
    assert len([record for record in caplog.records if "is gone" in record.message]) == 1


def test_the_live_view_skips_a_screenshot_whose_sidecar_is_unreadable(tmp_path: Path) -> None:
    extension = _extension(tmp_path, {})
    store = screenshots.ShotStore(tmp_path / "shots", 20)
    older = store.store("a1", png(4, 4), _meta(os.getpid(), command="vm view"))
    newest = store.store("a1", png(4, 4), _meta(os.getpid(), command="vm click"))
    newest.with_suffix(".json").write_text(json.dumps({"command": "vm click"}), encoding="utf-8")
    [item] = extension.live_view()
    assert item.image_path == older


def test_verify_accepts_specs_within_the_host_and_says_so(tmp_path: Path) -> None:
    extension = _extension(tmp_path, {})
    values = {"screen_width": "1280", "screen_height": "800", "memory_mb": "2048", "cpus": "2", "power_down_reminder_minutes": "120"}
    assert extension.verify_agent_config(values) == "Ready: 1280×800 screen, up to 2048 MB of 16000 MB and 2 of 4 CPUs."


@pytest.mark.parametrize(("values", "message"), [
    ({"cpus": "8"}, "This computer has 4 CPUs; choose 4 or fewer."),
    ({"memory_mb": "32768"}, "This computer has 16000 MB of memory; choose 16000 MB or less."),
])
def test_verify_refuses_specs_larger_than_the_host(tmp_path: Path, values: dict, message: str) -> None:
    extension = _extension(tmp_path, {})
    with pytest.raises(ConfigError, match=f"^{message}$"):
        extension.verify_agent_config(values)


def test_verify_without_docker_says_so(tmp_path: Path) -> None:
    host = FakeHost()
    host.fail["capacity"] = types.DockerUnavailable(types.UNAVAILABLE)
    with pytest.raises(ConfigError, match="^Docker isn't running or BossMod can't reach it.$"):
        _extension(tmp_path, {}, host).verify_agent_config({})


def test_turning_an_agent_off_stops_its_desktop_and_deletes_its_screenshots(tmp_path: Path) -> None:
    configs: dict = {}
    host = FakeHost()
    host.ensure_running("a1", DEFAULT_SETTINGS)
    extension = _extension(tmp_path, configs, host)
    screenshots.ShotStore(tmp_path / "shots", 20).store("a1", png(4, 4), _meta(os.getpid()))
    extension.agent_config_changed("a1")
    assert ("stop", "a1") in host.calls
    assert host.running_here() == frozenset()
    assert not (tmp_path / "shots" / "a1").exists()


def test_turning_off_an_agent_whose_desktop_is_not_running_makes_no_docker_call(tmp_path: Path) -> None:
    host = FakeHost()
    _extension(tmp_path, {}, host).agent_config_changed("a1")
    assert host.calls == []


def test_a_settings_save_restarts_only_when_screen_memory_or_cpus_differ(tmp_path: Path) -> None:
    configs = {"a1": {}}
    host = FakeHost()
    host.ensure_running("a1", DEFAULT_SETTINGS)
    extension = _extension(tmp_path, configs, host)

    configs["a1"] = {"power_down_reminder_minutes": "15"}
    extension.agent_config_changed("a1")
    assert "restart" not in host.names()

    configs["a1"] = {"power_down_reminder_minutes": "15", "screen_width": "1920", "screen_height": "1080"}
    extension.agent_config_changed("a1")
    restarts = [call for call in host.calls if call[0] == "restart"]
    assert restarts == [("restart", "a1", settings_module.DesktopSettings(width=1920, height=1080, memory_mb=2048, cpus=2))]

    extension.agent_config_changed("a1")
    assert len([call for call in host.calls if call[0] == "restart"]) == 1


def test_a_settings_save_for_a_stopped_desktop_waits_for_its_next_start(tmp_path: Path) -> None:
    host = FakeHost()
    _extension(tmp_path, {"a1": {"cpus": "4"}}, host).agent_config_changed("a1")
    assert host.calls == []


def test_reconcile_deletes_screenshots_of_desktops_not_running_here_even_when_it_fails(tmp_path: Path) -> None:
    host = FakeHost()
    host.ensure_running("kept", DEFAULT_SETTINGS)
    extension = _extension(tmp_path, {}, host)
    store = screenshots.ShotStore(tmp_path / "shots", 20)
    store.store("kept", png(4, 4), _meta(os.getpid()))
    store.store("old", png(4, 4), _meta(os.getpid()))
    host.fail["reconcile"] = types.DockerUnavailable(types.UNAVAILABLE)
    with pytest.raises(types.DockerUnavailable):
        extension.reconcile_agents(frozenset({"kept"}))
    assert store.agents() == ["kept"]
    extension.reconcile_agents(frozenset())
    assert ("reconcile", frozenset()) in host.calls
    assert store.agents() == []


def test_shutdown_stops_the_desktops_and_deletes_every_screenshot(tmp_path: Path) -> None:
    host = FakeHost()
    host.ensure_running("a1", DEFAULT_SETTINGS)
    extension = _extension(tmp_path, {}, host)
    screenshots.ShotStore(tmp_path / "shots", 20).store("a1", png(4, 4), _meta(os.getpid()))
    extension.shutdown()
    assert ("shutdown",) in host.calls
    assert screenshots.ShotStore(tmp_path / "shots", 20).agents() == []


def test_wake_errors_are_plain_sentences(tmp_path: Path) -> None:
    extension = _extension(tmp_path, {})
    assert extension.describe_wake_error(ValueError("stored desktop setting cpus is not a whole number")) == "Its desktop settings can't be read."
    assert extension.describe_wake_error(RuntimeError("boom")) == "Its desktop idle check failed."
