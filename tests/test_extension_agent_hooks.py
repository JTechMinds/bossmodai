"""The runtime worker's calls into extensions (core/extensions/agent_hooks.py) and the loader accessors they use."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.bm_cli.command_registry import CORE_COMMAND_NAMES
from core.extensions import agent_hooks, loader
from core.extensions.loader import ExtensionLoadError
from core.extensions.manifest import ExtensionManifest
from core.extensions.registry import Discovery, ExtensionEntry, discover
from core.runtime.events import NullRuntimeEventSink, runtime_events


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _entry(ext_id: str, *, live_view: bool = False, invalid: str | None = None) -> ExtensionEntry:
    data: dict[str, Any] = {
        "id": ext_id, "name": ext_id.title(), "version": "1", "description": "d",
        "command": {"name": ext_id.replace("-", "")[:15], "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False},
    }
    if live_view:
        data["live_view"] = {"label": "View", "icon": "monitor"}
    return ExtensionEntry(
        id=ext_id, root=Path("/nonexistent"), manifest=ExtensionManifest.model_validate(data), invalid_reason=invalid,
    )


class Plain:
    """An extension implementing neither hook; records its shutdowns."""

    def __init__(self, ext_id: str, log: list[tuple], *, fail: bool = False) -> None:
        self.ext_id = ext_id
        self.log = log
        self.fail = fail

    def handle(self, ctx, parsed, body):
        raise AssertionError("not used")

    def shutdown(self) -> None:
        self.log.append(("shutdown", self.ext_id))
        if self.fail:
            raise RuntimeError(f"{self.ext_id} shutdown broke")


class Hooked(Plain):
    """An extension implementing both worker hooks; ``fail`` makes each raise."""

    def agent_config_changed(self, agent_id: str) -> None:
        self.log.append(("changed", self.ext_id, agent_id))
        if self.fail:
            raise RuntimeError(f"{self.ext_id} cannot restart {agent_id}")

    def reconcile_agents(self, existing_agent_ids: frozenset[str]) -> None:
        self.log.append(("reconcile", self.ext_id, existing_agent_ids))
        if self.fail:
            raise RuntimeError(f"{self.ext_id}: Docker is not running")


class Host:
    """What agent_hooks sees of the host: a discovery, an enabled set, and a loader."""

    def __init__(self) -> None:
        self.entries: list[ExtensionEntry] = []
        self.enabled: set[str] = set()
        self.instances: dict[str, Plain] = {}
        self.load_errors: dict[str, str] = {}
        self.log: list[tuple] = []

    def add(self, entry: ExtensionEntry, instance_cls: type[Plain] | None, *, enabled: bool = True, fail: bool = False):
        self.entries.append(entry)
        if enabled:
            self.enabled.add(entry.id)
        if instance_cls is not None:
            self.instances[entry.id] = instance_cls(entry.id, self.log, fail=fail)
        return self.instances.get(entry.id)

    def load(self, entry: ExtensionEntry) -> Plain:
        self.log.append(("load", entry.id))
        if entry.id in self.load_errors:
            raise ExtensionLoadError(self.load_errors[entry.id])
        instance = self.instances[entry.id]
        # Kept in the real loader's table, so apply_extension_states reads it
        # through the real loaded_extensions()/unload_extension().
        loader._loaded[entry.id] = instance
        return instance


@pytest.fixture()
def host(monkeypatch: pytest.MonkeyPatch) -> Host:
    fake = Host()
    monkeypatch.setattr(loader, "_loaded", {})
    monkeypatch.setattr(agent_hooks, "get_discovery", lambda: Discovery(entries=tuple(fake.entries)))
    monkeypatch.setattr(agent_hooks, "enabled_ids", lambda: frozenset(fake.enabled))
    monkeypatch.setattr(agent_hooks, "load_extension", fake.load)
    return fake


# ─── notify_agent_config_changed ───


def test_notify_calls_an_enabled_extension_that_implements_the_hook(host: Host) -> None:
    host.add(_entry("desk", live_view=True), Hooked)
    assert agent_hooks.notify_agent_config_changed("desk", "a1") == frozenset({"desk"})
    assert host.log == [("load", "desk"), ("changed", "desk", "a1")]


def test_notify_returns_no_id_for_an_extension_without_a_live_view(host: Host) -> None:
    host.add(_entry("quiet"), Hooked)
    assert agent_hooks.notify_agent_config_changed("quiet", "a1") == frozenset()
    assert ("changed", "quiet", "a1") in host.log


def test_notify_skips_an_extension_without_the_hook(host: Host) -> None:
    host.add(_entry("plain", live_view=True), Plain)
    assert agent_hooks.notify_agent_config_changed("plain", "a1") == frozenset()
    assert host.log == [("load", "plain")]


@pytest.mark.parametrize("ext_id", ["missing", "broken", "off"])
def test_notify_warns_and_calls_nothing_for_an_unknown_invalid_or_disabled_extension(
    host: Host, caplog: pytest.LogCaptureFixture, ext_id: str,
) -> None:
    host.add(_entry("broken", invalid="manifest.json is invalid"), Hooked)
    host.add(_entry("off", live_view=True), Hooked, enabled=False)
    with caplog.at_level(logging.WARNING, logger=agent_hooks.__name__):
        assert agent_hooks.notify_agent_config_changed(ext_id, "a1") == frozenset()
    assert host.log == [], "nothing is loaded or called"
    [record] = caplog.records
    assert record.levelno == logging.WARNING and ext_id in record.getMessage() and "a1" in record.getMessage()


def test_notify_logs_a_failing_hook_and_still_nudges_its_live_view(host: Host, caplog: pytest.LogCaptureFixture) -> None:
    host.add(_entry("desk", live_view=True), Hooked, fail=True)
    with caplog.at_level(logging.ERROR, logger=agent_hooks.__name__):
        assert agent_hooks.notify_agent_config_changed("desk", "a1") == frozenset({"desk"})
    [record] = caplog.records
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert "desk" in record.getMessage() and "a1" in record.getMessage()


def test_notify_logs_a_load_failure(host: Host, caplog: pytest.LogCaptureFixture) -> None:
    host.add(_entry("desk", live_view=True), Hooked)
    host.load_errors["desk"] = "extension desk failed to start: boom"
    with caplog.at_level(logging.ERROR, logger=agent_hooks.__name__):
        assert agent_hooks.notify_agent_config_changed("desk", "a1") == frozenset()
    assert ("changed", "desk", "a1") not in host.log
    assert any(r.levelno == logging.ERROR and "could not load" in r.getMessage() for r in caplog.records)


# ─── apply_extension_states ───


def test_apply_releases_only_turned_off_extensions_and_unloads_them(host: Host, caplog: pytest.LogCaptureFixture) -> None:
    kept = host.add(_entry("kept", live_view=True), Plain)
    host.add(_entry("browser", live_view=True), Plain, enabled=False)
    host.add(_entry("mail"), Hooked, enabled=False)
    host.add(_entry("failing", live_view=True), Plain, enabled=False, fail=True)
    host.add(_entry("never-loaded", live_view=True), Plain, enabled=False)
    for ext_id in ("kept", "browser", "mail", "failing"):
        host.load(next(entry for entry in host.entries if entry.id == ext_id))
    host.log.clear()

    with caplog.at_level(logging.ERROR, logger=agent_hooks.__name__):
        released = agent_hooks.apply_extension_states()

    # A failing shutdown is logged and the next extension is still released.
    assert released == frozenset({"browser", "failing"})
    assert sorted(host.log) == [("shutdown", "browser"), ("shutdown", "failing"), ("shutdown", "mail")]
    assert loader.loaded_extensions() == {"kept": kept}, "released ones are unloaded, a failing one too"
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "failing" in record.getMessage() and record.exc_info is not None


def test_apply_with_everything_enabled_touches_nothing(host: Host) -> None:
    host.add(_entry("kept", live_view=True), Plain)
    host.load(host.entries[0])
    host.log.clear()
    assert agent_hooks.apply_extension_states() == frozenset()
    assert host.log == []


def test_apply_releases_a_loaded_id_missing_from_discovery_and_logs_it(host: Host, caplog: pytest.LogCaptureFixture) -> None:
    stray = Plain("stray", host.log)
    loader._loaded["stray"] = stray
    with caplog.at_level(logging.ERROR, logger=agent_hooks.__name__):
        assert agent_hooks.apply_extension_states() == frozenset()
    assert host.log == [("shutdown", "stray")]
    assert loader.loaded_extensions() == {}
    assert any("not in this process's discovery" in r.getMessage() for r in caplog.records)


# ─── reconcile_agents ───


def test_reconcile_tells_each_enabled_implementing_extension_which_agents_exist(
    host: Host, caplog: pytest.LogCaptureFixture,
) -> None:
    ada = db.create_agent("Ada", role="Researcher")
    bob = db.create_agent("Bob", role="Writer")
    host.add(_entry("first", live_view=True), Hooked, fail=True)
    host.add(_entry("second"), Hooked)
    host.add(_entry("plain", live_view=True), Plain)
    host.add(_entry("off", live_view=True), Hooked, enabled=False)
    host.add(_entry("broken", live_view=True, invalid="missing __init__.py"), Hooked)
    host.add(_entry("unloadable", live_view=True), Hooked)
    host.load_errors["unloadable"] = "extension unloadable failed to import: boom"

    with caplog.at_level(logging.ERROR, logger=agent_hooks.__name__):
        called = agent_hooks.reconcile_agents()

    existing = frozenset({ada.id, bob.id})
    # "first" raised and "second" still ran; "plain" has no hook; "off",
    # "broken" and "unloadable" are never called.
    assert [item for item in host.log if item[0] == "reconcile"] == [
        ("reconcile", "first", existing), ("reconcile", "second", existing),
    ]
    assert ("load", "off") not in host.log and ("load", "broken") not in host.log
    assert called == frozenset({"first"}), "hooked and live-view; second has no live view"
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("first" in message and "reconcile" in message for message in errors)
    assert any("unloadable" in message and "could not load" in message for message in errors)


def test_reconcile_sees_a_deleted_agent_as_missing(host: Host) -> None:
    from core.agent_repository import agent_repository

    ada = agent_repository.create(name="Ada")
    bob = agent_repository.create(name="Bob")
    host.add(_entry("desk", live_view=True), Hooked)
    agent_repository.delete(ada.id)
    agent_hooks.reconcile_agents()
    assert ("reconcile", "desk", frozenset({bob.id})) in host.log


# ─── the live nudge the worker sends for what the hooks return (plan step 35) ───


class _RecordingSink(NullRuntimeEventSink):
    def __init__(self) -> None:
        self.live: list[tuple[str | None, str | None]] = []

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        self.live.append((extension_id, agent_id))


@pytest.fixture()
def sink():
    recording = _RecordingSink()
    runtime_events.set_sink(recording)
    yield recording
    runtime_events.set_sink(NullRuntimeEventSink())


@pytest.mark.asyncio
async def test_the_worker_nudges_the_live_views_its_hooks_touched(host: Host, sink: _RecordingSink) -> None:
    from core.runtime.worker import RuntimeController

    host.add(_entry("desk", live_view=True), Hooked)
    host.add(_entry("browser", live_view=True), Plain, enabled=False)
    host.add(_entry("mail"), Hooked)
    host.load(host.entries[1])
    controller = RuntimeController()
    controller.extension_lane.start()

    async def drained() -> None:
        # The lane is sequential: once this job runs, every earlier one has.
        done = asyncio.Event()

        async def _mark() -> None:
            done.set()

        controller.extension_lane.submit(_mark)
        await asyncio.wait_for(done.wait(), timeout=5.0)

    try:
        await controller.extension_config_changed("desk", "a1")
        await drained()
        assert sink.live == [("desk", None)]

        sink.live.clear()
        await controller.extension_config_changed("mail", "a1")
        await drained()
        assert sink.live == [], "no live view, no nudge"

        await controller.extensions_reconcile()
        await drained()
        # browser was released (apply), desk reconciled; mail has no live view.
        assert sink.live == [("browser", None), ("desk", None)]
    finally:
        await controller.extension_lane.stop()


# ─── loader accessors used by apply_extension_states ───


def _write_extension(root: Path, ext_id: str, *, live_view: bool = False) -> ExtensionEntry:
    folder = root / ext_id
    folder.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "id": ext_id, "name": "Demo", "version": "1", "description": "d",
        "command": {"name": ext_id.replace("-", "")[:15], "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False},
    }
    if live_view:
        manifest["live_view"] = {"label": "View", "icon": "monitor"}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "__init__.py").write_text(
        "class _Ext:\n    def shutdown(self):\n        pass\ndef create(ctx):\n    return _Ext()\n", encoding="utf-8",
    )
    return discover(root, CORE_COMMAND_NAMES).get(ext_id)


def test_an_unloaded_extension_is_created_afresh_on_the_next_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(loader, "_loaded", {})
    entry = _write_extension(tmp_path, "fresh-ext")
    first = loader.load_extension(entry)
    assert loader.load_extension(entry) is first
    snapshot = loader.loaded_extensions()
    snapshot.clear()
    assert loader.loaded_extensions() == {"fresh-ext": first}, "a copy, not the loader's own table"

    loader.unload_extension("fresh-ext")
    assert loader.loaded_extensions() == {}
    second = loader.load_extension(entry)
    assert second is not first
    loader.unload_extension("never-loaded")  # nothing to forget; no error
    assert loader.loaded_extensions() == {"fresh-ext": second}


def test_unloading_keeps_a_contract_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(loader, "_loaded", {})
    monkeypatch.setattr(loader, "_contract_failures", {})
    entry = _write_extension(tmp_path, "liar-ext", live_view=True)
    with pytest.raises(ExtensionLoadError, match="declares live_view"):
        loader.load_extension(entry)
    loader.unload_extension("liar-ext")
    assert loader.contract_failure("liar-ext") == "manifest declares live_view but the extension has no live_view() method"
