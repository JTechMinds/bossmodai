"""HA-STRUCT-P1-06 — RuntimeServices broadcasts through an injected sink."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

import db
from api.websocket import ConnectionManager
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.message_delivery import peer_message_event
from core.agent_loop.say_before_actions import merge_say_artifacts, say_already_posted
from core.models.message import Message
from core.runtime.events import (
    NullRuntimeEventSink,
    RuntimeEventProxy,
    TransportRuntimeEventSink,
    runtime_events,
)
from core.runtime import services as runtime_services_module
from core.runtime.services import RuntimeServices
from core.runtime.supervisor import WorkerOutage


def test_core_package_does_not_import_api() -> None:
    hits: list[str] = []
    root = Path(__file__).resolve().parents[1] / "core"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "from api." in stripped or stripped == "import api" or stripped.startswith("import api."):
                hits.append(f"{path.relative_to(root.parent)}: {stripped}")
    assert hits == []


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def broadcast_world_state(self) -> None:
        self.calls.append(("world_state", {}))

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        self.calls.append(("runtime_state", payload))

    async def broadcast_chat_message(self, **data: Any) -> None:
        self.calls.append(("chat_message", data))

    async def broadcast_meeting_message(self, **data: Any) -> None:
        self.calls.append(("meeting_message", data))

    async def broadcast_peer_message(self, **data: Any) -> None:
        self.calls.append(("peer_message", data))

    async def broadcast_channel_message(self, **data: Any) -> None:
        self.calls.append(("channel_message", data))

    async def broadcast_presence_snapshot(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append(("presence_snapshot", {"rows": rows}))

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        self.calls.append(("diagnostic", summary))

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        self.calls.append(
            ("thought", {"agent_id": agent_id, "thought": thought, "action_name": action_name})
        )

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(
            ("activity", {"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})
        )

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        self.calls.append(("feed_update", entry))

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        self.calls.append(("extension_live", {"extension_id": extension_id, "agent_id": agent_id}))

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        self.calls.append(("task_step", step))


@pytest.mark.asyncio
async def test_dispatch_event_uses_injected_sink() -> None:
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)

    await services._dispatch_event({"kind": "world_state", "data": {}})
    await services._dispatch_event({"kind": "chat_message", "data": {"agent_id": "a1", "content": "hi"}})
    await services._dispatch_event(
        {"kind": "activity", "data": {"event": "ping", "detail": "ok", "agent_name": "Alex"}}
    )

    assert sink.calls == [
        ("world_state", {}),
        ("chat_message", {"agent_id": "a1", "content": "hi"}),
        ("activity", {"event": "ping", "detail": "ok", "agent_name": "Alex", "extra": None}),
    ]


@pytest.mark.asyncio
async def test_dispatch_event_without_sink_does_not_raise() -> None:
    services = RuntimeServices()
    await services._dispatch_event({"kind": "world_state", "data": {}})
    await services._dispatch_event({"kind": "chat_message", "data": {"agent_id": "a1"}})


@pytest.mark.asyncio
async def test_proxy_broadcast_activity_forwards_peek_budget_in_extra() -> None:
    """Decision-turn fail-closed results splat peek_budget onto the proxy."""
    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)

    result = {
        "event": "agent_error",
        "detail": "Peek Clerk peek budget exhausted — decide or accept work",
        "agent_name": "Peek Clerk",
        "peek_budget": "soft_budget",
    }
    await proxy.broadcast_activity(**result)

    assert sink.calls == [
        (
            "activity",
            {
                "event": result["event"],
                "detail": result["detail"],
                "agent_name": result["agent_name"],
                "extra": {"peek_budget": "soft_budget"},
            },
        )
    ]

    await proxy.broadcast_activity(
        event="agent_error",
        detail="looping — decide or accept work",
        agent_name="Peek Clerk",
        extra={"task_id": "t1"},
        peek_budget="identical_loop",
    )
    assert sink.calls[-1][1]["extra"] == {
        "task_id": "t1",
        "peek_budget": "identical_loop",
    }

    proxy.set_sink(NullRuntimeEventSink())
    await proxy.broadcast_activity(**result)


# ─── extension_live (R37) ───


class _RecordingTransport:
    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []

    async def send_event(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)


@pytest.mark.asyncio
async def test_the_transport_sink_emits_the_extension_live_envelope() -> None:
    transport = _RecordingTransport()
    await TransportRuntimeEventSink(transport).broadcast_extension_live("browser-vision", "a1")
    assert transport.envelopes == [{
        "type": "event",
        "payload": {"kind": "extension_live", "data": {"extension_id": "browser-vision", "agent_id": "a1"}},
    }]


@pytest.mark.asyncio
async def test_the_proxy_forwards_extension_live() -> None:
    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)
    await proxy.broadcast_extension_live("browser-vision", "a1")
    assert sink.calls == [("extension_live", {"extension_id": "browser-vision", "agent_id": "a1"})]
    proxy.set_sink(NullRuntimeEventSink())
    await proxy.broadcast_extension_live(None, None)


@pytest.mark.asyncio
async def test_dispatch_event_routes_extension_live() -> None:
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    await services._dispatch_event(
        {"kind": "extension_live", "data": {"extension_id": "browser-vision", "agent_id": "a1"}}
    )
    assert sink.calls == [("extension_live", {"extension_id": "browser-vision", "agent_id": "a1"})]


@pytest.mark.asyncio
async def test_worker_ready_announces_every_extension_changed() -> None:
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    await services._handle_worker_message({"type": "ready"})
    assert sink.calls == [("extension_live", {"extension_id": None, "agent_id": None})]


class _EndedStdout:
    async def readline(self) -> bytes:
        return b""

    def close(self) -> None:
        return None


class _ExitedProcess:
    """A worker process that has already exited: stdout at EOF, returncode set."""

    def __init__(self) -> None:
        self.pid = None
        self.returncode = 0
        self.stdout = _EndedStdout()


@pytest.mark.asyncio
async def test_an_unexpected_worker_exit_announces_every_extension_changed() -> None:
    db.init_db()
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    services._process = _ExitedProcess()
    await services._read_worker_output()
    # Its turns died with it too: the empty presence snapshot goes first.
    assert sink.calls == [
        ("presence_snapshot", {"rows": []}),
        ("extension_live", {"extension_id": None, "agent_id": None}),
    ]
    assert db.get_runtime_worker_state("primary").lifecycle_state == "error"


@pytest.mark.asyncio
async def test_a_normal_stop_announces_every_extension_changed_once() -> None:
    db.init_db()
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    services._process = _ExitedProcess()
    services._process_loop = asyncio.get_running_loop()
    services._expecting_shutdown = True
    # The reader sees EOF on an expected shutdown: it leaves the announcement to the stop.
    await services._read_worker_output()
    assert sink.calls == []
    await services._stop_unlocked()
    assert sink.calls == [
        ("presence_snapshot", {"rows": []}),
        ("extension_live", {"extension_id": None, "agent_id": None}),
    ]


# ─── one bad line or one dropped client never ends the event stream ───


class _FakeClient:
    """A WebSocket stand-in: records each frame it takes and each close code.

    ``error`` fails every send with it. ``stuck`` makes every send wait
    forever, like a screen that stopped reading; ``sending`` is set once
    such a send has begun. ``pace`` sleeps that long before taking a frame,
    and ``accept`` yields once, so other tasks run while it connects.
    """

    def __init__(self, error: Exception | None = None, *, stuck: bool = False, pace: float = 0.0) -> None:
        self.error = error
        self.stuck = stuck
        self.pace = pace
        self.frames: list[dict[str, Any]] = []
        self.closes: list[int] = []
        self.sending = asyncio.Event()

    async def accept(self) -> None:
        await asyncio.sleep(0)

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        if self.stuck:
            self.sending.set()
            await asyncio.Event().wait()
        await asyncio.sleep(self.pace)
        self.frames.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closes.append(code)


def _no_initial_frames() -> list[dict[str, Any]]:
    return []


async def _connected(manager: ConnectionManager, *clients: _FakeClient) -> None:
    """Connect each fake screen with no initial frames, so a test counts only its own broadcasts."""
    for client in clients:
        await manager.connect(client, initial_frames=_no_initial_frames)


async def _left(manager: ConnectionManager, *clients: _FakeClient) -> None:
    """Disconnect each fake screen, as the ws route's ``finally`` does."""
    for client in clients:
        await manager.disconnect(client)


