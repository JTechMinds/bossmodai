"""BossMod AI — Coordinated runtime service lifecycle."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from contextlib import suppress
from typing import Any

from api.websocket import manager
import db
from core import config
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.watchdog import watchdog
from core.runtime.events import LoopRuntimeEventSink, NullRuntimeEventSink, runtime_events
from core.world.simulation import simulation

logger = logging.getLogger(__name__)

_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS = 2.0


class _RuntimeController:
    """Own the runtime-only services inside the dedicated runtime thread."""

    async def boot(self, *, paused: bool) -> None:
        if not paused:
            self._start_services()

    async def shutdown(self) -> None:
        await self._stop_services()

    async def pause(self) -> None:
        await self._stop_services()

    async def resume(self) -> None:
        self._start_services()

    async def enqueue_trigger(
        self,
        *,
        agent_id: str,
        trigger_type: str,
        source_channel: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        dispatcher.enqueue_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )

    async def reset_agent_runtime(self, agent_id: str) -> None:
        await dispatcher.reset_agent(agent_id)
        simulation.clear_agent_path(agent_id)

    def _start_services(self) -> None:
        dispatcher.start()
        simulation.start()
        watchdog.start()

    async def _stop_services(self) -> None:
        await watchdog.stop()
        await dispatcher.stop()
        await simulation.stop()


class RuntimeServices:
    """Own lifecycle and control of the isolated runtime thread."""

    _STATE_KEY = "runtime_control_state"
    _RUNNING = "running"
    _PAUSED = "paused"
    _RELAY_STOP = object()

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        self._controller: _RuntimeController | None = None
        self._thread_ready = threading.Event()
        self._thread_error: BaseException | None = None
        self._relay_loop: asyncio.AbstractEventLoop | None = None
        self._event_queue: asyncio.Queue[dict[str, Any] | object] | None = None
        self._relay_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the dedicated runtime thread and its event relay."""
        async with self._guard():
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._thread_error = None
        self._thread_ready.clear()
        self._relay_loop = asyncio.get_running_loop()
        self._event_queue = asyncio.Queue()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="bossmod-runtime",
            daemon=True,
        )
        self._thread.start()

        ready = await asyncio.to_thread(self._thread_ready.wait, 10.0)
        if not ready:
            raise RuntimeError("Timed out waiting for runtime thread startup")
        if self._thread_error is not None:
            raise RuntimeError("Runtime thread failed to start") from self._thread_error
        if self._thread_loop is None or self._controller is None:
            raise RuntimeError("Runtime thread did not initialize correctly")
        if self._event_queue is None:
            raise RuntimeError("Runtime relay queue did not initialize")

        self._relay_task = asyncio.create_task(self._relay_events())

    async def stop(self) -> None:
        """Stop the runtime thread and event relay."""
        async with self._guard():
            await self._stop_unlocked()

    async def reseed_application_data(self) -> None:
        """Recreate the application database from the current schema and restart services."""
        async with self._guard():
            await self._stop_unlocked()
            db.reset_database()
            config.reload()
            await self._start_unlocked()

    def state(self) -> str:
        """Return the persisted global runtime state."""
        state = config.get(self._STATE_KEY)
        if state == self._PAUSED:
            return self._PAUSED
        return self._RUNNING

    def is_paused(self) -> bool:
        """Return whether the global runtime kill switch is engaged."""
        return self.state() == self._PAUSED

    def status_payload(self) -> dict[str, object]:
        """Return a small UI-friendly runtime state payload."""
        state = self.state()
        return {
            "state": state,
            "paused": state == self._PAUSED,
        }

    async def pause(self) -> dict[str, object]:
        """Persist the global paused state and stop runtime services."""
        async with self._guard():
            db.set_setting(self._STATE_KEY, self._PAUSED, "advanced")
            config.reload()
            await self._submit(self._require_controller().pause())
            return self.status_payload()

    async def resume(self) -> dict[str, object]:
        """Persist the global running state and restart runtime services."""
        async with self._guard():
            db.set_setting(self._STATE_KEY, self._RUNNING, "advanced")
            config.reload()
            await self._start_unlocked()
            await self._submit(self._require_controller().resume())
            return self.status_payload()

    async def enqueue_trigger(
        self,
        *,
        agent_id: str,
        trigger_type: str,
        source_channel: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        if self._controller is None:
            await self.start()
        await self._submit(
            self._require_controller().enqueue_trigger(
                agent_id=agent_id,
                trigger_type=trigger_type,
                source_channel=source_channel,
                payload=payload,
                task_id=task_id,
            )
        )

    async def reset_agent_runtime(self, agent_id: str) -> None:
        if self._controller is None:
            await self.start()
        await self._submit(self._require_controller().reset_agent_runtime(agent_id))

    def _require_controller(self) -> _RuntimeController:
        if self._controller is None:
            raise RuntimeError("Runtime controller is not running")
        return self._controller

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _submit(self, coro: Any) -> Any:
        loop = self._thread_loop
        if loop is None:
            raise RuntimeError("Runtime thread is not running")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return await asyncio.wrap_future(future)

    async def _stop_unlocked(self) -> None:
        """Stop the runtime thread and drain pending relay events."""
        thread = self._thread
        loop = self._thread_loop
        controller = self._controller
        relay_task = self._relay_task
        relay_loop = self._relay_loop
        relay_queue = self._event_queue

        if thread is None:
            return

        if loop is not None and controller is not None:
            shutdown_future = asyncio.run_coroutine_threadsafe(controller.shutdown(), loop)
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(shutdown_future)),
                    timeout=_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("Runtime shutdown stalled; interrupting runtime DuckDB query")
                db.interrupt_thread_connection(thread.ident)
                with suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.wrap_future(shutdown_future)),
                        timeout=_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS,
                    )
            loop.call_soon_threadsafe(loop.stop)

        await asyncio.to_thread(thread.join, 10.0)
        if thread.is_alive():
            raise RuntimeError("Runtime thread failed to stop cleanly")

        self._thread = None
        self._thread_loop = None
        self._controller = None

        if relay_task is not None and relay_loop is not None and relay_queue is not None:
            with suppress(RuntimeError):
                relay_loop.call_soon_threadsafe(relay_queue.put_nowait, self._RELAY_STOP)
            current_loop = asyncio.get_running_loop()
            if relay_task.get_loop() is current_loop:
                with suppress(asyncio.CancelledError):
                    await relay_task
        self._relay_loop = None
        self._event_queue = None
        self._relay_task = None

    async def _relay_events(self) -> None:
        queue = self._event_queue
        if queue is None:
            return
        while True:
            envelope = await queue.get()
            if envelope is self._RELAY_STOP:
                return
            if not isinstance(envelope, dict):
                continue
            await self._dispatch_event(envelope)

    async def _dispatch_event(self, envelope: dict[str, Any]) -> None:
        kind = envelope.get("kind")
        data = envelope.get("data") or {}
        if kind == "world_state":
            await manager.broadcast_world_state()
            return
        if kind == "runtime_state":
            await manager.broadcast_runtime_state(data["payload"])
            return
        if kind == "chat_message":
            await manager.broadcast_chat_message(**data)
            return
        if kind == "meeting_message":
            await manager.broadcast_meeting_message(**data)
            return
        if kind == "channel_message":
            await manager.broadcast_channel_message(**data)
            return
        if kind == "diagnostic":
            await manager.broadcast_diagnostic(data["summary"])
            return
        if kind == "thought":
            await manager.broadcast_thought(
                data["agent_id"],
                data["thought"],
                data["action_name"],
            )
            return
        if kind == "activity":
            await manager.broadcast_activity(
                event=data["event"],
                detail=data["detail"],
                agent_name=data.get("agent_name"),
                extra=data.get("extra"),
            )
            return
        if kind == "feed_update":
            await manager.broadcast_feed_update(data["entry"])
            return
        logger.warning("Unknown runtime event kind received: %s", kind)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        relay_loop = self._relay_loop
        event_queue = self._event_queue
        if relay_loop is None or event_queue is None:
            self._thread_error = RuntimeError("Runtime relay bridge is not initialized")
            self._thread_ready.set()
            loop.close()
            return
        runtime_events.set_sink(LoopRuntimeEventSink(relay_loop, event_queue))
        controller = _RuntimeController()
        try:
            self._thread_loop = loop
            self._controller = controller
            loop.run_until_complete(controller.boot(paused=self.is_paused()))
        except BaseException as exc:
            self._thread_error = exc
            self._thread_ready.set()
            runtime_events.set_sink(NullRuntimeEventSink())
            db.close_thread_connection()
            loop.close()
            return

        self._thread_ready.set()
        try:
            loop.run_forever()
        finally:
            runtime_events.set_sink(NullRuntimeEventSink())
            db.close_thread_connection()
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError, concurrent.futures.CancelledError):
                    loop.run_until_complete(task)
            loop.close()


runtime_services = RuntimeServices()
