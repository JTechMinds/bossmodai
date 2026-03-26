"""BossMod AI — Runtime event bridge for cross-thread delivery."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol


class RuntimeEventSink(Protocol):
    """Abstract sink for runtime-originated UI/realtime events."""

    async def broadcast_world_state(self) -> None: ...
    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None: ...
    async def broadcast_chat_message(
        self,
        *,
        agent_id: str,
        content: str,
        from_type: str,
        from_name: str,
        message_type: str | None = None,
        message_id: str | None = None,
        created_at: Any = None,
        notification_kind: str | None = None,
        desk_path: str | None = None,
    ) -> None: ...
    async def broadcast_meeting_message(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        content: str,
        author_type: str,
        author_name: str,
        message_id: str | None = None,
        created_at: Any = None,
    ) -> None: ...
    async def broadcast_channel_message(
        self,
        *,
        channel_id: str,
        content: str,
        author_type: str,
        author_name: str,
        message_id: str | None = None,
        created_at: Any = None,
    ) -> None: ...
    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None: ...
    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None: ...
    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None: ...
    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None: ...


class NullRuntimeEventSink:
    """Drop runtime events when no sink has been configured."""

    async def broadcast_world_state(self) -> None:
        return None

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        return None

    async def broadcast_chat_message(self, **_: Any) -> None:
        return None

    async def broadcast_meeting_message(self, **_: Any) -> None:
        return None

    async def broadcast_channel_message(self, **_: Any) -> None:
        return None

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        return None

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        return None

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        return None


class LoopRuntimeEventSink:
    """Forward runtime events onto an app-loop asyncio queue."""

    def __init__(
        self,
        relay_loop: asyncio.AbstractEventLoop,
        event_queue: asyncio.Queue[dict[str, Any] | object],
    ) -> None:
        self._relay_loop = relay_loop
        self._event_queue = event_queue

    def _emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        envelope = {"kind": kind, "data": data or {}}
        self._relay_loop.call_soon_threadsafe(self._event_queue.put_nowait, envelope)

    async def broadcast_world_state(self) -> None:
        self._emit("world_state")

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        self._emit("runtime_state", {"payload": payload})

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        self._emit("chat_message", kwargs)

    async def broadcast_meeting_message(self, **kwargs: Any) -> None:
        self._emit("meeting_message", kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        self._emit("channel_message", kwargs)

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        self._emit("diagnostic", {"summary": summary})

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        self._emit(
            "thought",
            {
                "agent_id": agent_id,
                "thought": thought,
                "action_name": action_name,
            },
        )

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            "activity",
            {
                "event": event,
                "detail": detail,
                "agent_name": agent_name,
                "extra": extra,
            },
        )

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        self._emit("feed_update", {"entry": entry})


class RuntimeEventProxy:
    """Mutable proxy used by runtime code regardless of execution context."""

    def __init__(self) -> None:
        self._sink: RuntimeEventSink = NullRuntimeEventSink()

    def set_sink(self, sink: RuntimeEventSink) -> None:
        self._sink = sink

    async def broadcast_world_state(self) -> None:
        await self._sink.broadcast_world_state()

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        await self._sink.broadcast_runtime_state(payload)

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_chat_message(**kwargs)

    async def broadcast_meeting_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_meeting_message(**kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_channel_message(**kwargs)

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        await self._sink.broadcast_diagnostic(summary)

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        await self._sink.broadcast_thought(agent_id, thought, action_name)

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        await self._sink.broadcast_activity(
            event=event,
            detail=detail,
            agent_name=agent_name,
            extra=extra,
        )

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        await self._sink.broadcast_feed_update(entry)


runtime_events = RuntimeEventProxy()
