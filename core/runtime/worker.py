"""BossMod AI — dedicated runtime worker process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import suppress
from typing import Any

import db
from core import config
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.meeting_watchdog import meeting_watchdog
from core.agent_loop.watchdog import watchdog
from core.runtime.events import NullRuntimeEventSink, TransportRuntimeEventSink, runtime_events
from core.world.simulation import simulation

logger = logging.getLogger(__name__)

_COMMAND_POLL_INTERVAL_SECONDS = 0.25
_HEARTBEAT_INTERVAL_SECONDS = 1.0
_PARENT_CHECK_INTERVAL_SECONDS = 1.0
_WORKER_NAME = "primary"


class RuntimeController:
    """Own the runtime-only services inside the worker process."""

    async def boot(self, *, paused: bool) -> None:
        if not paused:
            self._start_services()

    async def shutdown(self) -> None:
        await self._stop_services()

    async def pause(self) -> None:
        await self._stop_services()

    async def resume(self) -> None:
        self._start_services()

    async def wake_dispatcher(self) -> None:
        dispatcher.notify()

    async def reset_agent_runtime(self, agent_id: str) -> None:
        await dispatcher.reset_agent(agent_id)
        simulation.clear_agent_path(agent_id)

    def _start_services(self) -> None:
        dispatcher.start()
        simulation.start()
        watchdog.start()
        meeting_watchdog.start()

    async def _stop_services(self) -> None:
        await meeting_watchdog.stop()
        await watchdog.stop()
        await dispatcher.stop()
        await simulation.stop()


class WorkerTransport:
    """JSONL transport over stdout for worker readiness and events."""

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()

    async def send_message(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, default=str) + "\n"
        async with self._write_lock:
            await asyncio.to_thread(sys.stdout.write, line)
            await asyncio.to_thread(sys.stdout.flush)

    async def send_event(self, envelope: dict[str, Any]) -> None:
        await self.send_message(envelope)


class RuntimeWorker:
    """Worker process loop for runtime services and durable control commands."""

    def __init__(self) -> None:
        self._transport = WorkerTransport()
        self._controller = RuntimeController()
        self._stopping = asyncio.Event()
        self._background_tasks: list[asyncio.Task[None]] = []
        self._failed = False
        self._parent_pid = _read_parent_pid()

    async def run(self) -> int:
        runtime_events.set_sink(TransportRuntimeEventSink(self._transport))
        try:
            db.init_db()
            await self._controller.boot(paused=self._is_paused())
            db.mark_runtime_worker_running(os.getpid(), worker_name=_WORKER_NAME)
            await self._transport.send_message({"type": "ready"})
            self._background_tasks = [
                asyncio.create_task(self._command_loop()),
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._parent_watchdog_loop()),
            ]
            await self._stopping.wait()
            return 0
        except Exception as exc:
            self._failed = True
            logger.exception("Runtime worker failed")
            db.mark_runtime_worker_error(str(exc), pid=os.getpid(), worker_name=_WORKER_NAME)
            await self._safe_send_fatal(str(exc))
            return 1
        finally:
            runtime_events.set_sink(NullRuntimeEventSink())
            for task in self._background_tasks:
                task.cancel()
            for task in self._background_tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await self._controller.shutdown()
            if not self._failed:
                db.mark_runtime_worker_stopped(pid=os.getpid(), worker_name=_WORKER_NAME)
            db.close_connection()

    async def _safe_send_fatal(self, error: str) -> None:
        try:
            await self._transport.send_message({"type": "fatal", "error": error})
        except Exception:
            logger.exception("Failed to send runtime worker fatal message")

    async def _command_loop(self) -> None:
        while not self._stopping.is_set():
            command = self._claim_next_command()
            if command is None:
                await asyncio.sleep(_COMMAND_POLL_INTERVAL_SECONDS)
                continue
            payload = json.loads(command.payload) if command.payload else {}
            try:
                await self._execute(command.command_type, payload)
            except Exception as exc:
                logger.exception("Runtime worker command failed: %s", command.command_type)
                db.fail_runtime_command(command.id, str(exc))
            else:
                db.complete_runtime_command(command.id)

    async def _heartbeat_loop(self) -> None:
        while not self._stopping.is_set():
            db.record_runtime_worker_heartbeat(pid=os.getpid(), worker_name=_WORKER_NAME)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    async def _parent_watchdog_loop(self) -> None:
        if self._parent_pid is None:
            return
        while not self._stopping.is_set():
            if os.getppid() != self._parent_pid:
                logger.warning("Runtime worker lost its parent process; shutting down")
                self._stopping.set()
                return
            await asyncio.sleep(_PARENT_CHECK_INTERVAL_SECONDS)

    def _claim_next_command(self):
        for command in db.list_queued_runtime_commands(limit=50):
            claimed = db.claim_runtime_command(command.id)
            if claimed is not None:
                return claimed
        return None

    async def _execute(self, command_type: str, payload: dict[str, Any]) -> None:
        if command_type == "wake_dispatcher":
            await self._controller.wake_dispatcher()
            return
        if command_type == "pause_runtime":
            await self._controller.pause()
            return
        if command_type == "resume_runtime":
            await self._controller.resume()
            return
        if command_type == "reset_agent_runtime":
            await self._controller.reset_agent_runtime(payload["agent_id"])
            return
        if command_type == "shutdown_runtime":
            self._stopping.set()
            return
        raise RuntimeError(f"Unsupported runtime command: {command_type}")

    def _is_paused(self) -> bool:
        return config.get("runtime_control_state") == "paused"


def _read_parent_pid() -> int | None:
    raw = os.environ.get("BOSSMOD_APP_PID")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _async_main() -> int:
    worker = RuntimeWorker()
    return await worker.run()


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
