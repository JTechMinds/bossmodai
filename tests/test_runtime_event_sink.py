"""HA-STRUCT-P1-06 — RuntimeServices broadcasts through an injected sink."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import db
from core.runtime.events import NullRuntimeEventSink, RuntimeEventProxy, TransportRuntimeEventSink
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

    async def broadcast_channel_message(self, **data: Any) -> None:
        self.calls.append(("channel_message", data))

    async def broadcast_channel_presence(self, **data: Any) -> None:
        self.calls.append(("channel_presence", data))

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
    assert sink.calls == [("extension_live", {"extension_id": None, "agent_id": None})]
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
    assert sink.calls == [("extension_live", {"extension_id": None, "agent_id": None})]
