"""The extension wake service (core/extensions/wake_service.py) over a fake wake extension."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.extensions import wake_service
from core.extensions.contract import WakeBatch
from core.extensions.manifest import ExtensionManifest
from core.extensions.registry import Discovery, ExtensionEntry
from core.floors import send_home
from core.runtime.events import NullRuntimeEventSink, runtime_events

EXT = "fake-wake"
SENTENCE = "Couldn't reach Microsoft 365. Check your internet connection and try again."


def setup_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _entry(tmp_path: Path) -> ExtensionEntry:
    manifest = ExtensionManifest.model_validate({
        "id": EXT, "name": "Fake Wake", "version": "1", "description": "d",
        "command": {"name": "fakewake", "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False},
        "agent_config": {"label": "Fake", "help": "h", "fields": [
            {"key": "address", "label": "Address", "kind": "email"},
            {"key": "every", "label": "Every", "kind": "number", "min": 15, "max": 3600, "default": "90", "required": False},
        ]},
        "wake": {"interval_field": "every"},
    })
    return ExtensionEntry(id=EXT, root=tmp_path, manifest=manifest, invalid_reason=None)


class FakeWake:
    """A SupportsWake extension: scripted batches, recorded calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.batches: dict[str, list[WakeBatch | None]] = {}
        self.error: Exception | None = None

    def handle(self, ctx, parsed, body):
        raise AssertionError("not used")

    def shutdown(self) -> None:
        pass

    def poll_wake(self, agent_id: str) -> WakeBatch | None:
        self.calls.append(("poll", agent_id))
        if self.error is not None:
            raise self.error
        queue = self.batches.get(agent_id) or []
        return queue.pop(0) if queue else None

    def commit_wake(self, batch: WakeBatch) -> None:
        self.calls.append(("commit", batch.agent_id, batch.cursor))

    def skip_wake(self, agent_id: str) -> None:
        self.calls.append(("skip", agent_id))

    def describe_wake_error(self, exc: Exception) -> str:
        return SENTENCE


