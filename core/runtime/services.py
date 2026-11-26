"""BossMod AI — app-side gateway for the isolated runtime worker."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import sqlite3
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from core import config
from core.runtime.presence import PresenceRegistry
from core.runtime.supervisor import (
    RUNTIME_DOWN_EVENT,
    RUNTIME_RESTARTED_EVENT,
    RestartHistory,
    WorkerOutage,
    restart_allowed,
)
import db
import db.connection as db_connection

logger = logging.getLogger(__name__)

_WORKER_READY_TIMEOUT_SECONDS = 10.0
_WORKER_STOP_TIMEOUT_SECONDS = 2.0
_COMMAND_WAIT_TIMEOUT_SECONDS = 10.0
_COMMAND_POLL_INTERVAL_SECONDS = 0.05
# How much of a dropped worker output line its ERROR log shows.
_OUTPUT_PREVIEW_BYTES = 200
_WORKER_NAME = "primary"
_HUMAN_PREEMPTED_TRIGGER_TYPES = ["activity_resumed", "watchdog_status_ping", "social"]
# Set to "1" in the worker's own environment (see ``_start_unlocked``).
_WORKER_ENV = "BOSSMOD_RUNTIME_WORKER"
# Supervision settings (db/settings.py), each read when it is used.
_RESTART_DELAY_SETTING = "runtime_worker_restart_delay_seconds"
_RESTART_LIMIT_SETTING = "runtime_worker_restart_limit"
_RESTART_WINDOW_SETTING = "runtime_worker_restart_window_minutes"
_UNRESPONSIVE_SETTING = "runtime_worker_unresponsive_seconds"


def is_runtime_worker() -> bool:
    """Return whether this process is the runtime worker (not the app process)."""
    return os.environ.get(_WORKER_ENV) == "1"


class EventSink(Protocol):
    """App-process broadcast surface. Wired in ``main.py`` lifespan."""

    async def broadcast_world_state(self) -> None: ...

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None: ...

    async def broadcast_chat_message(self, **data: Any) -> None: ...

    async def broadcast_meeting_message(self, **data: Any) -> None: ...

    async def broadcast_peer_message(self, **data: Any) -> None: ...

    async def broadcast_channel_message(self, **data: Any) -> None: ...

    async def broadcast_agent_presence(self, **data: Any) -> None: ...

    async def broadcast_presence_snapshot(self, rows: list[dict[str, Any]]) -> None: ...

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
        self._telegram_queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self._telegram_consumer: asyncio.Task[None] | None = None
        self._event_sink: EventSink | None = None
        # Who is mid-turn, from the worker's agent_presence deltas; sent to
        # each WebSocket client on connect (presence_snapshot).
        self._presence = PresenceRegistry()
        # Supervision. Why the worker last stopped unexpectedly, held until a
        # start succeeds (which logs the restart once), and the outage once
        # automatic restarts gave up (the Needs card).
        self._incident: str | None = None
        self._outage: WorkerOutage | None = None
        self._restart_history = RestartHistory()
        self._recovery_task: asyncio.Task[None] | None = None
        self._heartbeat_watch: asyncio.Task[None] | None = None

    def set_telegram_bridge(self, bridge: Any) -> None:
        """Attach the Telegram event bridge and start its one consumer task.

        Runtime events reach the bridge through a bounded queue drained by a
        single consumer, so a slow Telegram API never stalls the worker-event
        reader (and through it every WebSocket broadcast). One consumer keeps
        the bridge's delivery order the order the events arrived in.

        Call on the app's event loop, once; :meth:`stop` shuts the consumer
        down and detaches the bridge.

        Args:
            bridge: Anything with ``async dispatch(kind, data)``
                (``integrations.telegram.bridge.TelegramEventBridge``).

        Raises:
            RuntimeError: A bridge is already attached.
            ConfigError: ``telegram_dispatch_queue_size`` is missing or not an int.
        """
        if self._telegram_consumer is not None:
            raise RuntimeError("A Telegram bridge is already attached")
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=config.require_int("telegram_dispatch_queue_size")
        )
        self._telegram_queue = queue
        self._telegram_consumer = asyncio.create_task(_drain_telegram(bridge, queue))

    async def _stop_telegram_consumer(self) -> None:
        """Cancel the Telegram consumer and detach the bridge; events still queued are dropped."""
        consumer = self._telegram_consumer
        queue = self._telegram_queue
        self._telegram_consumer = None
        self._telegram_queue = None
        if consumer is None:
            return
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
        if queue is not None and queue.qsize():
            logger.info("Telegram bridge stopped with %d undelivered event(s)", queue.qsize())

    def set_event_sink(self, sink: EventSink) -> None:
        """Attach the WebSocket (or test) broadcast sink. ``core`` must not import ``api``."""
        self._event_sink = sink

    def presence_snapshot(self) -> list[dict[str, Any]]:
        """Return who is thinking or queued right now, for a connecting client.

        The materialized view of every ``agent_presence`` delta the worker has
        emitted since it started (``core/runtime/presence.py``), so a browser
        that connects, reconnects or reloads mid-turn still sees the turn.

        Returns:
            Shallow copies of every row:
            ``{agent_id, agent_name, channel_id, phase, ahead, since}``.
            Empty while no turn is queued or running, and after the worker
            exits.
        """
        return self._presence.snapshot()

    async def start(self) -> None:
        """Start the dedicated runtime worker process."""
        db.init_db()
        async with self._guard():
            await self._start_unlocked()

    async def stop(self) -> None:
        """Stop the runtime worker process and the Telegram consumer, if any.

        The automatic recovery and the heartbeat watch are cancelled first
        (``_stop_unlocked``), so the stop never races a restart.
        """
        async with self._guard():
            await self._stop_unlocked()
        await self._stop_telegram_consumer()

    async def reseed_application_data(self) -> None:
        """Recreate the application database from the current schema and restart services.

        AI connections are carried across: a base URL and an API key are the one
        thing the operator cannot get back from inside the app. Everything else —
        agents, tasks, chat history — is what the reseed is for.

        The capture has to happen here rather than inside ``reset_database()``,
        which stays dumb and keeps meaning "recreate the file", and it has to
        happen before it: afterwards the rows are gone and the connection closed.
        Restoring lands after the schema is rebuilt and before ``config.reload()``
        so the runtime boots with the connections already in place. Nothing
        guards the capture — a reseed that could not read the keys must fail with
        the old database still on disk rather than destroy them.
        """
        async with self._guard():
            await self._stop_unlocked()
            preserved = db.list_connections()
            db.reset_database()
            restored = db.restore_connections(preserved)
            logger.info("Application reseeded; %d AI connection(s) preserved", restored)
            config.reload()
            await self._start_unlocked()

    def outage(self) -> WorkerOutage | None:
        """Return the current outage: automatic restarts gave up and the worker is still stopped.

        Read by the Needs list (``api/routes/needs.py``). Set when a recovery
        runs out of attempts, or cannot read its settings; cleared by any
        start that succeeds and by :meth:`restart_worker`. Held in this
        process only: an app restart starts the worker afresh.

        Returns:
            The outage, or ``None`` while the worker runs or is being
            restarted automatically.
        """
        return self._outage

    async def restart_worker(self) -> dict[str, object]:
        """Start the runtime worker again now: the operator's Restart now (Needs card, Telegram card).

        Under the guard, so it waits for a start already under way instead of
        cancelling it halfway: a pending automatic recovery is cancelled, the
        restart history and the outage are cleared, then the worker is started
        unless it already runs. A start that succeeds ends the incident as
        every start does (the restart is logged once). A start that fails is
        handed back to automatic recovery with the fresh history, so the
        operator is told again if those restarts give up too.

        Returns:
            :meth:`status_payload` after the start.

        Raises:
            Exception: Whatever the start raised (``_start_unlocked``); the
                worker row already holds it as ``last_error``.
        """
        async with self._guard():
            await _cancel_and_wait(self._recovery_task)
            self._restart_history.clear()
            self._outage = None
            try:
                await self._start_unlocked()
            except Exception:
                self._schedule_recovery("it could not start", kill=False)
                raise
        return self.status_payload()

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
            # The shared threshold: 3x runtime_heartbeat_seconds.
            healthy = heartbeat_age <= db.heartbeat_stale_after_seconds()
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
                self._ring_worker()
                await self._wait_for_command(command.id)
            return self.status_payload()

    async def resume(self) -> dict[str, object]:
        async with self._guard():
            db.set_setting(self._STATE_KEY, self._RUNNING, "advanced")
            config.reload()
            await self._start_unlocked()
            command = db.create_runtime_command("resume_runtime")
            self._ring_worker()
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
        # Before start(): a trigger that will never be written has no reason
        # to spin the runtime up.
        from core.floors import agent_wake_refusal

        refusal = agent_wake_refusal(agent_id)
        if refusal is not None:
            logger.info("Skipped %s trigger for %s: %s", trigger_type, agent_id, refusal)
            return
        await self.start()
        if trigger_type == "human_chat":
            db.delete_queued_triggers(agent_id, trigger_types=_HUMAN_PREEMPTED_TRIGGER_TYPES)
        if db.payload_targets_archived_channel(payload):
            return
        db.create_agent_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )
        from core.agent_loop.queue_visibility import emit_queue_visibility

        await emit_queue_visibility(agent_id)
        async with self._guard():
            if self._process_is_running() and not db.has_open_runtime_command(["wake_dispatcher"]):
                db.create_runtime_command("wake_dispatcher")
                self._ring_worker()

    async def reset_agent_runtime(self, agent_id: str) -> None:
        await self.start()
        async with self._guard():
            if not self._process_is_running():
                return
            command = db.create_runtime_command("reset_agent_runtime", {"agent_id": agent_id})
            self._ring_worker()
            await self._wait_for_command(command.id)

    def _ring_worker(self) -> None:
        """Ring the worker's doorbell: one newline on its stdin.

        Call right after filing a ``runtime_commands`` row. The row is the
        command and stays the source of truth; the newline only wakes the
        worker's command loop so it reads the queue now instead of at its
        fallback poll (``runtime_command_fallback_poll_seconds``).

        Safe from any thread: off the worker's event loop the write is handed
        to that loop. Does nothing when this process runs no worker (a
        starting worker deletes open rows and starts fresh). A closed or
        broken pipe, or a closed loop, is logged at WARNING and nothing else
        happens: the row stays queued for the fallback poll.
        """
        process = self._process
        loop = self._process_loop
        if process is None or loop is None or process.returncode is not None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a thread with no running event loop.
            current = None
        if current is loop:
            _write_doorbell(process)
            return
        try:
            loop.call_soon_threadsafe(_write_doorbell, process)
        except RuntimeError as exc:
            logger.warning(
                "Runtime worker doorbell not rung (%s); the command waits for the worker's fallback poll",
                exc,
            )

    def extension_config_changed(self, extension_id: str, agent_id: str) -> None:
        """Tell the worker one agent's settings for one extension were saved or removed.

        Files an ``extension_config_changed`` runtime command with payload
        ``{"extension_id", "agent_id"}`` and rings the doorbell. The worker
        then drops the wake service's cached configs and lets the extension
        apply the change (``agent_hooks.notify_agent_config_changed``), e.g.
        restart a running desktop with new specs. Never de-duplicated: an
        open command may already have been applied, and settings saves are
        rare. Does nothing when this process runs no worker; a starting
        worker reads every config fresh, and an extension applies stored
        settings when it next starts something.

        Args:
            extension_id: The extension whose settings changed.
            agent_id: The agent whose settings changed.
        """
        if not self._process_is_running():
            return
        db.create_runtime_command(
            "extension_config_changed",
            {"extension_id": extension_id, "agent_id": agent_id},
        )
        self._ring_worker()

    def extensions_reconcile(self) -> None:
        """Ask the worker to release turned-off extensions and what deleted agents held.

        Files an ``extensions_reconcile`` runtime command and rings the
        doorbell. The worker shuts down and unloads every extension the
        operator turned off (``agent_hooks.apply_extension_states``), then
        tells each enabled extension which agents exist
        (``agent_hooks.reconcile_agents``). Filed after an extension is
        turned on or off and after agents are deleted (one, all, or a
        floor's residents). Never de-duplicated,
        like :meth:`extension_config_changed`. Does nothing when this
        process runs no worker: a booting worker reconciles anyway.
        """
        if not self._process_is_running():
            return
        db.create_runtime_command("extensions_reconcile")
        self._ring_worker()

    def extensions_release_agents(self, agent_ids: list[str]) -> None:
        """Ask the worker to stop what extensions run for agents leaving service.

        Files an ``extensions_release_agents`` runtime command with payload
        ``{"agent_ids": [...]}`` and rings the doorbell. The worker then lets
        each enabled extension stop what it runs for them, keeping their
        data (``agent_hooks.release_agents``): a desktop powers down, a
        browser session closes. Filed by a stand-down (core/stand_down.py).
        Never de-duplicated, like :meth:`extension_config_changed`. Does
        nothing when this process runs no worker: desktops are stopped by
        the boot reconcile (it stops running ones this process did not
        start), and browser sessions end with the worker.

        Args:
            agent_ids: The agents leaving service.
        """
        if not self._process_is_running():
            return
        db.create_runtime_command("extensions_release_agents", {"agent_ids": list(agent_ids)})
        self._ring_worker()

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
        env["BOSSMOD_RUNTIME_WORKER"] = "1"
        self._process = await asyncio.create_subprocess_exec(
            *worker_cmd,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            # The doorbell (see _ring_worker); stdout stays the event channel.
            stdin=asyncio.subprocess.PIPE,
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
        await self._restart_heartbeat_watch()
        incident = self._incident
        if incident is not None:
            # Whoever started it, this start ends the incident and the outage.
            # Cleared before the announcement, so the restart is logged once.
            self._incident = None
            self._outage = None
            logger.info("Runtime worker restarted after %s", incident)
            try:
                await self._announce_runtime(RUNTIME_RESTARTED_EVENT, f"AI runtime restarted after {incident}.")
            except Exception:
                # The worker runs: a lost log line must not fail its start.
                logger.exception("The runtime worker restart was not announced")

    async def _stop_unlocked(self) -> None:
        # First, and before the awaits below: a recovery asked for while this
        # stop runs would bring the worker back (``_schedule_recovery``).
        self._expecting_shutdown = True
        await self._stop_supervision()
        process = self._process
        if process is None:
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)
            return

        db.mark_runtime_worker_stopping(process.pid if process.returncode is None else None, worker_name=_WORKER_NAME)

        process_loop = self._process_loop
        if process_loop is not asyncio.get_running_loop():
            await self._kill_process(process)
            await self._clear_process_state()
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)
            await self._announce_worker_exit()
            return

        if process.returncode is None:
            db.create_runtime_command("shutdown_runtime")
            self._ring_worker()
            try:
                await asyncio.wait_for(process.wait(), timeout=_WORKER_STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await self._force_stop_process(process)

        await self._clear_process_state()
        state = db.get_runtime_worker_state(_WORKER_NAME)
        if state is None or state.lifecycle_state not in {"stopped", "error"}:
            db.mark_runtime_worker_stopped(worker_name=_WORKER_NAME)
        await self._announce_worker_exit()

    async def _announce_worker_exit(self) -> None:
        """Tell the UI the worker is gone, and everything that lived in it.

        In-flight turns die with the worker exactly as extension sessions do,
        and a dead worker can never send their ``idle``. So the presence view
        is cleared and every client is sent the empty snapshot, then every
        extension's live state is announced as changed.
        """
        self._presence.clear()
        sink = self._event_sink
        if sink is not None:
            await sink.broadcast_presence_snapshot([])
        await self._announce_extension_sessions_changed()

    async def _announce_extension_sessions_changed(self) -> None:
        """Tell the UI every extension's live state may have changed.

        Extension sessions (e.g. Browser Vision's browsers) live in the worker
        and die with it, and the worker's own event sink is already gone by the
        time it shuts them down, so the app process announces worker start and
        exit as ``extension_live(None, None)``: "re-read everything".
        """
        sink = self._event_sink
        if sink is not None:
            await sink.broadcast_extension_live(None, None)

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
        """Read the worker's JSONL output line by line until the stream ends.

        This reader is the worker's only output path, so a fault in one line
        costs that line, never the stream: a line over the stream limit, a
        line that is not UTF-8 JSON or not a JSON object, and a message whose
        handling raises are each logged at ERROR and dropped, and reading
        continues. The loop ends at EOF, when ``readline()`` itself fails
        (logged at ERROR), or on cancellation, which is not caught: the
        canceller (``_clear_process_state``) owns the lifecycle.

        On the way out a pending ready wait fails. Then, unless a stop is
        expected: after EOF or a stream failure while ``returncode`` is still
        unset, :meth:`_settle_stream_end` gives the worker
        ``_WORKER_STOP_TIMEOUT_SECONDS`` to show its exit and stops it
        (fail-stop) when it does not; otherwise (an exit asyncio has already
        seen, or a cancellation) :meth:`_record_unexpected_exit` runs. A
        stream that ended (not a cancelled reader) then asks for an automatic
        restart (:meth:`_schedule_recovery`), from its own task.
        """
        process = self._process
        if process is None or process.stdout is None:
            return
        # How the stream ended, for the finally: "EOF" or the readline()
        # failure. Still None when the loop was cancelled.
        ended: str | None = None
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError as exc:
                    # Over the StreamReader limit: asyncio has already dropped
                    # the line and the stream stays usable.
                    logger.error("Dropped a runtime worker output line over the stream limit: %s", exc)
                    continue
                except Exception as exc:
                    # The stream itself failed (an exception set on the reader
                    # is raised again by every later read), so reading ends.
                    logger.exception("Runtime worker output stream failed")
                    ended = f"{type(exc).__name__}: {exc}"
                    break
                if not line:
                    ended = "EOF"
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    # E.g. a stray print() from a library inside the worker.
                    logger.error(
                        "Dropped a runtime worker output line that is not UTF-8 JSON (%s): %r",
                        exc,
                        line[:_OUTPUT_PREVIEW_BYTES],
                    )
                    continue
                if not isinstance(message, dict):
                    logger.error(
                        "Dropped a runtime worker output line that is not a JSON object: %r",
                        line[:_OUTPUT_PREVIEW_BYTES],
                    )
                    continue
                try:
                    await self._handle_worker_message(message)
                except Exception:
                    payload = message.get("payload")
                    label = payload.get("kind") if isinstance(payload, dict) else message.get("type")
                    # The database stays the source of truth for what the UI missed.
                    logger.exception("Runtime worker %s event was not delivered; reading continues", label)
        finally:
            ready_future = self._ready_future
            if ready_future is not None and not ready_future.done():
                ready_future.set_exception(RuntimeError("Runtime worker exited before ready"))
            if ended is not None and not self._expecting_shutdown and process.returncode is None:
                await self._settle_stream_end(process, ended)
            else:
                await self._record_unexpected_exit()
                # Never after a cancellation: the canceller is (re)starting or
                # stopping the worker, and asking here would loop.
                if ended is not None:
                    self._schedule_recovery("it stopped unexpectedly", kill=False)

    async def _settle_stream_end(self, process: asyncio.subprocess.Process, reason: str) -> None:
        """Tell a worker's exit from a worker the app can no longer hear, once its stream ended.

        asyncio sets ``returncode`` a few loop turns after stdout's EOF, so
        ``None`` there does not yet mean the worker runs. It gets
        ``_WORKER_STOP_TIMEOUT_SECONDS`` to show its exit:

        - it exited: :meth:`_record_unexpected_exit`, as for any exit, so a
          reason the worker recorded on its own row is kept; then an
          automatic restart is asked for, unless a stop began meanwhile;
        - it still runs, and no stop began meanwhile: it is stopped, its row
          is marked ``error`` with ``reason``, its exit is announced, and an
          automatic restart is asked for. A worker the app cannot hear must
          not keep running: its turns would block on output while their
          leases keep them looking alive;
        - a stop began during the wait: that stop owns the exit;
        - this reader is cancelled during the wait: what a cancelled reader
          always does (:meth:`_record_unexpected_exit`), then the
          cancellation goes on. No restart is asked for: the canceller owns
          the worker.

        Args:
            process: The worker whose stdout ended.
            reason: How it ended: ``EOF``, or the ``readline()`` failure.
        """
        try:
            await asyncio.wait_for(process.wait(), timeout=_WORKER_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if self._expecting_shutdown:
                return
            logger.error(
                "Runtime worker output stream ended while the worker was running (%s); stopping the worker",
                reason,
            )
            # Only after the wait: terminate() on a child whose exit asyncio
            # has not seen yet can reap it first and leave a wrong returncode.
            await self._force_stop_process(process)
            db.mark_runtime_worker_error(
                f"Runtime worker stopped: its output stream ended while it was running ({reason})",
                worker_name=_WORKER_NAME,
            )
            await self._announce_worker_exit()
            self._schedule_recovery("its output stream ended while it was running", kill=False)
            return
        except asyncio.CancelledError:
            await self._record_unexpected_exit()
            raise
        await self._record_unexpected_exit()
        self._schedule_recovery("it stopped unexpectedly", kill=False)

    async def _record_unexpected_exit(self) -> None:
        """Mark the worker row ``error`` and announce the exit, unless a stop is expected.

        A row already ``stopped`` or ``error`` keeps its state and reason
        (e.g. the worker's own ``fatal`` reason).
        """
        if self._expecting_shutdown:
            # A normal stop announces from _stop_unlocked instead.
            return
        state = db.get_runtime_worker_state(_WORKER_NAME)
        if state is None or state.lifecycle_state not in {"stopped", "error"}:
            db.mark_runtime_worker_error("Runtime worker exited unexpectedly", worker_name=_WORKER_NAME)
        await self._announce_worker_exit()

    # ─── Supervision: automatic restarts, the heartbeat watch, the outage ───

    def _schedule_recovery(
        self,
        reason: str,
        *,
        kill: bool,
        target: asyncio.subprocess.Process | None = None,
    ) -> bool:
        """Ask for an automatic restart of the worker, run in a task of its own.

        Never inside the reader task: a start cancels the reader
        (``_clear_process_state``), so a recovery running there would cancel
        itself. Synchronous, so a caller on its way out (the reader's
        ``finally``) asks without yielding first.

        Refused while a stop is expected (``_expecting_shutdown``: a stop, a
        reseed, or a recovery's own kill owns the worker), and while a
        recovery already runs. That one covers the request: its next start
        restarts a worker that has exited, and after a start it checks
        whether that worker already ended.

        Args:
            reason: Why the worker needs restarting, as a plain phrase that
                reads after "AI runtime restarted after" and "the last
                time," (e.g. "it stopped unexpectedly").
            kill: Stop the worker first: it runs but stopped responding.
            target: With ``kill``, the worker process that was judged
                unresponsive. Only that process is ever stopped, so a
                worker started in its place before the recovery runs is
                left alone. Given exactly when ``kill`` is.

        Returns:
            Whether a recovery was started.

        Raises:
            ValueError: ``kill`` without a ``target``, or a ``target``
                without ``kill``.
        """
        if kill != (target is not None):
            raise ValueError(f"A recovery takes a target process exactly when it kills (kill={kill})")
        if self._expecting_shutdown:
            return False
        running = self._recovery_task
        if running is not None and not running.done():
            logger.info("A runtime worker recovery is already running; it covers this one (%s)", reason)
            return False
        task = asyncio.create_task(
            self._recover(reason, kill=kill, target=target), name="runtime-worker-recovery",
        )
        task.add_done_callback(_log_supervision_failure)
        self._recovery_task = task
        return True

    async def _recover(
        self,
        reason: str,
        *,
        kill: bool,
        target: asyncio.subprocess.Process | None = None,
    ) -> None:
        """Restart the worker after the restart delay, under the crash-loop guard, or record the outage.

        The incident is recorded first (an open one keeps its first cause,
        which is why the worker stopped), so the start that ends it logs the
        restart once, whoever performs it. With ``kill`` the ``target`` is
        first stopped under the guard as a deliberate stop; its row is
        marked ``error`` and its exit announced, as for any worker that is
        gone. Only while it is still the worker this process runs: a target
        that already exited, or was replaced while this recovery waited for
        the guard, is not stopped and its row is left as it is.

        Each attempt is recorded in the sliding window. Past
        ``runtime_worker_restart_limit`` attempts within
        ``runtime_worker_restart_window_minutes`` the restarts give up
        (:meth:`_give_up`). Otherwise it sleeps
        ``runtime_worker_restart_delay_seconds`` without holding the guard,
        so an operator's Pause, Resume or Restart now never waits behind it,
        then starts the worker under the guard. That start does nothing when
        someone else already restarted the worker. A start that raises is
        logged with its traceback and tried again as "it could not start";
        a worker that ended before this recovery returned (its reader's
        request found this one still running) is tried again as "it stopped
        unexpectedly".

        Each setting is read when it is used. An unusable one is logged at
        ERROR and the restarts give up at once, so the operator is told
        instead of being left with a stopped worker.

        Args:
            reason: Why the worker needs restarting (see :meth:`_schedule_recovery`).
            kill: Stop ``target`` first.
            target: The worker judged unresponsive; given exactly when
                ``kill`` is (checked by :meth:`_schedule_recovery`).

        Raises:
            ValueError: ``kill`` without a ``target``.
            asyncio.CancelledError: Cancelled by a stop, a reseed or Restart
                now, which hold the guard while they cancel; never caught.
        """
        if kill and target is None:
            raise ValueError("A recovery that kills needs the target process it judged")
        if self._incident is None:
            self._incident = reason
        if kill and target is not None:
            async with self._guard():
                if self._process is target and target.returncode is None:
                    # Before the kill, so the reader's finally does not take it for a crash.
                    self._expecting_shutdown = True
                    await self._force_stop_process(target)
                    await self._clear_process_state()
                    db.mark_runtime_worker_error(f"Runtime worker stopped: {reason}", worker_name=_WORKER_NAME)
                    await self._announce_worker_exit()
                else:
                    # It exited, or another worker took its place while this
                    # waited; the loop below restarts only a worker that is gone.
                    logger.info("Unresponsive runtime worker pid %s is no longer running; not stopping", target.pid)
        tried = 0
        while True:
            try:
                window_seconds = _require_positive_float(_RESTART_WINDOW_SETTING) * 60
                limit = config.require_positive_int(_RESTART_LIMIT_SETTING)
                delay = _require_positive_float(_RESTART_DELAY_SETTING)
            except config.ConfigError:
                logger.exception("Runtime worker automatic restarts stopped: a restart setting is unusable")
                await self._give_up(reason, attempts=tried)
                return
            count = self._restart_history.record(time.monotonic(), window_seconds=window_seconds)
            if not restart_allowed(count, limit=limit):
                await self._give_up(reason, attempts=count - 1)
                return
            logger.warning(
                "Restarting the runtime worker in %g s because %s (attempt %d of %d within %g min)",
                delay, reason, count, limit, window_seconds / 60,
            )
            await asyncio.sleep(delay)
            tried += 1
            async with self._guard():
                try:
                    await self._start_unlocked()
                except Exception:
                    logger.exception("Runtime worker restart failed")
                    reason = "it could not start"
                    continue
            reader = self._reader_task
            if reader is None or not reader.done():
                return
            # The worker it left running is already gone, and that reader's
            # request was refused while this recovery still ran.
            reason = "it stopped unexpectedly"
            self._incident = reason

    async def _give_up(self, reason: str, *, attempts: int) -> None:
        """Record the outage and announce it: the Need (bell, toast, tray badge) and the Telegram card.

        Args:
            reason: Why the worker stopped the last time.
            attempts: Automatic restarts tried within the restart window.
        """
        outage = WorkerOutage(since=datetime.now(timezone.utc), reason=reason, attempts=attempts)
        self._outage = outage
        logger.error(
            "Runtime worker automatic restarts gave up after %d attempt(s); the last time, %s", attempts, reason,
        )
        await self._announce_runtime(
            RUNTIME_DOWN_EVENT,
            outage.describe(),
            extra={"since": outage.since.isoformat(), "attempts": attempts, "reason": reason},
        )

    async def _restart_heartbeat_watch(self) -> None:
        """Replace the heartbeat watch with one for the worker that just became ready."""
        await _cancel_and_wait(self._heartbeat_watch)
        task = asyncio.create_task(self._watch_heartbeat(), name="runtime-worker-heartbeat-watch")
        task.add_done_callback(_log_supervision_failure)
        self._heartbeat_watch = task

    async def _stop_supervision(self) -> None:
        """Cancel the automatic recovery, then the heartbeat watch, and wait until both have stopped."""
        await _cancel_and_wait(self._recovery_task)
        await _cancel_and_wait(self._heartbeat_watch)
        self._recovery_task = None
        self._heartbeat_watch = None

    async def _watch_heartbeat(self) -> None:
        """Restart a worker that runs but stopped responding: its heartbeat has not moved for too long.

        Every ``runtime_heartbeat_seconds`` it reads the worker row. Only a
        row that reads ``running`` with this app's child's pid is judged; a
        row marked ``error`` has its pid cleared, so it never matches. The
        silence is measured on this process's monotonic clock from when the
        watch last saw the heartbeat change, not from the stamp's wall-clock
        age: across a computer's sleep both processes stop, and the wall
        clock's jump must not read as a frozen worker. Past
        ``runtime_worker_unresponsive_seconds`` of silence (its own
        threshold, far above the 3x display one, so one slow prune never
        kills a healthy worker) it asks for a recovery that kills the
        process it judged, and ends. While another recovery runs, the
        request is refused and it keeps watching.

        A read or settings error is logged at ERROR and the next check
        still runs, at the last usable interval. It runs until cancelled (a
        start replaces it; a stop ends it); with no usable interval at all
        it logs that at ERROR and ends.
        """
        interval: float | None = None
        seen: datetime | None = None
        seen_at = 0.0
        while True:
            try:
                interval = db.heartbeat_interval_seconds()
            except config.ConfigError:
                if interval is None:
                    logger.exception("Runtime worker heartbeat watch did not start; this worker is not watched")
                    return
                logger.exception("Runtime worker heartbeat watch keeps its last usable interval (%g s)", interval)
            await asyncio.sleep(interval)
            try:
                state = db.get_runtime_worker_state(_WORKER_NAME)
                threshold = _require_positive_float(_UNRESPONSIVE_SETTING)
            except (config.ConfigError, sqlite3.Error):
                logger.exception("Runtime worker heartbeat check failed; checking again in %g s", interval)
                continue
            process = self._process
            if (
                state is None
                or process is None
                or state.lifecycle_state != "running"
                or state.pid != process.pid
                or state.last_heartbeat_at is None
            ):
                seen = None
                continue
            now = time.monotonic()
            if state.last_heartbeat_at != seen:
                seen, seen_at = state.last_heartbeat_at, now
                continue
            silent = now - seen_at
            if silent > threshold and self._schedule_recovery("it stopped responding", kill=True, target=process):
                logger.error(
                    "Runtime worker pid %s recorded no heartbeat for %.1f s; stopping and restarting it",
                    process.pid,
                    silent,
                )
                return

    async def _announce_runtime(self, event: str, detail: str, extra: dict[str, Any] | None = None) -> None:
        """Announce an app-side runtime event: to the activity log and every client, then to Telegram.

        For what only the app can see (the worker restarted, or restarts
        gave up). The sink persists it to the activity log and broadcasts it
        (``ConnectionManager.broadcast_activity``). The Telegram bridge gets
        the shape it reads from the worker's own ``activity`` events; a full
        Telegram queue drops it for Telegram only, logged at WARNING.

        Args:
            event: The activity event name (``core/runtime/supervisor.py``).
            detail: The plain-language line the activity log shows.
            extra: Fields merged into the broadcast entry, or ``None``.
        """
        sink = self._event_sink
        if sink is not None:
            await sink.broadcast_activity(event=event, detail=detail, agent_name=None, extra=extra)
        queue = self._telegram_queue
        if queue is None:
            return
        try:
            queue.put_nowait(("activity", {"event": event, "detail": detail, "agent_name": None, "extra": extra}))
        except asyncio.QueueFull:
            logger.warning(
                "Telegram dispatch queue is full (%d); dropped one %s event for Telegram",
                queue.maxsize,
                event,
            )

    async def _handle_worker_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "ready":
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_result(None)
            await self._announce_extension_sessions_changed()
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

        if kind == "agent_presence":
            # Ahead of the sink check: the connect snapshot must hold every
            # row the worker emitted, whether or not a sink was attached yet.
            try:
                self._presence.apply(data)
            except ValueError as exc:
                # Not forwarded anywhere, so no client holds a row the snapshot
                # lacks. Raising would end _read_worker_output's whole stream.
                logger.error("Dropped malformed presence event: %s (%s)", data, exc)
                return

        queue = self._telegram_queue
        if queue is not None:
            try:
                queue.put_nowait((kind, data))
            except asyncio.QueueFull:
                logger.warning(
                    "Telegram dispatch queue is full (%d); dropped one %s event for Telegram",
                    queue.maxsize,
                    kind,
                )

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
        if kind == "peer_message":
            await sink.broadcast_peer_message(**data)
            return
        if kind == "channel_message":
            await sink.broadcast_channel_message(**data)
            return
        if kind == "agent_presence":
            await sink.broadcast_agent_presence(**data)
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
        if kind == "extension_live":
            await sink.broadcast_extension_live(**data)
            return
        if kind == "conversation_notes":
            await sink.broadcast_conversation_notes(data["scope_kind"], data["scope_key"])
            return
        if kind == "task_step":
            await sink.broadcast_task_step(data["step"])
            return
        logger.warning("Unknown runtime event kind received: %s", kind)


async def _drain_telegram(bridge: Any, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
    """Hand queued runtime events to the Telegram bridge, one at a time, until cancelled.

    A failed dispatch is logged at WARNING and the next event is still
    delivered: one bad send must not end Telegram forwarding.
    """
    while True:
        kind, data = await queue.get()
        try:
            await bridge.dispatch(kind, data)
        except Exception:
            logger.warning("Telegram bridge dispatch failed for %s", kind, exc_info=True)


def _require_positive_float(key: str) -> float:
    """Read a required setting as a finite number greater than 0.

    Raises:
        ConfigError: The setting is missing, not a number, not finite, or
            not greater than 0.
    """
    value = config.require_float(key)
    if not math.isfinite(value) or value <= 0:
        raise config.ConfigError(f"Required setting '{key}' must be a number greater than 0: {value!r}")
    return value


async def _cancel_and_wait(task: asyncio.Task[None] | None) -> None:
    """Cancel ``task`` and, when it runs on this event loop, wait until it has stopped.

    A task from another loop (an earlier test's) is only cancelled, as
    ``_clear_process_state`` treats the reader.
    """
    if task is None or task.done():
        return
    task.cancel()
    if task.get_loop() is asyncio.get_running_loop():
        with suppress(asyncio.CancelledError):
            await task


def _log_supervision_failure(task: asyncio.Task[None]) -> None:
    """Log a supervision task that ended with an exception, which nothing else awaits.

    A cancelled task ended on purpose and is not logged.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Runtime worker %s failed", task.get_name(), exc_info=exc)


def _write_doorbell(process: asyncio.subprocess.Process) -> None:
    """Write one newline to the worker's stdin; on the worker's event loop only.

    asyncio's pipe transport never raises on a broken pipe: it closes itself
    and drops the write. So the writer is checked before and after, and
    either failure is logged at WARNING.
    """
    stdin = process.stdin
    if stdin is None or stdin.is_closing():
        logger.warning("Runtime worker doorbell is closed; the command waits for the worker's fallback poll")
        return
    stdin.write(b"\n")
    if stdin.is_closing():
        logger.warning("Runtime worker doorbell pipe broke; the command waits for the worker's fallback poll")


runtime_services = RuntimeServices()


# The worker's own wake-up for commands filed inside the worker process; set
# by RuntimeWorker.run (core/runtime/worker.py) while it runs.
_local_doorbell: Callable[[], None] | None = None


def register_local_doorbell(ring: Callable[[], None] | None) -> None:
    """Worker process only: install (or, with ``None``, remove) the in-process doorbell.

    ``ring`` must be safe to call from any thread and must not raise.
    """
    global _local_doorbell
    _local_doorbell = ring


def notify_runtime_command_queued() -> None:
    """Ring the worker's doorbell after filing a runtime command outside ``RuntimeServices``.

    For code that writes a ``runtime_commands`` row itself
    (``request_dispatcher_wake``, ``core.scheduling.service.request_reload``).
    In the app process it rings the worker's stdin, and does nothing when
    this process runs no worker. In the worker process
    (``is_runtime_worker()``) it wakes the worker's own command loop
    directly. A worker with no doorbell registered (not started yet, or
    exiting) is logged at WARNING and the row waits for the fallback poll.
    Never raises; a failed stdin ring is logged at WARNING by
    ``RuntimeServices._ring_worker``.
    """
    if is_runtime_worker():
        ring = _local_doorbell
        if ring is None:
            logger.warning(
                "Runtime command queued in the worker with no doorbell registered; it waits for the fallback poll"
            )
            return
        ring()
        return
    runtime_services._ring_worker()


def request_dispatcher_wake() -> None:
    """Ask the runtime worker's dispatcher to look at its queue now.

    For app-process code that has just persisted work (a released model-call
    lane, triggers written outside ``RuntimeServices.enqueue_trigger``) and
    should not wait for the worker's next poll. It files one
    ``wake_dispatcher`` runtime command, and only when the worker process is
    recorded as running and no wake is already open, so repeated calls
    collapse into one. Inside the worker process it does nothing: the
    dispatcher there is already awake.

    Synchronous and safe to call from sync code; it reads the worker state
    from the database rather than this process's ``RuntimeServices``, since
    callers such as the model-call budget run outside it.

    Failure: any error is logged at debug level and swallowed, so a caller's
    own work never fails on the wake. The worker's poll still picks the work
    up, only later.
    """
    if os.environ.get(_WORKER_ENV) == "1":
        return
    try:
        state = db.get_runtime_worker_state("primary")
        if state is None or state.lifecycle_state != "running":
            return
        if db.has_open_runtime_command(["wake_dispatcher"]):
            return
        db.create_runtime_command("wake_dispatcher")
        notify_runtime_command_queued()
    except Exception:
        logger.debug("model-call lane release did not wake the worker", exc_info=True)
