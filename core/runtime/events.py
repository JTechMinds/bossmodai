"""BossMod AI — Runtime event bridge for process-isolated delivery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import db

logger = logging.getLogger(__name__)


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
        host_path_consent: dict[str, Any] | None = None,
        cli_approval: dict[str, Any] | None = None,
        task_id: str | None = None,
        memory_id: int | None = None,
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
    async def broadcast_peer_message(self, **data: Any) -> None: ...
    async def broadcast_channel_message(
        self,
        *,
        channel_id: str,
        content: str,
        author_type: str,
        author_name: str,
        message_id: str | None = None,
        created_at: Any = None,
        notification_kind: str | None = None,
        host_path_consent: dict[str, Any] | None = None,
        cli_approval: dict[str, Any] | None = None,
        author_agent_id: str | None = None,
        desk_path: str | None = None,
        task_id: str | None = None,
    ) -> None: ...
    async def broadcast_agent_presence(
        self,
        *,
        agent_id: str,
        agent_name: str,
        phase: str,
        ahead: int | None = None,
        channel_id: str | None = None,
        since: str | None = None,
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
    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None: ...
    async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None: ...
    async def broadcast_task_step(self, step: dict[str, Any]) -> None: ...


class RuntimeEventTransport(Protocol):
    """Transport abstraction used by runtime event sinks."""

    async def send_event(self, envelope: dict[str, Any]) -> None: ...


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

    async def broadcast_peer_message(self, **_: Any) -> None:
        return None

    async def broadcast_channel_message(self, **_: Any) -> None:
        return None

    async def broadcast_agent_presence(self, **_: Any) -> None:
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

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        return None

    async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None:
        return None

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        return None


class TransportRuntimeEventSink:
    """Serialize runtime events over an external transport."""

    def __init__(self, transport: RuntimeEventTransport) -> None:
        self._transport = transport

    async def _emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        await self._transport.send_event({
            "type": "event",
            "payload": {
                "kind": kind,
                "data": data or {},
            },
        })

    async def broadcast_world_state(self) -> None:
        await self._emit("world_state")

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        await self._emit("runtime_state", {"payload": payload})

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        await self._emit("chat_message", kwargs)

    async def broadcast_meeting_message(self, **kwargs: Any) -> None:
        await self._emit("meeting_message", kwargs)

    async def broadcast_peer_message(self, **kwargs: Any) -> None:
        await self._emit("peer_message", kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        await self._emit("channel_message", kwargs)

    async def broadcast_agent_presence(self, **kwargs: Any) -> None:
        await self._emit("agent_presence", kwargs)

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        await self._emit("diagnostic", {"summary": summary})

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        await self._emit(
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
        await self._emit(
            "activity",
            {
                "event": event,
                "detail": detail,
                "agent_name": agent_name,
                "extra": extra,
            },
        )

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        await self._emit("feed_update", {"entry": entry})

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        await self._emit("extension_live", {"extension_id": extension_id, "agent_id": agent_id})

    async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None:
        await self._emit("conversation_notes", {"scope_kind": scope_kind, "scope_key": scope_key})

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        await self._emit("task_step", {"step": step})


def _report_world_state_failure(task: asyncio.Task[None]) -> None:
    """Done-callback of a scheduled world snapshot: log its failure at ERROR.

    Retrieving the exception here logs it with its traceback when it happens,
    instead of leaving it to asyncio's late "exception was never retrieved"
    warning, which fires only if and when the task is collected. Cancellation
    is not a failure and is ignored: it is how a closing event loop ends a
    broadcast that had not run yet.

    Args:
        task: The finished ``broadcast_world_state`` task.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Scheduled world snapshot broadcast failed", exc_info=exc)


