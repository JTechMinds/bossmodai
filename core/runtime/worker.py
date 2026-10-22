"""BossMod AI — dedicated runtime worker process."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import signal
import sys
import threading
from contextlib import suppress
from typing import Any, Awaitable, Callable

import db
from core import config
from core.agent_loop.channel_idle_check import channel_idle_watch
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.meeting_watchdog import meeting_watchdog
from core.agent_loop.watchdog import watchdog
from core.bm_cli import background_processes
from core.extensions.agent_hooks import apply_extension_states, notify_agent_config_changed, reconcile_agents
from core.extensions.loader import shutdown_loaded_extensions
from core.extensions.wake_service import extension_wake_watch
from core.runtime.events import NullRuntimeEventSink, TransportRuntimeEventSink, runtime_events
from core.runtime.services import register_local_doorbell
from core.scheduling.watch import schedule_watch
from core.world.simulation import simulation

logger = logging.getLogger(__name__)

_FALLBACK_POLL_SETTING = "runtime_command_fallback_poll_seconds"
_PARENT_CHECK_INTERVAL_SECONDS = 1.0
_WORKER_NAME = "primary"


class RuntimeSettingError(Exception):
    """A runtime worker interval setting is missing, not a number, or not positive."""


def fallback_poll_seconds() -> float:
    """Return how long the command loop waits for the doorbell before polling anyway.

    Seeded in ``db/settings.py``. Read from the settings cache, so call
    ``config.refresh_if_changed()`` first to see another process's edit.

    Raises:
        RuntimeSettingError: The setting is missing, not a number, or not
            greater than 0. There is no fallback value.
    """
    try:
        value = config.require_float(_FALLBACK_POLL_SETTING)
    except config.ConfigError as exc:
        raise RuntimeSettingError(str(exc)) from exc
    if value <= 0:
        raise RuntimeSettingError(f"setting {_FALLBACK_POLL_SETTING!r} must be greater than 0, got {value}")
    return value


class _LastGoodInterval:
    """One positive seconds setting, re-read on every use.

    The first read (at worker start) raises, so an unusable value fails the
    start loudly. Later, an unusable edit keeps the last good value and is
    logged at ERROR once per distinct problem, so a bad Settings edit cannot
    kill a loop.

    Args:
        name: What the value is, for the log lines.
        read: Returns the current value or raises ``errors``.
        errors: The exception types that mean "unusable value".
    """

    def __init__(self, name: str, read: Callable[[], float], errors: tuple[type[Exception], ...]) -> None:
        self._name = name
        self._read = read
        self._errors = errors
        self._value = read()
        self._error: str | None = None

    def current(self) -> float:
        """Return the setting now, or the last good value when it became unusable."""
        try:
            value = self._read()
        except self._errors as exc:
            if str(exc) != self._error:
                logger.error("Keeping the last usable %s (%ss): %s", self._name, self._value, exc)
                self._error = str(exc)
            return self._value
        if self._error is not None:
            logger.info("The %s setting is usable again", self._name)
            self._error = None
        self._value = value
        return value


class StdinDoorbell:
    """Turn bytes on this process's stdin into an ``asyncio.Event`` the command loop awaits.

    The app writes one newline after it files a ``runtime_commands`` row; the
    row is the command and the newline only says "look now". A daemon thread
    does the blocking read, so the event loop never waits on stdin, and hands
    each wake-up to the loop with ``call_soon_threadsafe``. A thread rather
    than ``loop.connect_read_pipe``: the app also ships on Windows, whose
    proactor loop cannot read a stdin pipe that way, and a blocking read in
    its own thread behaves the same everywhere.

    End-of-file or a read error stops the thread with one warning; the
    command loop's fallback poll keeps every command flowing without it.

    Args:
        loop: The worker's running event loop.
        event: Set (on ``loop``) each time bytes arrive.
    """

    _READ_SIZE = 4096

    def __init__(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        self._loop = loop
        self._event = event

    def start(self) -> None:
        """Start the reader thread. It is a daemon: it never holds the process open."""
        threading.Thread(target=self._read, name="runtime-doorbell", daemon=True).start()

    def _read(self) -> None:
        if sys.stdin is None:
            logger.warning("Runtime worker has no stdin; commands are picked up by the fallback poll only")
            return
        try:
            fileno = sys.stdin.fileno()
        except (OSError, ValueError) as exc:
            logger.warning(
                "Runtime worker stdin is unusable (%s); commands are picked up by the fallback poll only", exc
            )
            return
        while True:
            try:
                # One read can carry several rings; one wake-up covers them all.
                data = os.read(fileno, self._READ_SIZE)
            except OSError as exc:
                logger.warning("Runtime worker doorbell read failed (%s); falling back to polling", exc)
                return
            if not data:
                logger.warning("Runtime worker doorbell closed (stdin end-of-file); falling back to polling")
                return
            try:
                self._loop.call_soon_threadsafe(self._event.set)
            except RuntimeError:
                # The loop is closed: the worker is exiting and nothing is waiting.
                return


ExtensionJob = Callable[[], Awaitable[None]]


class ExtensionLane:
    """Run the worker's extension jobs one at a time, off the runtime command loop.

    The command loop handles one command at a time, and the app waits only
    seconds for a pause, a shutdown or the worker's ``ready``. An extension
    hook can take far longer (a desktop restart, a reconcile that stops
    desktops), so the command handlers queue it here and return at once.
    One consumer task runs the jobs sequentially, so they keep their filing
    order (a reconcile filed after a settings change runs after it).

    A job that raises is logged with its traceback and the next job runs.
    The ``agent_hooks`` functions log extension failures themselves, so what
    reaches the lane is a host bug (e.g. an unreadable enabled setting).
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ExtensionJob] | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Create the consumer task on the running event loop.

        Raises:
            RuntimeError: The lane is already running.
        """
        if self._task is not None:
            raise RuntimeError("the extension lane is already running")
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._consume(self._queue), name="extension-lane")

    def submit(self, job: ExtensionJob) -> None:
        """Queue a job after every job already queued; the caller never awaits it.

        Args:
            job: A zero-argument async callable.

        Raises:
            RuntimeError: The lane is not running (not started yet, or
                stopped), so the job would never run.
        """
        if self._queue is None:
            raise RuntimeError("the extension lane is not running")
        self._queue.put_nowait(job)

    async def stop(self) -> None:
        """Drop the queued jobs, cancel the consumer and wait for it to exit.

        A job whose hook is already running in a thread (``asyncio.to_thread``
        cannot be interrupted) finishes there on its own; an extension orders
        its own ``shutdown()`` after such a hook. Dropped jobs are logged at
        info: the next worker boot reconciles anyway. Stopping a lane that is
        not running does nothing.
        """
        task, queue = self._task, self._queue
        self._task = None
        self._queue = None
        if task is None or queue is None:
            return
        dropped = queue.qsize()
        if dropped:
            logger.info("Extension lane stopped; %d queued job(s) dropped", dropped)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    @staticmethod
    async def _consume(queue: asyncio.Queue[ExtensionJob]) -> None:
        while True:
            job = await queue.get()
            try:
                await job()
            except Exception:
                # One failed job must not stop the jobs filed after it.
                logger.exception("Extension lane job %r failed; the next job still runs", job)


class RuntimeController:
    """Own the runtime-only services inside the worker process.

    Attributes:
        extension_lane: Runs the extension hook jobs (settings changes,
            reconciles) off the command loop. ``RuntimeWorker.run`` starts it
            once the worker has reported ready.
    """

    def __init__(self) -> None:
        self.extension_lane = ExtensionLane()

    async def boot(self, *, paused: bool) -> None:
        """Reconcile what a previous app run left, then start the services unless paused.

        Background processes end with the app (they share its process
        group), so their rows still say ``running``; one sweep records them
        as ended before anything reads them. Paused or not: the desk and the
        agents' prompts read these rows either way. The extensions' boot
        reconcile is not here: ``RuntimeWorker.run`` queues
        :meth:`reconcile_after_boot` in the extension lane after the worker
        reports ready, because stopping what an earlier worker left can take
        longer than the app waits for ready.
        """
        await asyncio.to_thread(background_processes.sweep, exit_reason=background_processes.APP_STOPPED_REASON)
        if not paused:
            self._start_services()

    async def shutdown(self) -> None:
        await self._stop_services()
        # Before the extensions shut down, so no queued hook starts on an
        # extension being released.
        await self.extension_lane.stop()
        # Closes browsers and other resources extensions hold in this process.
        await asyncio.to_thread(shutdown_loaded_extensions)

    async def pause(self) -> None:
        await self._stop_services()

    async def resume(self) -> None:
        self._start_services()

    async def wake_dispatcher(self) -> None:
        dispatcher.notify()

    async def reload_schedules(self) -> None:
        """Sync the schedule timetable with the database.

        The command comes from ``core.scheduling.service.request_reload``.
        It carries no payload and announces nothing: the change's own caller
        tells the UI.
        """
        schedule_watch.reload()

    async def extension_config_changed(self, extension_id: str, agent_id: str) -> None:
        """Apply one agent's saved or removed settings for one extension.

        The command comes from ``RuntimeServices.extension_config_changed``
        after the app saved or removed one agent's settings for an extension.
        The wake service's cached configs are dropped here, so its next tick
        re-reads them. The extension's reaction (e.g. restarting a running
        desktop with new specs) is queued in the extension lane and this
        returns at once, so the command completes without waiting for it;
        see :meth:`_apply_settings_change`.

        Args:
            extension_id: The extension whose settings changed.
            agent_id: The agent whose settings changed.

        Raises:
            RuntimeError: The extension lane is not running.
        """
        extension_wake_watch.invalidate()
        self.extension_lane.submit(functools.partial(self._apply_settings_change, extension_id, agent_id))

    async def extensions_reconcile(self) -> None:
        """Release turned-off extensions, then drop what extensions hold for deleted agents.

        The command comes from ``RuntimeServices.extensions_reconcile`` after
        an extension was turned on or off or agents were deleted. The work
        is queued in the extension lane and this returns at once; see
        :meth:`_reconcile_extensions`.

        Raises:
            RuntimeError: The extension lane is not running.
        """
        self.extension_lane.submit(self._reconcile_extensions)

    async def reconcile_after_boot(self) -> None:
        """The boot job: tell every enabled extension which agents exist.

        Queued first in the extension lane by ``RuntimeWorker.run``, paused or
        not, so what an earlier or crashed worker left (e.g. a running
        desktop) is stopped or removed (``agent_hooks.reconcile_agents``,
        in a thread); then open live views of the extensions it touched
        re-read.
        """
        await self._announce_live_views(await asyncio.to_thread(reconcile_agents))

    async def _apply_settings_change(self, extension_id: str, agent_id: str) -> None:
        """Extension-lane job: let the extension react, then nudge its live view.

        ``agent_hooks.notify_agent_config_changed`` runs in a thread; open
        live views of the extension then re-read, since the change may have
        removed an agent's live item.
        """
        changed = await asyncio.to_thread(notify_agent_config_changed, extension_id, agent_id)
        await self._announce_live_views(changed)

    async def _reconcile_extensions(self) -> None:
        """Extension-lane job: apply enable states, reconcile agents, nudge live views.

        ``agent_hooks.apply_extension_states`` then
        ``agent_hooks.reconcile_agents``, each in a thread; then open live
        views of every extension either step touched re-read.
        """
        released = await asyncio.to_thread(apply_extension_states)
        reconciled = await asyncio.to_thread(reconcile_agents)
        await self._announce_live_views(released | reconciled)

    @staticmethod
    async def _announce_live_views(extension_ids: frozenset[str]) -> None:
        """Tell open live views of these extensions to re-read their ``/live`` route.

        A worker-side change (settings applied, an extension released, a
        reconcile) can remove an agent's live item without any CLI result to
        announce it. The event carries no state, as in
        ``turn_helpers.announce_extension_result``.

        Args:
            extension_ids: Live-view extension ids an ``agent_hooks`` call
                returned; sorted so the events go out in a stable order.
        """
        for ext_id in sorted(extension_ids):
            await runtime_events.broadcast_extension_live(ext_id, None)

    async def reset_agent_runtime(self, agent_id: str) -> None:
        await dispatcher.reset_agent(agent_id)
        simulation.clear_agent_path(agent_id)

    def _start_services(self) -> None:
        dispatcher.start()
        simulation.start()
        watchdog.start()
        meeting_watchdog.start()
        channel_idle_watch.start()
        extension_wake_watch.start()
        schedule_watch.start()

    async def _stop_services(self) -> None:
        await schedule_watch.stop()
        await extension_wake_watch.stop()
        await channel_idle_watch.stop()
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
        # Set by StdinDoorbell when the app files a runtime command, and by
        # _ring_locally when code in this process files one.
        self._doorbell = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fallback_poll: _LastGoodInterval | None = None
        self._heartbeat: _LastGoodInterval | None = None
        self._background_tasks: list[asyncio.Task[None]] = []
        self._failed = False
        self._parent_pid = _read_parent_pid()

    def _install_stop_signals(self) -> None:
        loop = asyncio.get_running_loop()

        def _request_stop() -> None:
            self._stopping.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_stop)

    async def run(self) -> int:
        """Boot, report ready, then serve runtime commands until told to stop.

        Order: settings and the doorbell; ``RuntimeController.boot`` (the
        process sweep, services unless paused); ``{"type": "ready"}`` to the
        app; then the extension lane starts with the boot reconcile
        (``RuntimeController.reconcile_after_boot``) as its first job, paused
        or not; then the command, heartbeat and parent-watch loops. On the
        way out the controller shuts down (services, then the lane, then the
        extensions).

        Returns:
            0 after a requested stop, 1 when starting failed (the failure is
            logged, recorded on the worker row and sent to the app as
            ``fatal``).
        """
        runtime_events.set_sink(TransportRuntimeEventSink(self._transport))
        self._install_stop_signals()
        try:
            db.init_db()
            # Unusable here fails the start loudly; later bad edits keep the last good value.
            self._fallback_poll = _LastGoodInterval(
                "command fallback poll", fallback_poll_seconds, (RuntimeSettingError,)
            )
            self._heartbeat = _LastGoodInterval(
                "heartbeat interval", db.heartbeat_interval_seconds, (config.ConfigError,)
            )
            self._loop = asyncio.get_running_loop()
            StdinDoorbell(self._loop, self._doorbell).start()
            register_local_doorbell(self._ring_locally)
            await self._controller.boot(paused=self._is_paused())
            db.mark_runtime_worker_running(os.getpid(), worker_name=_WORKER_NAME)
            await self._transport.send_message({"type": "ready"})
            # After ready, not in boot(): stopping what an earlier worker left
            # can take longer than the app waits for ready.
            self._controller.extension_lane.start()
            self._controller.extension_lane.submit(self._controller.reconcile_after_boot)
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
            register_local_doorbell(None)
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
            # Cleared before the queue is read: a ring that lands after the
            # read sets it again, so it is never lost.
            self._doorbell.clear()
            # Commands start services that read settings; refresh first so a
            # resume after an app-side change sees it (one integer read per wake).
            config.refresh_if_changed()
            command = self._claim_next_command()
            if command is None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._doorbell.wait(), timeout=self._interval(self._fallback_poll))
                continue
            payload = json.loads(command.payload) if command.payload else {}
            try:
                await self._execute(command.command_type, payload)
            except Exception as exc:
                logger.exception("Runtime worker command failed: %s", command.command_type)
                db.fail_runtime_command(command.id, str(exc))
            else:
                db.complete_runtime_command(command.id)

    @staticmethod
    def _interval(setting: _LastGoodInterval | None) -> float:
        if setting is None:
            raise RuntimeError("worker interval read before run() set it up")
        return setting.current()

    def _ring_locally(self) -> None:
        """Wake this worker's command loop after code in this process filed a command.

        Registered with ``register_local_doorbell``; safe from any thread. A
        closed loop (the worker is exiting) is logged at WARNING: the row
        stays queued, and a starting worker clears open rows anyway.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("local doorbell rung before run() set it up")
        try:
            loop.call_soon_threadsafe(self._doorbell.set)
        except RuntimeError as exc:
            logger.warning("Runtime worker local doorbell not rung (%s)", exc)

    async def _heartbeat_loop(self) -> None:
        missing = False
        while not self._stopping.is_set():
            stamped = db.record_runtime_worker_heartbeat(pid=os.getpid(), worker_name=_WORKER_NAME)
            # A lost row reads as a dead worker to everyone else: say so once
            # per streak, not once a tick.
            if stamped is None and not missing:
                logger.warning(
                    "Runtime worker heartbeat found no state row for pid %s; readers will see it as stale",
                    os.getpid(),
                )
            elif stamped is not None and missing:
                logger.info("Runtime worker heartbeat row is back")
            missing = stamped is None
            await asyncio.sleep(self._interval(self._heartbeat))

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
        if command_type == "reload_schedules":
            await self._controller.reload_schedules()
            return
        if command_type == "extension_config_changed":
            await self._controller.extension_config_changed(payload["extension_id"], payload["agent_id"])
            return
        if command_type == "extensions_reconcile":
            await self._controller.extensions_reconcile()
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
