"""BossMod AI — WebSocket connection manager and event broadcasting.

Manages active WebSocket connections, broadcasts world state updates
and activity events to all connected clients in real-time.
Activity events are persisted to the database for history across restarts.

Each screen (one browser WebSocket) has its own bounded outbox and its own
sender task, so a slow or stalled screen never holds up a broadcast, the
other screens, or the runtime-event reader that awaits ``broadcast``. A
screen that falls behind, stops taking frames, or cannot be sent to is
dropped and closed; its browser reconnects and resyncs
(``ui/static/js/shell/socket.js``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from core import config

import db

logger = logging.getLogger(__name__)

# WebSocket close codes (RFC 6455 and the IANA registry). The browser
# reconnects after any close and then resyncs (ui/static/js/shell/socket.js).
# 1013 "Try Again Later": the screen fell behind or could not be sent to.
_CLOSE_TRY_AGAIN_LATER = 1013
# 1011 "Internal Error": the server could not set the screen up on connect.
_CLOSE_INTERNAL_ERROR = 1011


def _log_task_failure(task: asyncio.Task[None]) -> None:
    """Done-callback for a screen's sender or drop task: log how it failed, if it did.

    A background task has no caller to raise to, so this is where an
    unexpected failure is surfaced. A cancelled task is not a failure: its
    screen left or was dropped.

    Args:
        task: The finished task.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("WebSocket task %s failed", task.get_name(), exc_info=exc)


async def _close_socket(websocket: WebSocket, code: int) -> None:
    """Close one screen's socket with ``code``, waiting at most ``ws_send_timeout_seconds``.

    Best effort, and never raises for the socket's own state:

    - the socket is already gone (``WebSocketDisconnect``, or ``RuntimeError``
      for one already closed): logged at DEBUG. This is the one place a
      failure is swallowed by design: there is nothing left to close;
    - the close does not finish within the bound: logged at INFO. A stalled
      screen may never answer the closing handshake, and nothing else is
      sent to it either way;
    - the bound itself cannot be read (``ConfigError``): logged at ERROR, and
      the close still runs, bounded by the server's own closing-handshake
      timeout instead. Skipping it would leave the browser connected and
      silently stale.

    Args:
        websocket: The accepted socket.
        code: The close code the browser receives.
    """
    bound: float | None
    try:
        bound = config.require_float("ws_send_timeout_seconds")
    except config.ConfigError:
        logger.exception("Closing a screen without its ws_send_timeout_seconds bound")
        bound = None
    try:
        await asyncio.wait_for(websocket.close(code=code), timeout=bound)
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.debug("Closing a screen that was already gone failed: %r", exc)
    except asyncio.TimeoutError:
        logger.info("A screen did not finish closing within %ss; it is sent nothing more", bound)


