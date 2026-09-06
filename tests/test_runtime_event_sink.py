"""HA-STRUCT-P1-06 — RuntimeServices broadcasts through an injected sink."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
