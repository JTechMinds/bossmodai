"""BossMod AI — app-side gateway for the isolated runtime worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from core import config
import db
import db.connection as db_connection

logger = logging.getLogger(__name__)

_WORKER_READY_TIMEOUT_SECONDS = 10.0
_WORKER_STOP_TIMEOUT_SECONDS = 2.0
_COMMAND_WAIT_TIMEOUT_SECONDS = 10.0
_COMMAND_POLL_INTERVAL_SECONDS = 0.05
_HEARTBEAT_STALE_SECONDS = 5.0
_WORKER_NAME = "primary"
_HUMAN_PREEMPTED_TRIGGER_TYPES = ["activity_resumed", "watchdog_status_ping", "social"]


class EventSink(Protocol):
    """App-process broadcast surface. Wired in ``main.py`` lifespan."""

    async def broadcast_world_state(self) -> None: ...

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None: ...

    async def broadcast_chat_message(self, **data: Any) -> None: ...

    async def broadcast_meeting_message(self, **data: Any) -> None: ...

    async def broadcast_channel_message(self, **data: Any) -> None: ...

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


class RuntimeServices:
    """Expose a durable control boundary to the runtime worker process."""

    _STATE_KEY = "runtime_control_state"
    _RUNNING = "running"
    _PAUSED = "paused"

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._process_loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._ready_future: asyncio.Future[None] | None = None
        self._expecting_shutdown = False
        self._telegram_bridge: Any | None = None
        self._event_sink: EventSink | None = None

    def set_telegram_bridge(self, bridge: Any) -> None:
        """Attach the Telegram event bridge for forwarding runtime events."""
        self._telegram_bridge = bridge

    def set_event_sink(self, sink: EventSink) -> None:
        """Attach the WebSocket (or test) broadcast sink. ``core`` must not import ``api``."""
        self._event_sink = sink

    async def start(self) -> None:
        """Start the dedicated runtime worker process."""
        db.init_db()
        async with self._guard():
            await self._start_unlocked()

    async def stop(self) -> None:
        """Stop the runtime worker process."""
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
        state = config.get(self._STATE_KEY)
        if state == self._PAUSED:
            return self._PAUSED
        return self._RUNNING

    def is_paused(self) -> bool:
        return self.state() == self._PAUSED

    def status_payload(self) -> dict[str, object]:
        state = self.state()
        try:
            worker = db.get_runtime_worker_state(_WORKER_NAME)
        except Exception:
            worker = None
        last_heartbeat = worker.last_heartbeat_at if worker is not None else None
        heartbeat_age = None
        healthy = False
        if last_heartbeat is not None:
            heartbeat_age = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
        if worker is not None and worker.lifecycle_state == "running" and heartbeat_age is not None:
            healthy = heartbeat_age <= _HEARTBEAT_STALE_SECONDS
        return {
            "state": state,
            "paused": state == self._PAUSED,
            "worker": {
                "name": worker.worker_name if worker is not None else _WORKER_NAME,
                "state": worker.lifecycle_state if worker is not None else "stopped",
                "healthy": healthy,
                "pid": worker.pid if worker is not None else None,
                "last_heartbeat_at": last_heartbeat.isoformat() if last_heartbeat is not None else None,
                "started_at": worker.started_at.isoformat() if worker and worker.started_at else None,
                "stopped_at": worker.stopped_at.isoformat() if worker and worker.stopped_at else None,
                "last_error": worker.last_error if worker is not None else None,
            },
        }

    async def pause(self) -> dict[str, object]:
        async with self._guard():
            db.set_setting(self._STATE_KEY, self._PAUSED, "advanced")
            config.reload()
            if self._process_is_running():
                command = db.create_runtime_command("pause_runtime")
                await self._wait_for_command(command.id)
            return self.status_payload()

    async def resume(self) -> dict[str, object]:
        async with self._guard():
            db.set_setting(self._STATE_KEY, self._RUNNING, "advanced")
            config.reload()
            await self._start_unlocked()
            command = db.create_runtime_command("resume_runtime")
            await self._wait_for_command(command.id)
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
        await self.start()
        if trigger_type == "human_chat":
            db.delete_queued_triggers(agent_id, trigger_types=_HUMAN_PREEMPTED_TRIGGER_TYPES)
        db.create_agent_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )
        async with self._guard():
            if self._process_is_running() and not db.has_open_runtime_command(["wake_dispatcher"]):
                db.create_runtime_command("wake_dispatcher")

    async def reset_agent_runtime(self, agent_id: str) -> None:
        await self.start()
        async with self._guard():
            if not self._process_is_running():
                return
            command = db.create_runtime_command("reset_agent_runtime", {"agent_id": agent_id})
            await self._wait_for_command(command.id)

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _process_is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _start_unlocked(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            return

        if process is not None and process.returncode is not None:
            await self._clear_process_state()

        db.delete_open_runtime_commands()

        loop = asyncio.get_running_loop()
        self._ready_future = loop.create_future()
        self._expecting_shutdown = False

        worker_cmd = [
            sys.executable,
            "-u",
            "-m",
            "core.runtime.worker",
        ]
        env = os.environ.copy()
        env["BOSSMOD_DB_PATH"] = str(db_connection._DB_PATH)
        env["BOSSMOD_APP_PID"] = str(os.getpid())
        self._process = await asyncio.create_subprocess_exec(
            *worker_cmd,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
        )
        db.mark_runtime_worker_starting(self._process.pid, worker_name=_WORKER_NAME)
        self._process_loop = asyncio.get_running_loop()
        self._reader_task = asyncio.create_task(self._read_worker_output())
        ready_future = self._ready_future
        if ready_future is None:
            raise RuntimeError("Runtime worker ready future was not initialized")
        try:
            await asyncio.wait_for(ready_future, timeout=_WORKER_READY_TIMEOUT_SECONDS)
        except Exception as exc:
            await self._force_stop_process(self._process)
            await self._clear_process_state()
            db.mark_runtime_worker_error(str(exc), worker_name=_WORKER_NAME)
            raise

    async def _stop_unlocked(self) -> None:
        process = self._process
        if process is None:
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)
            return

        self._expecting_shutdown = True
        db.mark_runtime_worker_stopping(process.pid if process.returncode is None else None, worker_name=_WORKER_NAME)

        process_loop = self._process_loop
        if process_loop is not asyncio.get_running_loop():
            await self._kill_process(process)
            await self._clear_process_state()
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)
            return

        if process.returncode is None:
            db.create_runtime_command("shutdown_runtime")
            try:
                await asyncio.wait_for(process.wait(), timeout=_WORKER_STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await self._force_stop_process(process)

        await self._clear_process_state()
        state = db.get_runtime_worker_state(_WORKER_NAME)
        if state is None or state.lifecycle_state not in {"stopped", "error"}:
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)

    async def _clear_process_state(self) -> None:
        reader_task = self._reader_task
        if reader_task is not None:
            reader_loop = reader_task.get_loop()
            current_loop = asyncio.get_running_loop()
            if reader_loop is current_loop:
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task
            else:
                reader_task.cancel()
        self._reader_task = None

        process = self._process
        if process is not None:
            transport = getattr(process, "_transport", None)
            if transport is not None:
                with suppress(Exception):
                    transport.close()
                with suppress(Exception):
                    process._transport = None
            if process.stdout is not None:
                with suppress(Exception):
                    process.stdout.close()
                with suppress(Exception):
                    process.stdout = None

        self._process = None
        self._process_loop = None
        ready_future = self._ready_future
        if ready_future is not None and not ready_future.done():
            ready_future.cancel()
        self._ready_future = None

    async def _force_stop_process(self, process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _kill_process(self, process: asyncio.subprocess.Process) -> None:
        pid = process.pid
        if pid is None:
            return
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.to_thread(os.waitpid, pid, 0), timeout=2.0)
            return
        except (asyncio.TimeoutError, ChildProcessError):
            pass
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        with suppress(ChildProcessError):
            await asyncio.to_thread(os.waitpid, pid, 0)

    async def _wait_for_command(self, command_id: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _COMMAND_WAIT_TIMEOUT_SECONDS
        while True:
            command = db.get_runtime_command(command_id)
            if command is None:
                raise RuntimeError(f"Runtime command disappeared: {command_id}")
            if command.status == "completed":
                return
            if command.status == "failed":
                raise RuntimeError(command.failure_reason or "Runtime command failed")
            if loop.time() >= deadline:
                raise RuntimeError(f"Timed out waiting for runtime command {command.command_type}")
            if not self._process_is_running():
                raise RuntimeError("Runtime worker is not running")
            await asyncio.sleep(_COMMAND_POLL_INTERVAL_SECONDS)

    async def _read_worker_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                await self._handle_worker_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed reading runtime worker output")
        finally:
            ready_future = self._ready_future
            if ready_future is not None and not ready_future.done():
                ready_future.set_exception(RuntimeError("Runtime worker exited before ready"))
            if not self._expecting_shutdown:
                state = db.get_runtime_worker_state(_WORKER_NAME)
                if state is None or state.lifecycle_state not in {"stopped", "error"}:
                    db.mark_runtime_worker_error("Runtime worker exited unexpectedly", worker_name=_WORKER_NAME)

    async def _handle_worker_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "ready":
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_result(None)
            return
        if message_type == "fatal":
            error = message.get("error") or "Runtime worker failed"
            logger.error("Runtime worker fatal error: %s", error)
            db.mark_runtime_worker_error(error, worker_name=_WORKER_NAME)
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_exception(RuntimeError(error))
            return
        if message_type == "event":
            payload = message.get("payload")
            if isinstance(payload, dict):
                await self._dispatch_event(payload)
            return
        logger.warning("Unknown runtime worker message: %s", message)

    async def _dispatch_event(self, envelope: dict[str, Any]) -> None:
        kind = envelope.get("kind")
        data = envelope.get("data") or {}

        if self._telegram_bridge is not None:
            try:
                await self._telegram_bridge.dispatch(kind, data)
            except Exception:
                logger.warning("Telegram bridge dispatch failed for %s", kind, exc_info=True)

        sink = self._event_sink
        if sink is None:
            return

        if kind == "world_state":
            await sink.broadcast_world_state()
            return
        if kind == "runtime_state":
            await sink.broadcast_runtime_state(data["payload"])
            return
        if kind == "chat_message":
            await sink.broadcast_chat_message(**data)
            return
        if kind == "meeting_message":
            await sink.broadcast_meeting_message(**data)
            return
        if kind == "channel_message":
            await sink.broadcast_channel_message(**data)
            return
        if kind == "diagnostic":
            await sink.broadcast_diagnostic(data["summary"])
            return
        if kind == "thought":
            await sink.broadcast_thought(
                data["agent_id"],
                data["thought"],
                data["action_name"],
            )
            return
        if kind == "activity":
            await sink.broadcast_activity(
                event=data["event"],
                detail=data["detail"],
                agent_name=data.get("agent_name"),
                extra=data.get("extra"),
            )
            return
        if kind == "feed_update":
            await sink.broadcast_feed_update(data["entry"])
            return
        logger.warning("Unknown runtime event kind received: %s", kind)


runtime_services = RuntimeServices()
