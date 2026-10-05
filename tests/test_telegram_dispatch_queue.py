"""Phase 3 (refresh efficiency): Telegram never stalls the runtime-event reader.

``RuntimeServices._dispatch_event`` runs inside the app's one serial reader
of the worker's stdout. It used to await the Telegram send inline, so a slow
Telegram API delayed every later event and backed the worker's pipe up. Now
events go into a bounded queue drained by one consumer task.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.runtime.services import RuntimeServices


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


class _StalledBridge:
    """A Telegram bridge whose sends hang until released."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.delivered: list[str] = []
        self.release = asyncio.Event()

    async def dispatch(self, kind: str, data: dict[str, Any]) -> None:
        self.started.append(kind)
        await self.release.wait()
        self.delivered.append(kind)


class _Sink:
    def __init__(self) -> None:
        self.chat: list[dict[str, Any]] = []

    async def broadcast_chat_message(self, **data: Any) -> None:
        self.chat.append(data)

    async def broadcast_activity(self, **data: Any) -> None:
        pass


def _chat(index: int) -> dict[str, Any]:
    return {"kind": "chat_message", "data": {"agent_id": "a1", "content": f"hi {index}"}}


def test_the_queue_size_is_a_seeded_setting() -> None:
    assert config.require_int("telegram_dispatch_queue_size") == 200


async def test_a_stalled_telegram_send_does_not_delay_the_websocket_broadcast() -> None:
    services = RuntimeServices()
    sink = _Sink()
    services.set_event_sink(sink)
    bridge = _StalledBridge()
    services.set_telegram_bridge(bridge)
    try:
        await asyncio.wait_for(services._dispatch_event(_chat(1)), timeout=1)
        await asyncio.sleep(0)  # let the consumer pick the event up and hang on it
        assert bridge.started == ["chat_message"]
        # The bridge is still stuck on the first event; the next one reaches
        # the sink anyway, straight away.
        await asyncio.wait_for(services._dispatch_event(_chat(2)), timeout=1)
        assert [item["content"] for item in sink.chat] == ["hi 1", "hi 2"]
        assert bridge.delivered == []

        bridge.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        # One consumer: Telegram gets them in arrival order.
        assert bridge.delivered == ["chat_message", "chat_message"]
    finally:
        await services.stop()


async def test_a_full_queue_drops_the_event_with_a_warning_naming_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.set_setting("telegram_dispatch_queue_size", "2", "advanced")
    config.reload()
    services = RuntimeServices()
    services.set_event_sink(_Sink())
    bridge = _StalledBridge()
    services.set_telegram_bridge(bridge)
    try:
        await services._dispatch_event(_chat(1))
        await asyncio.sleep(0)  # the consumer holds event 1
        await services._dispatch_event(_chat(2))
        await services._dispatch_event(_chat(3))  # the queue is now full
        with caplog.at_level(logging.WARNING, logger="core.runtime.services"):
            await services._dispatch_event({"kind": "activity", "data": {"event": "x", "detail": "y"}})
        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert any("dropped one activity event" in message for message in warnings), warnings
    finally:
        await services.stop()


async def test_a_failed_telegram_send_is_logged_and_the_next_event_still_goes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    delivered: list[str] = []

    class _FlakyBridge:
        async def dispatch(self, kind: str, data: dict[str, Any]) -> None:
            if data.get("content") == "hi 1":
                raise RuntimeError("telegram is down")
            delivered.append(data["content"])

    services = RuntimeServices()
    services.set_event_sink(_Sink())
    services.set_telegram_bridge(_FlakyBridge())
    try:
        with caplog.at_level(logging.WARNING, logger="core.runtime.services"):
            await services._dispatch_event(_chat(1))
            await services._dispatch_event(_chat(2))
            for _ in range(10):
                await asyncio.sleep(0)
        assert delivered == ["hi 2"]
        assert any("Telegram bridge dispatch failed for chat_message" in r.getMessage() for r in caplog.records)
    finally:
        await services.stop()


async def test_stop_shuts_the_consumer_down_and_detaches_the_bridge() -> None:
    services = RuntimeServices()
    bridge = _StalledBridge()
    services.set_telegram_bridge(bridge)
    consumer = services._telegram_consumer
    assert consumer is not None and not consumer.done()

    await services.stop()

    assert consumer.cancelled()
    assert services._telegram_consumer is None
    # Detached: later events go nowhere near Telegram.
    await services._dispatch_event(_chat(1))
    await asyncio.sleep(0)
    assert bridge.started == []


async def test_attaching_a_second_bridge_is_refused() -> None:
    services = RuntimeServices()
    services.set_telegram_bridge(_StalledBridge())
    try:
        with pytest.raises(RuntimeError):
            services.set_telegram_bridge(_StalledBridge())
    finally:
        await services.stop()