class RuntimeEventProxy:
    """Mutable proxy used by runtime code regardless of execution context."""

    def __init__(self) -> None:
        self._sink: RuntimeEventSink = NullRuntimeEventSink()
        # The scheduled snapshot that has not started yet, which absorbs
        # further requests; cleared by the snapshot as it starts.
        self._world_pending: asyncio.Task[None] | None = None

    def set_sink(self, sink: RuntimeEventSink) -> None:
        self._sink = sink

    async def broadcast_world_state(self) -> None:
        await self._sink.broadcast_world_state()

    def schedule_world_state(self) -> None:
        """Ask for one world snapshot from sync code, coalescing a burst into one send.

        Sync code (``activity_runtime.refresh_agent_status``, the one choke
        point for visible status writes) cannot await ``broadcast_world_state``,
        so this schedules it on the running event loop and returns at once,
        the way ``queue_visibility.schedule_queue_visibility`` does for the
        Busy line.

        - **No running event loop:** returns without scheduling anything. This
          is the documented behaviour, not a dropped event: a process with no
          loop (a script, a sync test, a migration) has no live clients to
          tell.
        - **A snapshot scheduled on this loop that has not started yet:** the
          request is absorbed, so every refresh made before the loop next
          yields becomes one snapshot. The app coalesces again
          (``world_state_coalesce_ms``) and the browser repaints only rows
          that changed, so a snapshot that changed nothing repaints nothing.
        - **Otherwise:** a new snapshot task is created. A done-callback logs
          its failure at ERROR and ignores its cancellation, so a failed send
          never raises into the caller's loop.

        Only a snapshot that has not started absorbs a request. Once it
        starts, its send yields more than once (the worker writes the line
        and flushes it in two ``to_thread`` steps), so the line can already
        be in the pipe, and the app can already have read the database, while
        the task is still not done. A refresh made in that gap would not be
        in that snapshot, so the snapshot stops absorbing as it starts
        (``_send_scheduled_world_state``) and the refresh gets its own.

        A pending task that belongs to another event loop does not absorb the
        request: that loop may already be closed (each test gets its own), and
        the task could never finish on this one.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pending = self._world_pending
        if pending is not None and not pending.done() and pending.get_loop() is loop:
            return
        task = loop.create_task(self._send_scheduled_world_state())
        task.add_done_callback(_report_world_state_failure)
        self._world_pending = task

    async def _send_scheduled_world_state(self) -> None:
        """Send one scheduled world snapshot, absorbing no request once it has begun.

        Clearing ``_world_pending`` comes first, before the send's first
        yield: a request from here on may change the database after this
        snapshot has been read, so it must schedule a snapshot of its own.
        It is cleared only when it still names this task: a newer snapshot
        that has not started (one scheduled meanwhile on another event loop)
        must keep absorbing requests.
        """
        if self._world_pending is asyncio.current_task():
            self._world_pending = None
        await self.broadcast_world_state()

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        await self._sink.broadcast_runtime_state(payload)

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_chat_message(**kwargs)

    async def broadcast_meeting_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_meeting_message(**kwargs)

    async def broadcast_peer_message(self, **kwargs: Any) -> None:
        await self._sink.broadcast_peer_message(**kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        if db.is_channel_archived(kwargs.get("channel_id")):
            return
        await self._sink.broadcast_channel_message(**kwargs)

    async def broadcast_agent_presence(self, **kwargs: Any) -> None:
        await self._sink.broadcast_agent_presence(**kwargs)

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
        peek_budget: str | None = None,
    ) -> None:
        # Decision-turn fail-closed results splat peek_budget. Park it in extra
        # so the sink contract stays event/detail/agent_name/extra.
        if peek_budget is not None:
            extra = {**(extra or {}), "peek_budget": peek_budget}
        await self._sink.broadcast_activity(
            event=event,
            detail=detail,
            agent_name=agent_name,
            extra=extra,
        )

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        await self._sink.broadcast_feed_update(entry)

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        await self._sink.broadcast_extension_live(extension_id, agent_id)

    async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None:
        """Tell open transcripts that one conversation's notes changed."""
        await self._sink.broadcast_conversation_notes(scope_kind, scope_key)

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        """Send one task step as it starts or ends (``task_steps.task_step_event``'s shape)."""
        await self._sink.broadcast_task_step(step)


runtime_events = RuntimeEventProxy()
