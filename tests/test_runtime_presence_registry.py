"""Presence survives the browser: the app keeps the dispatcher's deltas as a snapshot.

The dispatcher is the one producer of ``agent_presence``. ``PresenceRegistry``
(core/runtime/presence.py) materializes those deltas in the app process,
``RuntimeServices`` feeds and clears it, and the WebSocket endpoint hands the
result to every connecting client as its fourth frame. These tests prove each
seam: the registry's set/remove semantics and validation, the dispatch path
(applied before the sink check; a malformed event is logged and never
forwarded), the worker-exit reset, and the connect frames.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import install_local_api_auth, install_settings_refresh
from api.routes import router
from core import config
from core.runtime import runtime_services
from core.runtime.presence import PresenceRegistry
from core.runtime.services import RuntimeServices


def _thinking(agent_id: str = "a1", channel_id: str | None = "ch-1", since: str = "2026-11-10T09:00:00+00:00") -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_name": "Pierre",
        "phase": "thinking",
        "channel_id": channel_id,
        "ahead": None,
        "since": since,
    }


def _queued(agent_id: str = "a1", channel_id: str | None = "ch-1", ahead: int = 2) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_name": "Pierre",
        "phase": "queued",
        "channel_id": channel_id,
        "ahead": ahead,
        "since": None,
    }


def _idle(agent_id: str = "a1", channel_id: str | None = "ch-1") -> dict[str, Any]:
    return {"agent_id": agent_id, "agent_name": "Pierre", "phase": "idle", "channel_id": channel_id}


# ─── PresenceRegistry ───


def test_thinking_and_queued_set_a_row() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking())
    registry.apply(_queued(agent_id="a2", ahead=1))
    assert registry.snapshot() == [
        {"agent_id": "a1", "agent_name": "Pierre", "channel_id": "ch-1", "phase": "thinking",
         "ahead": None, "since": "2026-11-10T09:00:00+00:00"},
        {"agent_id": "a2", "agent_name": "Pierre", "channel_id": "ch-1", "phase": "queued",
         "ahead": 1, "since": None},
    ]


def test_a_queued_row_that_starts_thinking_is_one_key() -> None:
    registry = PresenceRegistry()
    registry.apply(_queued())
    registry.apply(_thinking())
    rows = registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["phase"] == "thinking"
    assert rows[0]["ahead"] is None
    assert rows[0]["since"] == "2026-11-10T09:00:00+00:00"


def test_rows_are_keyed_per_channel_and_an_absent_channel_is_none() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking(channel_id="ch-1"))
    registry.apply(_queued(channel_id="ch-2"))
    # A DM or desk turn sends no channel: absent and empty both key as None.
    registry.apply({"agent_id": "a1", "agent_name": "Pierre", "phase": "queued", "ahead": 0})
    registry.apply({"agent_id": "a1", "agent_name": "Pierre", "phase": "queued", "ahead": 3, "channel_id": ""})
    rows = registry.snapshot()
    assert [(row["channel_id"], row["phase"], row["ahead"]) for row in rows] == [
        ("ch-1", "thinking", None),
        ("ch-2", "queued", 2),
        (None, "queued", 3),
    ]


def test_idle_removes_only_its_own_key() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking(channel_id="ch-1"))
    registry.apply(_queued(channel_id="ch-2"))
    registry.apply(_idle(channel_id="ch-1"))
    assert [(row["channel_id"], row["phase"]) for row in registry.snapshot()] == [("ch-2", "queued")]


def test_idle_for_an_absent_key_is_a_no_op() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking(channel_id="ch-1"))
    registry.apply(_idle(channel_id="ch-9"))
    registry.apply(_idle(agent_id="nobody"))
    registry.apply(_idle(channel_id="ch-1"))
    registry.apply(_idle(channel_id="ch-1"))
    assert registry.snapshot() == []


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({**_thinking(), "phase": "working"}, "phase"),
        ({**_thinking(), "phase": None}, "phase"),
        ({**_thinking(), "agent_id": ""}, "agent_id"),
        ({key: value for key, value in _thinking().items() if key != "agent_id"}, "agent_id"),
        ({**_thinking(), "agent_name": ""}, "agent_name"),
        ({**_queued(), "ahead": None}, "ahead"),
        ({**_queued(), "ahead": -1}, "ahead"),
        ({**_queued(), "ahead": True}, "ahead"),
    ],
)
def test_a_malformed_payload_raises_naming_the_field_and_changes_nothing(
    payload: dict[str, Any], field: str
) -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking(agent_id="kept"))
    before = registry.snapshot()
    with pytest.raises(ValueError, match=field):
        registry.apply(payload)
    assert registry.snapshot() == before


def test_snapshot_rows_are_copies() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking())
    first = registry.snapshot()
    first[0]["phase"] = "mutated"
    first.clear()
    assert registry.snapshot()[0]["phase"] == "thinking"


def test_clear_forgets_every_row() -> None:
    registry = PresenceRegistry()
    registry.apply(_thinking())
    registry.apply(_queued(agent_id="a2"))
    registry.clear()
    assert registry.snapshot() == []


# ─── RuntimeServices: dispatch and worker exit ───


class _PresenceSink:
    """Records the two presence surfaces of the app's EventSink."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def broadcast_agent_presence(self, **data: Any) -> None:
        self.calls.append(("agent_presence", data))

    async def broadcast_presence_snapshot(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append(("presence_snapshot", rows))

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        self.calls.append(("extension_live", (extension_id, agent_id)))


@pytest.mark.asyncio
async def test_dispatch_applies_presence_to_the_snapshot_and_forwards_it() -> None:
    services = RuntimeServices()
    sink = _PresenceSink()
    services.set_event_sink(sink)
    payload = _thinking()
    await services._dispatch_event({"kind": "agent_presence", "data": payload})
    assert services.presence_snapshot() == [payload]
    assert sink.calls == [("agent_presence", payload)]

    await services._dispatch_event({"kind": "agent_presence", "data": _idle()})
    assert services.presence_snapshot() == []
    assert sink.calls[-1] == ("agent_presence", _idle())


@pytest.mark.asyncio
async def test_dispatch_keeps_presence_before_any_sink_is_attached() -> None:
    services = RuntimeServices()
    await services._dispatch_event({"kind": "agent_presence", "data": _queued()})
    assert [row["phase"] for row in services.presence_snapshot()] == ["queued"]


@pytest.mark.asyncio
async def test_a_malformed_presence_event_is_logged_and_not_forwarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = RuntimeServices()
    sink = _PresenceSink()
    services.set_event_sink(sink)
    await services._dispatch_event({"kind": "agent_presence", "data": _thinking()})
    bad = {**_thinking(agent_id="a2"), "phase": "working"}
    with caplog.at_level(logging.ERROR, logger="core.runtime.services"):
        await services._dispatch_event({"kind": "agent_presence", "data": bad})
    assert [call[0] for call in sink.calls] == ["agent_presence"], "the malformed event was forwarded"
    assert [row["agent_id"] for row in services.presence_snapshot()] == ["a1"]
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Dropped malformed presence event" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_worker_exit_clears_presence_and_broadcasts_an_empty_snapshot() -> None:
    services = RuntimeServices()
    sink = _PresenceSink()
    services.set_event_sink(sink)
    await services._dispatch_event({"kind": "agent_presence", "data": _thinking()})
    sink.calls.clear()

    await services._announce_worker_exit()

    assert services.presence_snapshot() == []
    assert sink.calls == [("presence_snapshot", []), ("extension_live", (None, None))]


@pytest.mark.asyncio
async def test_worker_ready_keeps_presence() -> None:
    """A thinking emitted before the ready line must survive it."""
    services = RuntimeServices()
    sink = _PresenceSink()
    services.set_event_sink(sink)
    await services._dispatch_event({"kind": "agent_presence", "data": _thinking()})
    await services._handle_worker_message({"type": "ready"})
    assert [row["phase"] for row in services.presence_snapshot()] == ["thinking"]
    assert ("presence_snapshot", []) not in sink.calls


# ─── The WebSocket connect frames ───


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


def test_a_connecting_client_gets_the_presence_snapshot_fourth() -> None:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    install_settings_refresh(app)
    client = TestClient(app)
    token = db.ensure_local_api_token()
    row = _thinking(agent_id="pierre", channel_id="ch-eng")
    runtime_services._presence.apply(row)
    try:
        frames: list[dict[str, Any]] = []
        with client.websocket_connect(f"/api/ws?token={token}") as ws:
            while not frames or frames[-1]["type"] != "presence_snapshot":
                assert len(frames) < 4, f"no presence_snapshot in the first four frames: {frames}"
                frames.append(ws.receive_json())
    finally:
        runtime_services._presence.clear()
    assert [frame["type"] for frame in frames] == [
        "world_update", "unified_feed", "runtime_state", "presence_snapshot",
    ]
    assert frames[-1]["data"] == [row]
