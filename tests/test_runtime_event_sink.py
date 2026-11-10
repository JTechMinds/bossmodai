"""HA-STRUCT-P1-06 — RuntimeServices broadcasts through an injected sink."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from api.websocket import ConnectionManager
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
from core.runtime.services import RuntimeServices


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