class _ScreenOutbox:
    """One screen's outbox: a bounded FIFO of frames and the one task that sends them.

    The sender is the only code that sends on a registered socket, so the
    screen gets its frames in the order they were offered, one at a time.
    The sender asks ``drop`` to drop the screen when it is gone, too slow, or
    cannot be sent to.

    Attributes:
        websocket: The accepted socket.
        queue: Frames waiting to be sent, at most ``capacity``.
        sender: The task running :meth:`_send_loop`, once :meth:`start` ran.
        closed: Whether a close has begun; a screen is closed at most once.

    Args:
        websocket: The accepted socket.
        capacity: The most frames that may wait (``ws_client_outbox_size``).
        drop: ``ConnectionManager._drop``: ``drop(outbox, code)`` unregisters
            the screen and closes it with ``code``, or only unregisters it
            when ``code`` is ``None`` (the screen is already gone).
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        capacity: int,
        drop: Callable[[_ScreenOutbox, int | None], Awaitable[None]],
    ) -> None:
        self.websocket = websocket
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=capacity)
        self.sender: asyncio.Task[None] | None = None
        self.closed = False
        self._drop = drop

    def offer(self, frame: Any) -> bool:
        """Queue one encoded frame without waiting.

        Args:
            frame: A ``jsonable_encoder`` result, shared by every screen and
                never mutated.

        Returns:
            ``False`` when the outbox is full: the screen fell behind.
        """
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        return True

    def start(self) -> None:
        """Start the sender. Synchronous, so a caller can queue frames and start it with no await between."""
        self.sender = asyncio.create_task(self._send_loop(), name="websocket-screen-sender")
        self.sender.add_done_callback(_log_task_failure)

    async def stop(self) -> None:
        """Cancel the sender and wait until it has ended.

        A no-op when the sender never started or when called from the
        sender itself (it is the one dropping its screen). The sender's own
        cancellation is not raised here; a cancellation of the caller still is.
        """
        sender = self.sender
        if sender is None or sender is asyncio.current_task():
            return
        sender.cancel()
        # wait(), not `await sender`: that would re-raise the sender's own
        # CancelledError in the caller. Its failure, if any, is logged by
        # _log_task_failure.
        await asyncio.wait([sender])

    async def close(self, code: int) -> None:
        """Close the socket with ``code`` once, bounded by ``ws_send_timeout_seconds``.

        Best effort (see ``_close_socket``): a socket already gone, or one
        that does not finish closing in time, is logged, never raised.

        Args:
            code: The close code the browser receives.
        """
        if self.closed:
            return
        self.closed = True
        await _close_socket(self.websocket, code)

    async def _send_loop(self) -> None:
        """Send queued frames in order until the screen is dropped or the task is cancelled.

        ``ws_send_timeout_seconds`` is read for every frame, so an edit to it
        applies at once. Each failure ends the loop and drops the screen:

        - ``WebSocketDisconnect`` (Starlette converts an ``OSError`` mid-send)
          or ``RuntimeError`` (already closed): the screen is gone; logged at
          DEBUG and only unregistered;
        - ``asyncio.TimeoutError``: the screen did not take the frame in
          time; logged at INFO and closed with 1013;
        - ``ConfigError``: the timeout cannot be read; logged at ERROR and
          closed with 1013. The browser reconnects and meets the same error
          again, so it stays loud.
        """
        while True:
            frame = await self.queue.get()
            try:
                timeout = config.require_float("ws_send_timeout_seconds")
                await asyncio.wait_for(self.websocket.send_json(frame), timeout=timeout)
            except WebSocketDisconnect as exc:
                logger.debug("A screen disconnected mid-send (code %s); dropping it", exc.code)
                await self._drop(self, None)
                return
            except RuntimeError as exc:
                logger.debug("A screen was already closed (%s); dropping it", exc)
                await self._drop(self, None)
                return
            except asyncio.TimeoutError:
                logger.info(
                    "A screen did not take an update within %ss; dropping it so it reconnects and catches up",
                    timeout,
                )
                await self._drop(self, _CLOSE_TRY_AGAIN_LATER)
                return
            except config.ConfigError:
                logger.exception("Cannot send to a screen; dropping it so it reconnects")
                await self._drop(self, _CLOSE_TRY_AGAIN_LATER)
                return


class ConnectionManager:
    """Tracks the connected screens and fans every broadcast out to their outboxes.

    ``broadcast`` only queues: each screen's own sender delivers its frames
    in order, so no screen's pace reaches a broadcaster.
    """

    def __init__(self) -> None:
        self._connections: dict[WebSocket, _ScreenOutbox] = {}
        # Drops scheduled by broadcast() for screens that fell behind. Held
        # here because the event loop keeps only weak references to tasks.
        self._drop_tasks: set[asyncio.Task[None]] = set()
        self._max_log_size = 200
        # World-state coalescing (see broadcast_world_state): the one pending
        # send task, and whether a call landed that it has not yet served.
        self._world_task: asyncio.Task[None] | None = None
        self._world_requested = False

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def unified_feed(self) -> dict[str, Any]:
        """Load initial unified feed from the database."""
        return db.get_unified_feed(limit=50)

    async def connect(
        self,
        websocket: WebSocket,
        initial_frames: Callable[[], list[dict[str, Any]]],
    ) -> None:
        """Accept a screen, queue its initial frames first, then register it for broadcasts.

        After ``accept()`` nothing awaits until the screen is registered: the
        frames are built, encoded and queued, the sender is started, and the
        screen is registered in one synchronous run. So no broadcast can land
        before or between the initial frames, and the snapshot they carry
        cannot be overtaken by an older delta.

        Args:
            websocket: The screen's socket, not yet accepted.
            initial_frames: Builds the frames the screen gets first, in order
                (e.g. ``{"type": "world_update", "data": ...}``).

        Raises:
            ConfigError: ``ws_client_outbox_size`` is missing, not a positive
                integer, or smaller than the number of initial frames.
            Exception: Whatever ``initial_frames`` raises. In every case the
                socket is closed with 1011 first, and the screen is never
                registered.
        """
        await websocket.accept()
        try:
            capacity = config.require_positive_int("ws_client_outbox_size")
            frames = [jsonable_encoder(frame) for frame in initial_frames()]
            if len(frames) > capacity:
                raise config.ConfigError(
                    f"Setting 'ws_client_outbox_size' ({capacity}) cannot hold the "
                    f"{len(frames)} frames a screen is sent when it connects"
                )
        except Exception:
            # Accepted but never registered: closed, so its browser is not
            # left connected and silent. Re-raised for the route to surface.
            await _close_socket(websocket, _CLOSE_INTERNAL_ERROR)
            raise
        outbox = _ScreenOutbox(websocket, capacity=capacity, drop=self._drop)
        for frame in frames:
            # Always queued: the capacity check above leaves room for every one.
            outbox.offer(frame)
        outbox.start()
        self._connections[websocket] = outbox
        logger.info("WebSocket connected (%d active)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a screen that left, and stop its sender.

        Idempotent: a screen already dropped or disconnected is not
        registered, and nothing more happens. Does not close the socket: the
        screen left, or its route is ending.

        Args:
            websocket: The screen's socket.
        """
        outbox = self._connections.pop(websocket, None)
        if outbox is not None:
            await outbox.stop()
        logger.info("WebSocket disconnected (%d active)", len(self._connections))

    async def _drop(self, outbox: _ScreenOutbox, code: int | None) -> None:
        """Unregister a screen, stop its sender, and close its socket with ``code``.

        Called by the screen's own sender (gone, too slow, or a settings
        error; its own task is not cancelled) and by the task ``broadcast``
        schedules for a screen that fell behind. Never inside ``broadcast``.

        Args:
            outbox: The screen to drop.
            code: The close code, or ``None`` for a screen that is already
                gone: it is only unregistered.
        """
        self._connections.pop(outbox.websocket, None)
        await outbox.stop()
        if code is not None:
            await outbox.close(code)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Queue one JSON message for every connected screen, without sending it.

        Encodes once with ``jsonable_encoder`` (datetimes, Pydantic objects),
        then offers the frame to each screen's outbox. Returns once every
        screen has it queued: delivery happens later, in order, per screen,
        by that screen's sender, so no screen's pace reaches the caller.

        A screen whose outbox is full fell ``ws_client_outbox_size`` frames
        behind. It is unregistered here, logged at WARNING, and closed with
        1013 from a task of its own, so its browser reconnects and catches up.

        Async because every caller awaits it; it never awaits a network send.

        Args:
            message: The JSON-encodable frame, e.g. ``{"type": ..., "data": ...}``.
        """
        encoded = jsonable_encoder(message)
        for websocket, outbox in list(self._connections.items()):
            if outbox.offer(encoded):
                continue
            # Unregistered now, before any await, so no later broadcast offers
            # to it again or warns about it twice.
            del self._connections[websocket]
            logger.warning(
                "Dropped a screen that fell %d updates behind; it will reconnect and catch up",
                outbox.queue.qsize(),
            )
            task = asyncio.create_task(
                self._drop(outbox, _CLOSE_TRY_AGAIN_LATER), name="websocket-screen-drop",
            )
            self._drop_tasks.add(task)
            task.add_done_callback(self._drop_tasks.discard)
            task.add_done_callback(_log_task_failure)

    async def broadcast_world_state(self) -> None:
        """Schedule one coalesced ``world_update`` to all clients.

        Every agent step, movement tick and roster mutation calls this, often
        several times within a few milliseconds. The first call schedules a
        send ``world_state_coalesce_ms`` later; calls before that send reads
        the database are absorbed into it, so one ``db.get_world_state()``
        serves the whole burst. A call that lands while a send is already
        reading or sending schedules exactly one more send after it, so the
        last change before a quiet spell is always delivered, and every send
        reflects the database as it is when the send reads it.

        Returns as soon as the send is scheduled: the broadcast has NOT
        happened yet when the await returns. The send task's ``broadcast``
        only queues the frame for each screen, so what it can fail on is the
        database read or the frame's encoding; either is logged at ERROR by
        the send task, and the next call schedules a fresh one.

        Raises:
            ConfigError: ``world_state_coalesce_ms`` is missing or not an int.
        """
        self._world_requested = True
        task = self._world_task
        # A task left behind by a closed event loop (each test gets its own)
        # can never finish, so only a live task on this loop absorbs the call.
        if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
            return
        window = config.require_int("world_state_coalesce_ms") / 1000
        self._world_task = asyncio.create_task(self._send_world_state(window))

    async def _send_world_state(self, window: float) -> None:
        """Send world updates until no call is left unserved; see ``broadcast_world_state``."""
        try:
            while True:
                await asyncio.sleep(window)
                # Cleared before the read: a call from here on asks for a
                # newer snapshot than this one and loops once more.
                self._world_requested = False
                # Off the event loop: the app's runtime-event reader awaits
                # this path and must not stall behind a roster query.
                world = await asyncio.to_thread(db.get_world_state)
                await self.broadcast({"type": "world_update", "data": world})
                if not self._world_requested:
                    return
        except Exception:
            # A background task has no caller to raise to; this is the
            # boundary where the failure is surfaced.
            logger.exception("World state broadcast failed")
        finally:
            if self._world_task is asyncio.current_task():
                self._world_task = None

    async def broadcast_runtime_state(self, payload: dict[str, Any]) -> None:
        """Broadcast the current global runtime state to all clients."""
        await self.broadcast({"type": "runtime_state", "data": payload})

    async def broadcast_chat_message(
        self,
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
        attachments: list[dict[str, Any]] | None = None,
        memory_id: int | None = None,
    ) -> None:
        """Broadcast a chat message to all connected clients.

        Does NOT persist to activity log — chat messages live in the messages table.
        The ``agent_id`` tells the frontend which agent's chat panel this belongs to.
        ``memory_id`` is set on an agent's memory note (add / replace): the
        number the line opens in the desk's Memory layer.
        """
        await self.broadcast({
            "type": "chat_message",
            "data": {
                "agent_id": agent_id,
                "content": content,
                "from": from_type,
                "from_name": from_name,
                "message_type": message_type,
                "message_id": message_id,
                "created_at": created_at,
                "notification_kind": notification_kind,
                "desk_path": desk_path,
                "host_path_consent": host_path_consent,
                "cli_approval": cli_approval,
                "task_id": task_id,
                "memory_id": memory_id,
                "attachments": attachments,
            },
        })

    async def broadcast_chat_reset(self, agent_id: str) -> None:
        """Broadcast that an agent's chat history was cleared."""
        await self.broadcast({
            "type": "chat_reset",
            "data": {"agent_id": agent_id},
        })

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
    ) -> None:
        """Broadcast one shared meeting transcript message."""
        await self.broadcast({
            "type": "meeting_message",
            "data": {
                "agent_id": agent_id,
                "session_id": session_id,
                "content": content,
                "author_type": author_type,
                "author_name": author_name,
                "message_id": message_id,
                "created_at": created_at,
            },
        })

    async def broadcast_peer_message(
        self,
        *,
        message_id: str,
        from_agent_id: str,
        to_agent_id: str,
        content: str,
        message_type: str,
        created_at: str,
        floor_id: str,
    ) -> None:
        """Broadcast one new agent-to-agent message for the Office chatter panel.

        The keywords are exactly the row built by
        ``core.agent_loop.message_delivery.peer_message_event``, which
        ``GET /api/office/chatter`` also returns, so a live row and a loaded
        row have one shape. Clients keep only rows for the floor they show.

        Args:
            message_id: The persisted ``messages.id``.
            from_agent_id: The sending agent.
            to_agent_id: The receiving agent.
            content: The message body (untrusted markdown).
            message_type: The persisted message type.
            created_at: ISO 8601 send time.
            floor_id: The floor the conversation happened on.
        """
        await self.broadcast({
            "type": "peer_message",
            "data": {
                "message_id": message_id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "content": content,
                "message_type": message_type,
                "created_at": created_at,
                "floor_id": floor_id,
            },
        })

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
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Broadcast one shared channel transcript message."""
        if db.is_channel_archived(channel_id):
            return
        await self.broadcast({
            "type": "channel_message",
            "data": {
                "channel_id": channel_id,
                "content": content,
                "author_type": author_type,
                "author_name": author_name,
                "author_agent_id": author_agent_id,
                "message_id": message_id,
                "created_at": created_at,
                "notification_kind": notification_kind,
                "host_path_consent": host_path_consent,
                "cli_approval": cli_approval,
                "desk_path": desk_path,
                "task_id": task_id,
                "attachments": attachments,
            },
        })

    async def broadcast_agent_presence(
        self,
        *,
        agent_id: str,
        agent_name: str,
        phase: str,
        ahead: int | None = None,
        channel_id: str | None = None,
        since: str | None = None,
    ) -> None:
        """Broadcast one agent's presence delta: thinking, queued, or idle.

        The one presence event. Each client applies it to the page's presence
        rows (``ui/static/js/core/presence.js``), keyed by agent and channel,
        and every surface selects its own rows from those. Keys left unset are
        omitted from the frame.

        Args:
            agent_id: The agent the turn belongs to.
            agent_name: Its display name.
            phase: ``thinking``, ``queued`` or ``idle``.
            ahead: How many turns wait ahead of a queued one.
            channel_id: The thread the turn is for; unset for a DM or desk turn.
            since: ISO-8601 start of a thinking turn, which the transcript
                counts elapsed time from.
        """
        data: dict[str, Any] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "phase": phase,
        }
        if ahead is not None:
            data["ahead"] = ahead
        if channel_id:
            data["channel_id"] = channel_id
        if since is not None:
            data["since"] = since
        await self.broadcast({
            "type": "agent_presence",
            "data": data,
        })

    async def broadcast_presence_snapshot(self, rows: list[dict[str, Any]]) -> None:
        """Broadcast the whole presence view; each client replaces its rows with it.

        Sent with an empty list when the runtime worker exits, since every
        turn died with it. A connecting client gets the same frame from
        ``api/routes/ws.py``.

        Args:
            rows: ``RuntimeServices.presence_snapshot()`` rows:
                ``{agent_id, agent_name, channel_id, phase, ahead, since}``.
        """
        await self.broadcast({"type": "presence_snapshot", "data": rows})

    async def broadcast_channel_updated(self, channel: dict[str, Any]) -> None:
        """Broadcast a channel summary update to all connected clients."""
        await self.broadcast({"type": "channel_updated", "data": channel})

    async def broadcast_channel_reset(self, channel_id: str) -> None:
        """Broadcast that a thread's transcript was rewound, so open views reload it."""
        await self.broadcast({
            "type": "channel_reset",
            "data": {"channel_id": channel_id},
        })

    async def broadcast_floors_updated(self, floors: list[dict[str, Any]]) -> None:
        """Broadcast the full floor list after a floor was created, renamed, or deleted.

        Every open window's header switcher replaces its list with this one, so
        a change made in one window reaches the rest without a reload. The
        whole list is sent rather than a diff: it is a handful of rows, and a
        client that missed an earlier event is corrected by the next one.

        Args:
            floors: Every floor, JSON-ready (``Floor.model_dump(mode="json")``).
                Never empty — Lobby always exists.
        """
        await self.broadcast({"type": "floors_updated", "data": floors})

    async def broadcast_operator_invalidate(self, surfaces: list[str]) -> None:
        """Tell every open operator surface to repaint what it owns.

        HTTP mutations that do not carry a WebSocket payload of their own —
        settings rows, connection strings — still have to reach the takeover
        the operator is staring at without a tab poke.
        """
        cleaned = [str(name).strip() for name in surfaces if str(name).strip()]
        if not cleaned:
            return
        await self.broadcast({"type": "operator_invalidate", "data": {"surfaces": cleaned}})

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        """Broadcast a diagnostic summary to all connected clients."""
        await self.broadcast({"type": "diagnostic", "data": summary})

    async def broadcast_thought(self, agent_id: str, thought: str, action_name: str) -> None:
        """Broadcast an agent's thought to display as a speech bubble on canvas."""
        await self.broadcast({
            "type": "agent_thought",
            "data": {"agent_id": agent_id, "thought": thought, "action_name": action_name},
        })

    async def broadcast_activity(
        self,
        event: str,
        detail: str,
        agent_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Persist an activity event to the database and broadcast to all clients.

        The broadcast payload carries the full unified feed shape so the
        frontend can render it without a separate REST call.
        """
        row = db.create_activity_log_entry(event=event, detail=detail, agent_name=agent_name)
        entry = db.normalize_activity_log_entry(row)

        # Preserve extra fields (e.g. path/agent_id for canvas movement)
        if extra:
            entry.update(extra)

        await self.broadcast({"type": "activity", "data": entry})

    async def broadcast_feed_update(self, entry: dict[str, Any]) -> None:
        """Broadcast a unified feed entry for an activity or notification update."""
        await self.broadcast({"type": "activity_update", "data": entry})

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        """Nudge clients to re-read an extension's live-view state.

        Carries no state, only which extension and agent may have changed, so
        ``GET /api/extensions/{id}/live`` stays the one source of truth.

        Args:
            extension_id: The extension whose live state may have changed, or
                ``None`` for "any extension" (the runtime worker started or
                exited).
            agent_id: The agent whose command caused it, or ``None`` when the
                change is not one agent's (a toggle, setup, worker start/exit).
        """
        await self.broadcast({
            "type": "extension_live",
            "data": {"extension_id": extension_id, "agent_id": agent_id},
        })

    async def broadcast_conversation_notes(self, scope_kind: str, scope_key: str) -> None:
        """Nudge open transcripts to refetch one conversation's window.

        Carries no notes, only which conversation changed, so
        ``GET /api/conversations/{scope_kind}/{scope_key}/window`` stays the
        one source of truth for what the agent sees.

        Args:
            scope_kind: ``human_dm``, ``agent_dm`` or ``channel``.
            scope_key: The agent id, the ``"<lower id>:<higher id>"`` pair, or
                the channel id.
        """
        await self.broadcast({
            "type": "conversation_notes_updated",
            "data": {"scope_kind": scope_kind, "scope_key": scope_key},
        })

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        """Broadcast one task step as it starts or as it ends.

        Carries the whole row, in the shape ``GET /api/tasks/{id}/steps``
        returns (``core.agent_loop.task_steps.task_step_event``), so an open
        task detail upserts it by id. Persists nothing: the runtime wrote the
        row before it sent this.

        Args:
            step: The step row; ``ended_at`` is ``None`` while it is open.
        """
        await self.broadcast({"type": "task_step", "data": step})


# Module-level singleton — imported by api.routes and wired into RuntimeServices in main.py
manager = ConnectionManager()
