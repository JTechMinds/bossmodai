"""Phase 3 (refresh efficiency): ``broadcast_world_state`` coalesces bursts.

Every agent step and movement tick asks for a world broadcast. One send per
``world_state_coalesce_ms`` window serves a burst, it reads the database
off the event loop, and a call made while a send is already reading is
never lost: it gets one more send, which reads the newer state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import db
from api.websocket import ConnectionManager
from core import config
from db.crud import execute


def setup_function() -> None:
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


class _Recorder:
    """Stands in for the roster read and the socket fan-out."""

    def __init__(self) -> None:
        self.reads = 0
        self.sent: list[dict[str, Any]] = []
        self.read_threads: set[int] = set()
        self.state: Any = "v1"

    def read(self) -> Any:
        self.reads += 1
        self.read_threads.add(threading.get_ident())
        return self.state

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _manager(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> ConnectionManager:
    manager = ConnectionManager()
    monkeypatch.setattr(db, "get_world_state", recorder.read)
    monkeypatch.setattr(manager, "broadcast", recorder.broadcast)
    return manager


async def _settle(manager: ConnectionManager) -> None:
    """Wait for the pending send task, however many rounds it runs."""
    while manager._world_task is not None:
        await asyncio.wait_for(asyncio.shield(manager._world_task), timeout=5)


def test_the_coalesce_window_is_a_seeded_setting() -> None:
    assert config.require_int("world_state_coalesce_ms") == 150


async def test_twenty_calls_in_one_window_are_one_read_and_one_send(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    for _ in range(20):
        await manager.broadcast_world_state()
    await _settle(manager)

    assert recorder.reads == 1
    assert recorder.sent == [{"type": "world_update", "data": "v1"}]


async def test_the_await_returns_before_the_send(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    await manager.broadcast_world_state()

    assert recorder.sent == []
    await _settle(manager)
    assert len(recorder.sent) == 1


async def test_the_send_reads_the_state_at_send_time_not_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    await manager.broadcast_world_state()
    recorder.state = "v2"  # changed inside the window, after the call
    await _settle(manager)

    assert recorder.sent == [{"type": "world_update", "data": "v2"}]


async def test_a_call_during_an_in_flight_read_gets_one_more_send(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)
    reading = threading.Event()
    release = threading.Event()

    def slow_read() -> Any:
        state = recorder.read()
        reading.set()
        assert release.wait(timeout=5)
        return state

    monkeypatch.setattr(db, "get_world_state", slow_read)

    await manager.broadcast_world_state()
    assert await asyncio.to_thread(reading.wait, 5)
    # The first send has already read "v1"; these land while it is in flight.
    recorder.state = "v2"
    for _ in range(5):
        await manager.broadcast_world_state()
    release.set()
    await _settle(manager)

    assert recorder.reads == 2
    assert [message["data"] for message in recorder.sent] == ["v1", "v2"]


async def test_calls_in_separate_windows_are_separate_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    await manager.broadcast_world_state()
    await _settle(manager)
    recorder.state = "v2"
    await manager.broadcast_world_state()
    await _settle(manager)

    assert [message["data"] for message in recorder.sent] == ["v1", "v2"]


async def test_the_read_runs_off_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    await manager.broadcast_world_state()
    await _settle(manager)

    assert recorder.read_threads and threading.get_ident() not in recorder.read_threads


async def test_a_failed_read_is_logged_and_the_next_call_sends_again(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)

    def broken() -> Any:
        raise RuntimeError("roster read failed")

    monkeypatch.setattr(db, "get_world_state", broken)
    with caplog.at_level(logging.ERROR, logger="api.websocket"):
        await manager.broadcast_world_state()
        await _settle(manager)
    assert any("World state broadcast failed" in record.getMessage() for record in caplog.records)

    monkeypatch.setattr(db, "get_world_state", recorder.read)
    await manager.broadcast_world_state()
    await _settle(manager)
    assert [message["data"] for message in recorder.sent] == ["v1"]


async def test_a_missing_window_setting_raises_at_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    manager = _manager(monkeypatch, recorder)
    execute("DELETE FROM settings WHERE key = $1", ["world_state_coalesce_ms"])
    config.reload()

    with pytest.raises(config.ConfigError):
        await manager.broadcast_world_state()