class FakeDispatcher:
    """Writes through db like the real one; can refuse or raise."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.calls: list[dict[str, Any]] = []

    def enqueue_trigger(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        if self.mode == "raise":
            raise RuntimeError("database is locked")
        if self.mode == "refuse":
            return False
        db.create_agent_trigger(**kwargs)
        return True


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    entry = _entry(tmp_path)
    ext = FakeWake()
    dispatcher = FakeDispatcher()
    enabled = {"ids": frozenset({EXT})}
    monkeypatch.setattr(wake_service, "get_discovery", lambda: Discovery(entries=(entry,)))
    monkeypatch.setattr(wake_service, "enabled_ids", lambda: enabled["ids"])
    monkeypatch.setattr(wake_service, "load_extension", lambda e: ext)
    monkeypatch.setattr(wake_service, "dispatcher", dispatcher)
    clock = Clock()
    watch = wake_service.ExtensionWakeWatch(clock=clock)
    return type("Env", (), {"entry": entry, "ext": ext, "dispatcher": dispatcher, "enabled": enabled,
                            "clock": clock, "watch": watch})


def _agent(name: str = "Iris", *, every: str | None = "30"):
    agent = db.create_agent(name, role="Researcher")
    values = {"address": f"{name.lower()}@contoso.com"}
    if every is not None:
        values["every"] = every
    db.set_extension_agent_config(EXT, agent.id, values)
    return agent


def _batch(agent_id: str, lines: list[str], cursor: str = "c1") -> WakeBatch:
    return WakeBatch(agent_id=agent_id, title="New email in iris@contoso.com", lines=lines, cursor=cursor)


def _events(agent_id: str) -> list[Any]:
    return [t for t in db.list_queued_triggers() if t.agent_id == agent_id and t.trigger_type == "extension_event"]


@pytest.mark.asyncio
async def test_a_batch_is_delivered_then_committed(env) -> None:
    agent = _agent()
    env.ext.batches[agent.id] = [_batch(agent.id, ["[m1] Alice — Hi"])]
    await env.watch.run_once()

    [event] = _events(agent.id)
    payload = json.loads(event.payload)
    assert event.source_channel == "system" and event.task_id is None
    assert payload == {
        "extension_id": EXT, "extension_name": "Fake Wake", "from_name": "Fake Wake",
        "title": "New email in iris@contoso.com", "lines": ["[m1] Alice — Hi"],
        "content": "New email in iris@contoso.com\n- [m1] Alice — Hi",
    }
    assert env.ext.calls == [("poll", agent.id), ("commit", agent.id, "c1")]
    status = db.get_wake_status(EXT, agent.id)
    assert status["ok"] is True and status["error"] is None and status["last_new_count"] == 1


@pytest.mark.parametrize("mode", ["raise", "refuse"])
@pytest.mark.asyncio
async def test_nothing_is_committed_when_the_enqueue_fails_or_is_refused(env, mode: str) -> None:
    agent = _agent()
    env.dispatcher.mode = mode
    env.ext.batches[agent.id] = [_batch(agent.id, ["[m1] Alice — Hi"])]
    await env.watch.run_once()
    assert env.ext.calls == [("poll", agent.id)]
    assert _events(agent.id) == []
    assert db.get_wake_status(EXT, agent.id) is None


@pytest.mark.asyncio
async def test_new_lines_merge_into_the_queued_event(env) -> None:
    agent = _agent()
    env.ext.batches[agent.id] = [
        _batch(agent.id, ["[m1] Alice — Hi"], cursor="c1"),
        _batch(agent.id, ["[m1] Alice — Hi", "[m2] Bob — Report"], cursor="c2"),
    ]
    await env.watch.run_once()
    env.clock.now += 30
    await env.watch.run_once()

    [event] = _events(agent.id)
    payload = json.loads(event.payload)
    assert payload["lines"] == ["[m1] Alice — Hi", "[m2] Bob — Report"]
    assert payload["content"] == "New email in iris@contoso.com\n- [m1] Alice — Hi\n- [m2] Bob — Report"
    assert len(env.dispatcher.calls) == 1, "the second batch merged; no second wake"
    assert [call for call in env.ext.calls if call[0] == "commit"] == [("commit", agent.id, "c1"), ("commit", agent.id, "c2")]


@pytest.mark.asyncio
async def test_a_trigger_claimed_in_between_gets_a_new_event(env, monkeypatch) -> None:
    agent = _agent()
    env.ext.batches[agent.id] = [_batch(agent.id, ["[m1] A"], cursor="c1"), _batch(agent.id, ["[m2] B"], cursor="c2")]
    await env.watch.run_once()
    [first] = _events(agent.id)
    real_update = db.update_queued_trigger_payload

    def claim_then_update(trigger_id, payload):
        db.claim_trigger(trigger_id)  # the dispatcher wins the race
        return real_update(trigger_id, payload)

    monkeypatch.setattr(wake_service.db, "update_queued_trigger_payload", claim_then_update)
    env.clock.now += 30
    await env.watch.run_once()

    assert db.get_agent_trigger(first.id).status == "claimed"
    assert json.loads(db.get_agent_trigger(first.id).payload)["lines"] == ["[m1] A"], "a claimed trigger is never modified"
    [second] = _events(agent.id)
    assert json.loads(second.payload)["lines"] == ["[m2] B"]
    assert ("commit", agent.id, "c2") in env.ext.calls


@pytest.mark.asyncio
async def test_only_due_agents_are_polled(env) -> None:
    fast = _agent("Iris", every="30")
    slow = _agent("Vera", every=None)  # saved before the field existed: the default 90
    await env.watch.run_once()
    assert sorted(call[1] for call in env.ext.calls) == sorted([fast.id, slow.id])
    env.ext.calls.clear()
    env.clock.now += 29
    await env.watch.run_once()
    assert env.ext.calls == []
    env.clock.now += 1
    await env.watch.run_once()
    assert env.ext.calls == [("poll", fast.id)]
    env.ext.calls.clear()
    env.clock.now += 60
    await env.watch.run_once()
    assert sorted(call[1] for call in env.ext.calls) == sorted([fast.id, slow.id])


@pytest.mark.asyncio
async def test_a_vacationer_is_skipped_not_polled_and_unconfigured_agents_are_ignored(env) -> None:
    away = _agent("Iris")
    db.create_agent("Nobody", role="Writer")  # no stored config
    send_home(away.id)
    db.record_wake_check(EXT, away.id, ok=True, error=None, new_count=0)
    before = db.get_wake_status(EXT, away.id)
    await env.watch.run_once()
    assert env.ext.calls == [("skip", away.id)]
    assert db.get_wake_status(EXT, away.id) == before, "a skip records nothing"


@pytest.mark.asyncio
async def test_poll_errors_are_recorded_and_logged_once_per_change(env, caplog) -> None:
    agent = _agent()
    env.ext.error = ConnectionError("dns failure")
    with caplog.at_level(logging.INFO, logger=wake_service.__name__):
        await env.watch.run_once()
        env.clock.now += 30
        await env.watch.run_once()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "dns failure" in warnings[0].getMessage()
        status = db.get_wake_status(EXT, agent.id)
        assert status["ok"] is False and status["error"] == SENTENCE

        env.ext.error = None
        env.clock.now += 30
        await env.watch.run_once()
        assert any(r.levelno == logging.INFO and "works again" in r.getMessage() for r in caplog.records)
    assert db.get_wake_status(EXT, agent.id)["ok"] is True


@pytest.mark.asyncio
async def test_a_disabled_extension_is_not_polled(env) -> None:
    _agent()
    env.enabled["ids"] = frozenset()
    await env.watch.run_once()
    assert env.ext.calls == []


@pytest.mark.asyncio
async def test_a_bad_interval_is_logged_and_other_agents_still_run(env, caplog) -> None:
    broken = _agent("Iris", every="soon")
    fine = _agent("Vera")
    with caplog.at_level(logging.WARNING, logger=wake_service.__name__):
        await env.watch.run_once()
        env.clock.now += 30
        await env.watch.run_once()
    assert all(call[1] != broken.id for call in env.ext.calls)
    assert ("poll", fine.id) in env.ext.calls
    assert len([r for r in caplog.records if "soon" in r.getMessage()]) == 1


def test_the_tick_setting_is_seeded_and_read() -> None:
    assert config.get("extension_wake_tick_seconds") == "5"
    assert wake_service.wake_tick_seconds() == wake_service.WAKE_TICK_SECONDS_FALLBACK == 5.0
    assert config.get("extension_wake_interval_seconds") is None


@pytest.mark.asyncio
async def test_the_paused_worker_does_not_run_the_service(monkeypatch) -> None:
    from core.runtime import worker

    class _Quiet:
        def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    for name in ("dispatcher", "simulation", "watchdog", "meeting_watchdog", "channel_idle_watch"):
        monkeypatch.setattr(worker, name, _Quiet())
    started: list[str] = []

    class _Watch:
        running = False

        def start(self) -> None:
            self.running = True
            started.append("start")

        async def stop(self) -> None:
            self.running = False

    watch = _Watch()
    monkeypatch.setattr(worker, "extension_wake_watch", watch)
    controller = worker.RuntimeController()
    await controller.boot(paused=True)
    assert started == [] and watch.running is False
    await controller.resume()
    assert watch.running is True
    await controller.pause()
    assert watch.running is False