async def _screens_settle(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for the screens' senders")
        await asyncio.sleep(0.001)


def _screen_tasks() -> list[asyncio.Task[Any]]:
    return [task for task in asyncio.all_tasks() if task.get_name().startswith("websocket-screen")]


@pytest.fixture
def screen_settings() -> Iterator[Callable[..., None]]:
    """``screen_settings(key=value)`` sets WebSocket settings; each is put back to its seed afterwards.

    This module shares one database per worker, so the seeds are put back
    and re-read before the next test.
    """
    db.init_db()
    changed: set[str] = set()

    def apply(**values: str) -> None:
        for key, value in values.items():
            db.set_setting(key, value, "advanced")
            changed.add(key)
        config.reload()

    try:
        yield apply
    finally:
        for key in changed:
            db.reset_setting_to_seed(key)
        config.reload()


@pytest.mark.asyncio
async def test_broadcast_drops_a_client_that_disconnects_mid_send_and_still_delivers() -> None:
    """Starlette turns a socket error mid-send into ``WebSocketDisconnect``; it must stay in the fan-out."""
    db.init_db()
    manager = ConnectionManager()
    # First, so a failure that escaped would also cost the second client its frame.
    dropped = _FakeClient(WebSocketDisconnect(code=1006))
    live = _FakeClient()
    await _connected(manager, dropped, live)

    await manager.broadcast({"type": "thought", "data": {"agent_id": "a1"}})
    await _screens_settle(lambda: manager.connection_count == 1 and len(live.frames) == 1)

    assert live.frames == [{"type": "thought", "data": {"agent_id": "a1"}}]
    # Already gone: only unregistered, nothing to close.
    assert dropped.frames == [] and dropped.closes == []
    await manager.broadcast({"type": "world_update", "data": {}})
    await _screens_settle(lambda: len(live.frames) == 2)
    assert live.frames[-1] == {"type": "world_update", "data": {}}
    await _left(manager, dropped, live)


@pytest.mark.asyncio
async def test_broadcast_without_its_send_timeout_setting_drops_the_screen_and_logs_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No ``or 5.0``: a missing seed is a broken database, and it is said, not guessed.

    ``broadcast`` no longer reads the timeout; the screen's sender does, says
    so at ERROR, and drops the screen with 1013 so its browser reconnects.
    """
    db.init_db()
    manager = ConnectionManager()
    client = _FakeClient()
    await _connected(manager, client)
    sender = manager._connections[client].sender
    assert sender is not None
    db.execute("DELETE FROM settings WHERE key = $1", ["ws_send_timeout_seconds"])
    config.reload()
    try:
        with caplog.at_level(logging.ERROR, logger="api.websocket"):
            # Only this block: the connect above may have logged at INFO.
            caplog.clear()
            await manager.broadcast({"type": "thought", "data": {"agent_id": "a1"}})
            await asyncio.wait_for(asyncio.shield(sender), timeout=2.0)

        assert client.frames == []
        assert manager.connection_count == 0
        assert client.closes == [1013]
        errors = [record for record in caplog.records if record.name == "api.websocket"]
        # The sender says it cannot send; the close says it runs without its bound.
        assert [record.getMessage() for record in errors] == [
            "Cannot send to a screen; dropping it so it reconnects",
            "Closing a screen without its ws_send_timeout_seconds bound",
        ]
        for record in errors:
            assert record.levelno == logging.ERROR
            assert record.exc_info is not None and isinstance(record.exc_info[1], config.ConfigError)
            assert "ws_send_timeout_seconds" in str(record.exc_info[1])
    finally:
        # This module shares one database per worker, so the seed is put back
        # and re-read before the next test broadcasts.
        db.reset_setting_to_seed("ws_send_timeout_seconds")
        config.reload()


@pytest.mark.asyncio
async def test_broadcast_returns_while_a_screen_is_stuck_and_a_fast_screen_gets_every_frame(
    screen_settings: Callable[..., None],
) -> None:
    # Far beyond the test, so the stuck screen is never dropped for its timeout here.
    screen_settings(ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    stuck, fast = _FakeClient(stuck=True), _FakeClient()
    await _connected(manager, stuck, fast)
    frames = [{"type": "agent_thought", "data": {"n": n}} for n in range(20)]

    await manager.broadcast(frames[0])
    await asyncio.wait_for(stuck.sending.wait(), timeout=2.0)
    # The stuck screen's send is now blocked forever; each broadcast still returns at once.
    for frame in frames[1:]:
        await asyncio.wait_for(manager.broadcast(frame), timeout=1.0)

    await _screens_settle(lambda: len(fast.frames) == len(frames))
    assert fast.frames == frames
    assert stuck.frames == [] and stuck.closes == []
    assert manager.connection_count == 2
    await _left(manager, stuck, fast)


@pytest.mark.asyncio
async def test_each_screen_gets_every_broadcast_in_order(screen_settings: Callable[..., None]) -> None:
    screen_settings(ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    # Senders at different paces, so broadcasts land while each is mid-send.
    screens = [_FakeClient(), _FakeClient(pace=0.0005), _FakeClient(pace=0.001)]
    await _connected(manager, *screens)
    frames = [{"type": "activity", "data": {"n": n}} for n in range(200)]

    for n, frame in enumerate(frames):
        await manager.broadcast(frame)
        if n % 7 == 0:
            await asyncio.sleep(0)

    await _screens_settle(lambda: all(len(screen.frames) == len(frames) for screen in screens), timeout=5.0)
    for screen in screens:
        assert screen.frames == frames
    await _left(manager, *screens)


@pytest.mark.asyncio
async def test_a_screen_that_falls_behind_is_dropped_with_1013_and_a_warning(
    screen_settings: Callable[..., None], caplog: pytest.LogCaptureFixture,
) -> None:
    screen_settings(ws_client_outbox_size="2", ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    stuck, fast = _FakeClient(stuck=True), _FakeClient()
    await _connected(manager, stuck, fast)
    stuck_sender = manager._connections[stuck].sender
    assert stuck_sender is not None
    frames = [{"type": "agent_thought", "data": {"n": n}} for n in range(5)]

    with caplog.at_level(logging.WARNING, logger="api.websocket"):
        caplog.clear()
        await manager.broadcast(frames[0])
        # Frame 0 is the stuck screen's blocked send; frames 1 and 2 fill its outbox.
        await asyncio.wait_for(stuck.sending.wait(), timeout=2.0)
        for n in (1, 2, 3):
            await manager.broadcast(frames[n])
            # The fast screen keeps up, so its own outbox of 2 never fills.
            await _screens_settle(lambda n=n: len(fast.frames) == n + 1)
        # Frame 3 found the stuck outbox full: unregistered at once, closed by a task of its own.
        assert manager.connection_count == 1
        await _screens_settle(lambda: stuck.closes == [1013] and not manager._drop_tasks)
        await manager.broadcast(frames[4])
        await _screens_settle(lambda: len(fast.frames) == 5)

    assert fast.frames == frames
    assert stuck.frames == []
    assert stuck_sender.cancelled()
    warnings = [record for record in caplog.records if record.name == "api.websocket"]
    assert [(record.levelno, record.getMessage()) for record in warnings] == [
        (logging.WARNING, "Dropped a screen that fell 2 updates behind; it will reconnect and catch up"),
    ]
    await _left(manager, stuck, fast)


@pytest.mark.asyncio
async def test_a_send_timeout_closes_the_screen_with_1013(
    screen_settings: Callable[..., None], caplog: pytest.LogCaptureFixture,
) -> None:
    """A screen that timed out used to be forgotten but left open: its browser never resynced."""
    screen_settings(ws_send_timeout_seconds="0.05")
    manager = ConnectionManager()
    stuck = _FakeClient(stuck=True)
    await _connected(manager, stuck)

    with caplog.at_level(logging.INFO, logger="api.websocket"):
        # Only this block: the connect above may have logged at INFO.
        caplog.clear()
        await manager.broadcast({"type": "agent_thought", "data": {"n": 0}})
        await _screens_settle(lambda: stuck.closes == [1013])

    assert manager.connection_count == 0
    assert [record.getMessage() for record in caplog.records if record.name == "api.websocket"] == [
        "A screen did not take an update within 0.05s; dropping it so it reconnects and catches up",
    ]
    await _left(manager, stuck)


@pytest.mark.asyncio
async def test_connect_queues_the_initial_frames_before_any_broadcast(
    screen_settings: Callable[..., None],
) -> None:
    screen_settings(ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    at = datetime(2026, 11, 26, 13, 3, tzinfo=timezone.utc)
    initial = [
        {"type": "world_update", "data": {"at": at}},
        {"type": "unified_feed", "data": {"items": []}},
        {"type": "runtime_state", "data": {"lifecycle_state": "running"}},
        {"type": "presence_snapshot", "data": []},
    ]
    client = _FakeClient()
    # The broadcasts made while the screen was registered, by number.
    registered: list[int] = []
    stop = asyncio.Event()

    async def broadcaster() -> None:
        # Broadcasts on every loop turn, including while the screen is being accepted.
        n = 0
        while not stop.is_set():
            if client in manager._connections:
                registered.append(n)
            await manager.broadcast({"type": "agent_presence", "data": {"n": n}})
            n += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(broadcaster())
    await asyncio.sleep(0)
    await manager.connect(client, initial_frames=lambda: initial)
    right_after = {"type": "agent_presence", "data": {"n": "right after connect"}}
    await manager.broadcast(right_after)
    await _screens_settle(lambda: len(registered) >= 5)
    stop.set()
    await task
    await _screens_settle(lambda: len(client.frames) == len(initial) + 1 + len(registered))

    # Encoded once on the way in, as broadcasts are.
    assert client.frames[:4] == [{"type": "world_update", "data": {"at": at.isoformat()}}, *initial[1:]]
    assert client.frames[4] == right_after
    assert client.frames[5:] == [{"type": "agent_presence", "data": {"n": n}} for n in registered]
    await _left(manager, client)


@pytest.mark.asyncio
async def test_disconnect_cancels_the_sender_and_leaves_no_task_behind(
    screen_settings: Callable[..., None],
) -> None:
    screen_settings(ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    # One sender waits for its next frame; the other is blocked mid-send.
    idle, stuck = _FakeClient(), _FakeClient(stuck=True)
    await _connected(manager, idle, stuck)
    senders = [manager._connections[client].sender for client in (idle, stuck)]
    await manager.broadcast({"type": "agent_thought", "data": {"n": 0}})
    await asyncio.wait_for(stuck.sending.wait(), timeout=2.0)
    await _screens_settle(lambda: len(idle.frames) == 1)

    await _left(manager, idle, stuck)

    assert all(sender is not None and sender.cancelled() for sender in senders)
    assert manager.connection_count == 0
    assert _screen_tasks() == []
    # It left: nothing is closed, and a second disconnect does nothing.
    assert idle.closes == [] and stuck.closes == []
    await _left(manager, idle, stuck)
    assert manager.connection_count == 0


class _RosterReadFailed(Exception):
    """What an ``initial_frames`` builder raises in the connect-failure test."""


@pytest.mark.asyncio
async def test_a_connect_whose_initial_frames_fail_closes_with_1011_and_registers_nothing(
    screen_settings: Callable[..., None],
) -> None:
    screen_settings(ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    client = _FakeClient()

    def failing_frames() -> list[dict[str, Any]]:
        raise _RosterReadFailed("world state read failed")

    with pytest.raises(_RosterReadFailed, match="world state read failed"):
        await manager.connect(client, initial_frames=failing_frames)

    # Accepted, then closed so the browser is not left connected and silent.
    assert client.closes == [1011]
    assert client.frames == []
    assert manager.connection_count == 0 and client not in manager._connections
    assert _screen_tasks() == []


@pytest.mark.asyncio
async def test_an_outbox_smaller_than_the_initial_frames_is_a_config_error_and_registers_nothing(
    screen_settings: Callable[..., None],
) -> None:
    screen_settings(ws_client_outbox_size="3", ws_send_timeout_seconds="60")
    manager = ConnectionManager()
    client = _FakeClient()
    initial = [{"type": "world_update", "data": {"n": n}} for n in range(4)]

    with pytest.raises(config.ConfigError, match="ws_client_outbox_size"):
        await manager.connect(client, initial_frames=lambda: initial)

    assert client.closes == [1011]
    assert client.frames == []
    assert manager.connection_count == 0 and client not in manager._connections
    assert _screen_tasks() == []


class _ScriptedStdout:
    """A worker stdout that replays scripted lines, raising any that are exceptions, then EOF."""

    def __init__(self, items: list[bytes | Exception]) -> None:
        self._items = list(items)

    async def readline(self) -> bytes:
        if not self._items:
            return b""
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        return None


class _ScriptedProcess:
    """An exited worker whose stdout replays ``items`` before EOF."""

    def __init__(self, items: list[bytes | Exception]) -> None:
        self.pid = None
        self.returncode = 0
        self.stdout = _ScriptedStdout(items)


class _DisconnectingThoughtSink(_RecordingSink):
    """Fails every thought the way a browser dropping mid-send did on 2026-11-26."""

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        raise WebSocketDisconnect(code=1006)


def _line(message: dict[str, Any]) -> bytes:
    return json.dumps(message).encode("utf-8") + b"\n"


@pytest.mark.asyncio
async def test_the_reader_drops_each_bad_line_with_an_error_and_keeps_reading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.init_db()
    services = RuntimeServices()
    sink = _DisconnectingThoughtSink()
    services.set_event_sink(sink)
    thought = {"agent_id": "a1", "thought": "Checking the build", "action_name": "cli"}
    services._process = _ScriptedProcess([
        _line({"type": "event", "payload": {"kind": "thought", "data": thought}}),
        b"Downloading weights...\n",
        b"\xff\xfe\n",
        b"[1, 2]\n",
        ValueError("Separator is not found, and chunk exceed the limit"),
        _line({"type": "event", "payload": {"kind": "world_state", "data": {}}}),
    ])

    with caplog.at_level(logging.ERROR, logger="core.runtime.services"):
        await services._read_worker_output()

    errors = [
        record for record in caplog.records
        if record.name == "core.runtime.services" and record.levelno == logging.ERROR
    ]
    assert [record.msg for record in errors] == [
        "Runtime worker %s event was not delivered; reading continues",
        "Dropped a runtime worker output line that is not UTF-8 JSON (%s): %r",
        "Dropped a runtime worker output line that is not UTF-8 JSON (%s): %r",
        "Dropped a runtime worker output line that is not a JSON object: %r",
        "Dropped a runtime worker output line over the stream limit: %s",
    ]
    assert errors[0].args == ("thought",)
    assert errors[0].exc_info is not None and isinstance(errors[0].exc_info[1], WebSocketDisconnect)
    assert "b'Downloading weights...\\n'" in errors[1].getMessage()
    assert "b'[1, 2]\\n'" in errors[3].getMessage()
    # The event after every bad line still arrived; then the stream's end was announced.
    assert sink.calls == [
        ("world_state", {}),
        ("presence_snapshot", {"rows": []}),
        ("extension_live", {"extension_id": None, "agent_id": None}),
    ]


@pytest.mark.asyncio
async def test_the_reader_never_waits_on_a_screen() -> None:
    """The agents' pace is never a screen's pace (the 2026-11-26 freeze).

    With the real ``ConnectionManager`` as the sink and one screen whose
    sends never return, the reader still takes every worker event and
    reaches EOF at once; a second screen gets each event in order.
    """
    db.init_db()
    manager = ConnectionManager()
    stuck, fast = _FakeClient(stuck=True), _FakeClient()
    await _connected(manager, stuck, fast)
    services = RuntimeServices()
    services.set_event_sink(manager)
    thoughts = [{"agent_id": "a1", "thought": f"Step {n}", "action_name": "cli"} for n in range(50)]
    services._process = _ScriptedProcess(
        [_line({"type": "event", "payload": {"kind": "thought", "data": thought}}) for thought in thoughts]
    )

    try:
        # A stalled send used to hold the reader for ws_send_timeout_seconds (5 s).
        await asyncio.wait_for(services._read_worker_output(), timeout=2.0)

        await _screens_settle(lambda: len(fast.frames) == len(thoughts) + 2)
        assert fast.frames[:50] == [{"type": "agent_thought", "data": thought} for thought in thoughts]
        assert [frame["type"] for frame in fast.frames[50:]] == ["presence_snapshot", "extension_live"]
        # Still within its outbox, so still registered: only its own delivery waits.
        assert stuck.frames == [] and manager.connection_count == 2
    finally:
        # The stream's end asked for an automatic restart; none is wanted here.
        await runtime_services_module._cancel_and_wait(services._recovery_task)
        await _left(manager, stuck, fast)


# ─── a stream that ends while the worker still runs: bounded wait, then fail-stop ───


_EXIT_ANNOUNCED = [
    ("presence_snapshot", {"rows": []}),
    ("extension_live", {"extension_id": None, "agent_id": None}),
]


class _RunningProcess:
    """A worker whose ``returncode`` is still unset when its stdout ends.

    Built with ``exits=True``, ``wait()`` sets ``returncode`` itself, as
    asyncio does a few loop turns after EOF; otherwise it returns only after
    ``terminate()`` or ``kill()``. ``on_wait`` runs whenever a wait begins.
    """

    def __init__(
        self,
        items: list[bytes | Exception],
        *,
        exits: bool,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        self.pid = None
        self.returncode: int | None = None
        self.stdout = _ScriptedStdout(items)
        self.terminations = 0
        self._exits = exits
        self._on_wait = on_wait
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        if self._on_wait is not None:
            self._on_wait()
        if self._exits and self.returncode is None:
            await asyncio.sleep(0)
            self.returncode = 0
            self._exited.set()
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminations += 1
        self.returncode = -15
        self._exited.set()

    def kill(self) -> None:
        self.terminate()


@pytest.fixture
def short_worker_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker gets 10 ms, not seconds, to show its exit after its stream ends."""
    monkeypatch.setattr(runtime_services_module, "_WORKER_STOP_TIMEOUT_SECONDS", 0.01)


def _reader_for(process: _RunningProcess) -> tuple[RuntimeServices, _RecordingSink]:
    """A reader on ``process``, with the worker row reset to ``running``."""
    db.init_db()
    db.mark_runtime_worker_running(worker_name="primary")
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    services._process = process
    return services, sink


@pytest.mark.asyncio
async def test_a_worker_still_running_after_eof_is_stopped(
    short_worker_wait: None, caplog: pytest.LogCaptureFixture,
) -> None:
    process = _RunningProcess([], exits=False)
    services, sink = _reader_for(process)

    with caplog.at_level(logging.ERROR, logger="core.runtime.services"):
        await services._read_worker_output()

    assert process.terminations == 1
    state = db.get_runtime_worker_state("primary")
    assert state is not None and state.lifecycle_state == "error"
    assert state.last_error == "Runtime worker stopped: its output stream ended while it was running (EOF)"
    assert sink.calls == _EXIT_ANNOUNCED
    assert [record.getMessage() for record in caplog.records if record.name == "core.runtime.services"] == [
        "Runtime worker output stream ended while the worker was running (EOF); stopping the worker",
    ]


@pytest.mark.asyncio
async def test_a_worker_that_exits_within_the_wait_is_an_unexpected_exit(short_worker_wait: None) -> None:
    process = _RunningProcess([], exits=True)
    services, sink = _reader_for(process)

    await services._read_worker_output()

    assert process.terminations == 0
    state = db.get_runtime_worker_state("primary")
    assert state is not None and state.lifecycle_state == "error"
    assert state.last_error == "Runtime worker exited unexpectedly"
    assert sink.calls == _EXIT_ANNOUNCED

    # The worker's own reason (its fatal message) is never overwritten.
    process = _RunningProcess([], exits=True)
    services, sink = _reader_for(process)
    db.mark_runtime_worker_error("Runtime worker failed: no such table", worker_name="primary")

    await services._read_worker_output()

    assert process.terminations == 0
    state = db.get_runtime_worker_state("primary")
    assert state is not None and state.last_error == "Runtime worker failed: no such table"
    assert sink.calls == _EXIT_ANNOUNCED


@pytest.mark.asyncio
async def test_a_stop_that_begins_during_the_wait_owns_the_exit(short_worker_wait: None) -> None:
    services: RuntimeServices | None = None

    def stop_begins() -> None:
        assert services is not None
        services._expecting_shutdown = True

    process = _RunningProcess([], exits=False, on_wait=stop_begins)
    services, sink = _reader_for(process)

    await services._read_worker_output()

    assert process.terminations == 0
    state = db.get_runtime_worker_state("primary")
    assert state is not None and state.lifecycle_state == "running" and state.last_error is None
    assert sink.calls == []


@pytest.mark.asyncio
async def test_a_failed_stream_with_the_worker_still_running_stops_it(short_worker_wait: None) -> None:
    process = _RunningProcess([ConnectionResetError("Connection lost")], exits=False)
    services, sink = _reader_for(process)

    await services._read_worker_output()

    assert process.terminations == 1
    state = db.get_runtime_worker_state("primary")
    assert state is not None and state.lifecycle_state == "error"
    assert state.last_error == (
        "Runtime worker stopped: its output stream ended while it was running "
        "(ConnectionResetError: Connection lost)"
    )
    assert sink.calls == _EXIT_ANNOUNCED


# ─── supervision: automatic restarts, the crash-loop guard, the frozen-worker watch ───


# Above Linux's largest pid (4194304), so a fake worker never names a real process.
_FAKE_PID_BASE = 5_000_000


def _pid(n: int) -> int:
    return _FAKE_PID_BASE + n


class _FakeStdin:
    """The doorbell pipe: always open, and nothing listens."""

    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        return None


class _FakeWorkerStdout:
    def __init__(self, worker: "_FakeWorker") -> None:
        self._worker = worker

    async def readline(self) -> bytes:
        return await self._worker.next_line()

    def close(self) -> None:
        return None


class _FakeWorker:
    """A runtime worker stand-in for ``_start_unlocked``, as ``asyncio.create_subprocess_exec`` returns it.

    It marks its row ``running`` and says ready, as the real worker does
    (``ready=False``: it exits before ready instead), then runs until it is
    terminated or ``crash()``-ed. ``crash_after_ready`` exits right after the
    ready line; ``heartbeats`` stamps its heartbeat every 5 ms, otherwise it
    never does (a frozen worker).
    """

    def __init__(self, pid: int, *, ready: bool = True, crash_after_ready: bool = False, heartbeats: bool = False) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = _FakeStdin()
        self.stdout: _FakeWorkerStdout | None = _FakeWorkerStdout(self)
        self.terminations = 0
        self._ready = ready
        self._crash_after_ready = crash_after_ready
        self._heartbeats = heartbeats
        self._said_ready = False
        self._exited = asyncio.Event()
        self._beats: asyncio.Task[None] | None = None

    async def next_line(self) -> bytes:
        if not self._said_ready:
            self._said_ready = True
            if not self._ready:
                self._exit(1)
                return b""
            db.mark_runtime_worker_running(self.pid, worker_name="primary")
            if self._heartbeats:
                self._beats = asyncio.create_task(self._beat())
            return _line({"type": "ready"})
        if self._crash_after_ready:
            self._exit(1)
        await self._exited.wait()
        return b""

    async def _beat(self) -> None:
        while self.returncode is None:
            db.record_runtime_worker_heartbeat(self.pid, worker_name="primary")
            await asyncio.sleep(0.005)

    def _exit(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
        self._exited.set()

    def crash(self) -> None:
        self._exit(1)

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminations += 1
        self._exit(-15)

    def kill(self) -> None:
        self.terminate()


class _Spawner:
    """Stands in for ``asyncio.create_subprocess_exec``: hands out the scripted workers in order."""

    def __init__(self, workers: tuple[_FakeWorker, ...]) -> None:
        self._waiting = list(workers)
        self.spawned: list[_FakeWorker] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> _FakeWorker:
        if not self._waiting:
            raise RuntimeError("an unscripted runtime worker start")
        worker = self._waiting.pop(0)
        self.spawned.append(worker)
        return worker


class _RecordingBridge:
    """The Telegram bridge as the dispatch queue's consumer sees it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append((kind, data))


@pytest.fixture
def supervision(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Restarts after 10 ms and stops that take 10 ms; ``supervision(key=value)`` sets more.

    Every changed setting is put back to its seed afterwards: this module
    shares one database per worker.
    """
    db.init_db()
    monkeypatch.setattr(runtime_services_module, "_WORKER_STOP_TIMEOUT_SECONDS", 0.01)
    changed: set[str] = set()

    def apply(**values: str) -> None:
        for key, value in values.items():
            db.set_setting(key, value, "advanced")
            changed.add(key)
        config.reload()

    apply(runtime_worker_restart_delay_seconds="0.01")
    try:
        yield apply
    finally:
        for key in changed:
            db.reset_setting_to_seed(key)
        config.reload()


def _spawn(monkeypatch: pytest.MonkeyPatch, *workers: _FakeWorker) -> _Spawner:
    spawner = _Spawner(workers)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    return spawner


def _supervised() -> tuple[RuntimeServices, _RecordingSink]:
    services = RuntimeServices()
    sink = _RecordingSink()
    services.set_event_sink(sink)
    return services, sink


def _activities(sink: _RecordingSink) -> list[tuple[str, str]]:
    return [(data["event"], data["detail"]) for kind, data in sink.calls if kind == "activity"]


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for the runtime worker's supervision")
        await asyncio.sleep(0.005)


def _recovered(services: RuntimeServices) -> bool:
    task = services._recovery_task
    return task is not None and task.done()


_RESTARTED_UNEXPECTEDLY = ("runtime_worker_restarted", "AI runtime restarted after it stopped unexpectedly.")


@pytest.mark.asyncio
async def test_an_unexpected_exit_restarts_the_worker_once_and_logs_it_once(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _FakeWorker(_pid(1)), _FakeWorker(_pid(2))
    spawner = _spawn(monkeypatch, first, second)
    services, sink = _supervised()
    await services.start()
    try:
        first.crash()
        await _until(lambda: _recovered(services))

        assert spawner.spawned == [first, second]
        assert services._process is second
        assert _activities(sink) == [_RESTARTED_UNEXPECTEDLY]
        assert services.outage() is None
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_restarts_that_keep_failing_stop_at_the_limit_and_tell_the_operator(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_worker_restart_limit="2")
    first = _FakeWorker(_pid(1))
    spawner = _spawn(monkeypatch, first, _FakeWorker(_pid(2), ready=False), _FakeWorker(_pid(3), ready=False))
    services, sink = _supervised()
    bridge = _RecordingBridge()
    services.set_telegram_bridge(bridge)
    await services.start()
    try:
        first.crash()
        await _until(lambda: services.outage() is not None and bool(bridge.events))

        outage = services.outage()
        assert outage is not None
        assert (outage.reason, outage.attempts) == ("it could not start", 2)
        assert len(spawner.spawned) == 3
        down = {
            "event": "runtime_worker_down",
            "detail": (
                "Your agents can't work until it runs again. "
                "BossMod tried to restart it 2 times; the last time, it could not start."
            ),
            "agent_name": None,
            "extra": {"since": outage.since.isoformat(), "attempts": 2, "reason": "it could not start"},
        }
        assert [data for kind, data in sink.calls if kind == "activity"] == [down]
        assert bridge.events == [("activity", down)]
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_an_expected_stop_schedules_no_restart(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _FakeWorker(_pid(1))
    spawner = _spawn(monkeypatch, worker)
    services, sink = _supervised()
    await services.start()

    await services.stop()
    await asyncio.sleep(0.05)

    assert worker.terminations == 1
    assert services._recovery_task is None
    assert spawner.spawned == [worker]
    assert _activities(sink) == []


@pytest.mark.asyncio
async def test_stop_cancels_a_pending_restart(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_worker_restart_delay_seconds="60")
    worker = _FakeWorker(_pid(1))
    spawner = _spawn(monkeypatch, worker)
    services, sink = _supervised()
    await services.start()
    worker.crash()
    await _until(lambda: services._recovery_task is not None)
    pending = services._recovery_task
    assert pending is not None

    await services.stop()

    assert pending.cancelled()
    assert spawner.spawned == [worker]
    assert _activities(sink) == []


@pytest.mark.asyncio
async def test_a_frozen_worker_is_stopped_restarted_and_logged(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_heartbeat_seconds="0.01", runtime_worker_unresponsive_seconds="0.1")
    frozen, healthy = _FakeWorker(_pid(1)), _FakeWorker(_pid(2), heartbeats=True)
    spawner = _spawn(monkeypatch, frozen, healthy)
    services, sink = _supervised()
    await services.start()
    try:
        await _until(lambda: bool(_activities(sink)))

        assert frozen.terminations == 1
        assert services._process is healthy
        assert _activities(sink) == [
            ("runtime_worker_restarted", "AI runtime restarted after it stopped responding."),
        ]
        # Its turns went with it: the empty presence snapshot was announced.
        assert ("presence_snapshot", {"rows": []}) in sink.calls
        # A worker whose heartbeat moves is left alone.
        await asyncio.sleep(0.3)
        assert healthy.terminations == 0
        assert spawner.spawned == [frozen, healthy]
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_a_frozen_worker_replaced_before_its_kill_takes_the_guard_leaves_the_new_worker_alone(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_heartbeat_seconds="0.01", runtime_worker_unresponsive_seconds="0.1")
    frozen, fresh = _FakeWorker(_pid(1)), _FakeWorker(_pid(2), heartbeats=True)
    spawner = _spawn(monkeypatch, frozen, fresh)
    services, sink = _supervised()
    await services.start()
    guard = services._guard()
    # Held, so the recovery the watch asks for waits on it, as behind a Pause.
    await guard.acquire()
    try:
        await _until(lambda: services._recovery_task is not None and services._incident is not None)
        # Meanwhile the frozen worker exits on its own and a start replaces it.
        frozen.crash()
        await _until(lambda: services._reader_task is not None and services._reader_task.done())
        await services._start_unlocked()
        assert services._process is fresh
    finally:
        guard.release()
    try:
        await _until(lambda: _recovered(services))

        assert fresh.terminations == 0 and frozen.terminations == 0
        assert services._process is fresh and spawner.spawned == [frozen, fresh]
        state = db.get_runtime_worker_state("primary")
        assert state is not None and state.lifecycle_state == "running" and state.pid == fresh.pid
        # The start that replaced it ended the incident: one restart line.
        assert _activities(sink) == [
            ("runtime_worker_restarted", "AI runtime restarted after it stopped responding."),
        ]
    finally:
        await services.stop()


def test_a_kill_recovery_names_its_target_and_only_a_kill_does() -> None:
    services = RuntimeServices()
    with pytest.raises(ValueError, match="target process exactly when it kills"):
        services._schedule_recovery("it stopped responding", kill=True)
    with pytest.raises(ValueError, match="target process exactly when it kills"):
        services._schedule_recovery("it stopped unexpectedly", kill=False, target=_FakeWorker(_pid(1)))
    assert services._recovery_task is None


@pytest.mark.asyncio
async def test_a_stale_heartbeat_on_another_pid_or_a_row_not_running_is_left_alone(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_heartbeat_seconds="0.01", runtime_worker_unresponsive_seconds="0.05")
    worker = _FakeWorker(_pid(1))
    _spawn(monkeypatch, worker)
    services, sink = _supervised()
    await services.start()
    try:
        # A row that names a process other than this app's child, never refreshed.
        db.mark_runtime_worker_running(_pid(99), worker_name="primary")
        await asyncio.sleep(0.3)
        assert services._recovery_task is None and worker.terminations == 0

        # This child's row, but not running.
        db.mark_runtime_worker_stopping(worker.pid, worker_name="primary")
        await asyncio.sleep(0.3)
        assert services._recovery_task is None and worker.terminations == 0
        assert _activities(sink) == []
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_restart_now_clears_the_outage_and_the_restart_history(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _FakeWorker(_pid(1))
    _spawn(monkeypatch, worker)
    services, sink = _supervised()
    services._incident = "it stopped responding"
    services._outage = WorkerOutage(since=datetime.now(timezone.utc), reason="it stopped responding", attempts=3)
    for _ in range(3):
        services._restart_history.record(time.monotonic(), window_seconds=600)
    try:
        payload = await services.restart_worker()

        assert services.outage() is None
        assert services._process is worker
        assert payload["worker"]["state"] == "running" and payload["worker"]["pid"] == worker.pid
        assert _activities(sink) == [
            ("runtime_worker_restarted", "AI runtime restarted after it stopped responding."),
        ]
        assert services._restart_history.record(time.monotonic(), window_seconds=600) == 1
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_restart_now_cancels_a_pending_automatic_restart(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_worker_restart_delay_seconds="60")
    first, second = _FakeWorker(_pid(1)), _FakeWorker(_pid(2))
    spawner = _spawn(monkeypatch, first, second)
    services, sink = _supervised()
    await services.start()
    try:
        first.crash()
        await _until(lambda: services._recovery_task is not None)
        pending = services._recovery_task
        assert pending is not None

        await services.restart_worker()

        assert pending.cancelled()
        assert spawner.spawned == [first, second] and services._process is second
        assert _activities(sink) == [_RESTARTED_UNEXPECTEDLY]
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_a_failed_restart_now_is_handed_back_to_automatic_restarts(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing, healthy = _FakeWorker(_pid(1), ready=False), _FakeWorker(_pid(2))
    spawner = _spawn(monkeypatch, failing, healthy)
    services, sink = _supervised()
    services._incident = "it stopped responding"
    services._outage = WorkerOutage(since=datetime.now(timezone.utc), reason="it stopped responding", attempts=3)
    try:
        with pytest.raises(RuntimeError, match="exited before ready"):
            await services.restart_worker()
        assert services.outage() is None

        await _until(lambda: _recovered(services))

        assert spawner.spawned == [failing, healthy] and services._process is healthy
        # The first cause is why it stopped; the log line keeps it.
        assert _activities(sink) == [
            ("runtime_worker_restarted", "AI runtime restarted after it stopped responding."),
        ]
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_a_restart_someone_else_made_during_the_delay_is_left_alone(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervision(runtime_worker_restart_delay_seconds="0.2")
    first, second = _FakeWorker(_pid(1)), _FakeWorker(_pid(2))
    spawner = _spawn(monkeypatch, first, second)
    services, sink = _supervised()
    await services.start()
    try:
        first.crash()
        await _until(lambda: services._recovery_task is not None and services._incident is not None)

        # E.g. an enqueued trigger's start, while the recovery sleeps.
        await services.start()
        await _until(lambda: _recovered(services))

        assert spawner.spawned == [first, second] and services._process is second
        assert _activities(sink) == [_RESTARTED_UNEXPECTEDLY]
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_a_worker_that_dies_right_after_its_restart_is_restarted_again(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its reader asks for a restart while the recovery that started it still runs."""
    first, brief, third = _FakeWorker(_pid(1)), _FakeWorker(_pid(2), crash_after_ready=True), _FakeWorker(_pid(3))
    spawner = _spawn(monkeypatch, first, brief, third)
    services, sink = _supervised()
    await services.start()
    try:
        first.crash()
        await _until(lambda: services._process is third and _recovered(services))

        assert spawner.spawned == [first, brief, third]
        assert _activities(sink) == [_RESTARTED_UNEXPECTEDLY, _RESTARTED_UNEXPECTEDLY]
        assert services.outage() is None
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_an_unusable_restart_setting_gives_up_at_once_and_says_so(
    supervision: Callable[..., None], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    supervision(runtime_worker_restart_limit="0")
    worker = _FakeWorker(_pid(1))
    spawner = _spawn(monkeypatch, worker)
    services, sink = _supervised()
    await services.start()
    try:
        with caplog.at_level(logging.ERROR, logger="core.runtime.services"):
            worker.crash()
            await _until(lambda: services.outage() is not None)

        outage = services.outage()
        assert outage is not None
        assert (outage.reason, outage.attempts) == ("it stopped unexpectedly", 0)
        assert spawner.spawned == [worker]
        assert "Runtime worker automatic restarts stopped: a restart setting is unusable" in [
            record.getMessage() for record in caplog.records
        ]
        assert [event for event, _detail in _activities(sink)] == ["runtime_worker_down"]
    finally:
        await services.stop()


# ─── peer_message (Office chatter) ───


def _peer_row(**overrides: Any) -> Message:
    fields = {
        "id": "m1", "from_agent": "a1", "to_agent": "a2", "content": "verdict: SHIP",
        "message_type": "social", "floor_id": "lobby",
        "created_at": datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return Message(**fields)


_PEER = {
    "message_id": "m1", "from_agent_id": "a1", "to_agent_id": "a2", "content": "verdict: SHIP",
    "message_type": "social", "created_at": "2026-10-07T09:30:00+00:00", "floor_id": "lobby",
}


def test_the_peer_event_is_the_persisted_row_and_refuses_a_floorless_one() -> None:
    assert peer_message_event(_peer_row()) == _PEER
    for missing in (None, ""):
        with pytest.raises(ValueError, match="no floor_id"):
            peer_message_event(_peer_row(floor_id=missing))


def test_a_peer_message_is_merged_but_is_not_operator_say() -> None:
    result: dict[str, Any] = {}
    merge_say_artifacts(result, {"peer_message": _PEER})
    assert result["peer_message"] == _PEER
    assert say_already_posted(result) is False


@pytest.mark.asyncio
async def test_peer_message_crosses_every_sink_layer() -> None:
    transport = _RecordingTransport()
    await TransportRuntimeEventSink(transport).broadcast_peer_message(**_PEER)
    assert transport.envelopes == [{"type": "event", "payload": {"kind": "peer_message", "data": _PEER}}]

    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)
    await proxy.broadcast_peer_message(**_PEER)
    assert sink.calls == [("peer_message", _PEER)]
    proxy.set_sink(NullRuntimeEventSink())
    await proxy.broadcast_peer_message(**_PEER)

    services = RuntimeServices()
    routed = _RecordingSink()
    services.set_event_sink(routed)
    await services._dispatch_event(transport.envelopes[0]["payload"])
    assert routed.calls == [("peer_message", _PEER)]


@pytest.mark.asyncio
async def test_the_connection_manager_broadcasts_peer_message(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ConnectionManager()
    sent: list[dict[str, Any]] = []

    async def record(message: dict[str, Any]) -> None:
        sent.append(message)

    monkeypatch.setattr(manager, "broadcast", record)
    await manager.broadcast_peer_message(**_PEER)
    assert sent == [{"type": "peer_message", "data": _PEER}]


# ─── task_step (the task detail's Recent steps) ───


_STEP = {
    "id": "s1", "task_id": "t1", "agent_id": "a1", "agent_name": "Ettie", "action": "bm_cli",
    "note": "Run the tests", "command": "cargo test", "outcome": None, "result": None,
    "started_at": "2026-10-22T21:15:49+00:00", "ended_at": None,
}


@pytest.mark.asyncio
async def test_task_step_crosses_every_sink_layer() -> None:
    transport = _RecordingTransport()
    await TransportRuntimeEventSink(transport).broadcast_task_step(_STEP)
    assert transport.envelopes == [{"type": "event", "payload": {"kind": "task_step", "data": {"step": _STEP}}}]

    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)
    await proxy.broadcast_task_step(_STEP)
    assert sink.calls == [("task_step", _STEP)]
    proxy.set_sink(NullRuntimeEventSink())
    await proxy.broadcast_task_step(_STEP)

    services = RuntimeServices()
    routed = _RecordingSink()
    services.set_event_sink(routed)
    await services._dispatch_event(transport.envelopes[0]["payload"])
    assert routed.calls == [("task_step", _STEP)]


@pytest.mark.asyncio
async def test_the_connection_manager_broadcasts_task_step(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ConnectionManager()
    sent: list[dict[str, Any]] = []

    async def record(message: dict[str, Any]) -> None:
        sent.append(message)

    monkeypatch.setattr(manager, "broadcast", record)
    await manager.broadcast_task_step(_STEP)
    assert sent == [{"type": "task_step", "data": _STEP}]


# ─── schedule_world_state (status changes outside agent turns) ───


async def _let_scheduled_tasks_run() -> None:
    """Yield to the loop until a scheduled broadcast and its done-callback have both run."""
    for _ in range(3):
        await asyncio.sleep(0)


class _FailingWorldSink(_RecordingSink):
    async def broadcast_world_state(self) -> None:
        raise RuntimeError("socket closed")


@pytest.fixture
def recording_runtime_events() -> Iterator[_RecordingSink]:
    """A recording sink on the process-wide ``runtime_events``, and the old sink back after.

    The proxy is a singleton every test in this worker shares, so the
    previous sink is restored even when the test fails.
    """
    previous = runtime_events._sink
    sink = _RecordingSink()
    runtime_events.set_sink(sink)
    try:
        yield sink
    finally:
        runtime_events.set_sink(previous)


def test_schedule_world_state_without_a_running_loop_schedules_nothing() -> None:
    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)

    assert proxy.schedule_world_state() is None
    assert sink.calls == []


@pytest.mark.asyncio
async def test_schedule_world_state_coalesces_a_burst_until_its_broadcast_has_run() -> None:
    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)

    proxy.schedule_world_state()
    proxy.schedule_world_state()
    proxy.schedule_world_state()
    assert sink.calls == []
    await _let_scheduled_tasks_run()
    assert sink.calls == [("world_state", {})]

    # That broadcast is done, so the next request is a new snapshot.
    proxy.schedule_world_state()
    await _let_scheduled_tasks_run()
    assert sink.calls == [("world_state", {}), ("world_state", {})]


class _HeldWorldSink(_RecordingSink):
    """Records each world snapshot as its send starts, then holds the send open."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def broadcast_world_state(self) -> None:
        self.calls.append(("world_state", {}))
        await self.release.wait()


@pytest.mark.asyncio
async def test_a_request_made_while_a_snapshot_is_sending_gets_its_own() -> None:
    """A started snapshot no longer absorbs requests.

    The worker's send yields mid-way (two ``to_thread`` steps), so its line
    can already be in the pipe, and the app can already have read the
    database, while the task is not done. A refresh in that gap is not in
    that snapshot and must schedule another.
    """
    proxy = RuntimeEventProxy()
    sink = _HeldWorldSink()
    proxy.set_sink(sink)

    proxy.schedule_world_state()
    for _ in range(10):
        if sink.calls:
            break
        await asyncio.sleep(0)
    assert sink.calls == [("world_state", {})], "the first snapshot never started sending"

    proxy.schedule_world_state()
    sink.release.set()
    await _let_scheduled_tasks_run()

    assert sink.calls == [("world_state", {}), ("world_state", {})]


@pytest.mark.asyncio
async def test_a_failed_scheduled_world_state_is_logged_and_stays_in_its_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    proxy = RuntimeEventProxy()
    proxy.set_sink(_FailingWorldSink())
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with caplog.at_level(logging.ERROR, logger="core.runtime.events"):
            proxy.schedule_world_state()
            await _let_scheduled_tasks_run()
    finally:
        loop.set_exception_handler(None)

    failures = [record for record in caplog.records if record.name == "core.runtime.events"]
    assert len(failures) == 1
    assert failures[0].levelno == logging.ERROR
    assert failures[0].exc_info is not None
    assert isinstance(failures[0].exc_info[1], RuntimeError)
    assert str(failures[0].exc_info[1]) == "socket closed"
    assert unhandled == []


@pytest.mark.asyncio
async def test_a_cancelled_scheduled_world_state_is_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    proxy = RuntimeEventProxy()
    sink = _RecordingSink()
    proxy.set_sink(sink)
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with caplog.at_level(logging.ERROR, logger="core.runtime.events"):
            proxy.schedule_world_state()
            pending = proxy._world_pending
            assert pending is not None
            pending.cancel()
            await _let_scheduled_tasks_run()
    finally:
        loop.set_exception_handler(None)

    assert pending.cancelled()
    assert sink.calls == []
    assert [record for record in caplog.records if record.name == "core.runtime.events"] == []
    assert unhandled == []
    # A cancelled request does not absorb the next one.
    proxy.schedule_world_state()
    await _let_scheduled_tasks_run()
    assert sink.calls == [("world_state", {})]


@pytest.mark.asyncio
async def test_refresh_agent_status_schedules_one_world_snapshot(
    recording_runtime_events: _RecordingSink,
) -> None:
    """The one choke point for visible status writes also asks for the snapshot.

    Status changes outside a turn (claim checks, failed-turn supervision, the
    watchdog's stall escalation) have no turn broadcast after them, so the
    rail stayed wrong until an unrelated one.
    """
    db.init_db()
    agent = db.create_agent(f"Refresh-{uuid.uuid4().hex[:8]}", role="Eng", desk_x=1, desk_y=1)

    state = activity_runtime.refresh_agent_status(agent.id)

    assert state is not None and state.status == "idle"
    # Scheduled, not sent: the loop has not yielded yet.
    assert recording_runtime_events.calls == []
    await _let_scheduled_tasks_run()
    assert recording_runtime_events.calls == [("world_state", {})]
