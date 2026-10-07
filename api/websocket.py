"""BossMod AI — WebSocket connection manager and event broadcasting.

Manages active WebSocket connections, broadcasts world state updates
and activity events to all connected clients in real-time.
Activity events are persisted to the database for history across restarts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

from core import config

import db

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
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

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket connected (%d active)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket disconnected (%d active)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients.

        Uses ``jsonable_encoder`` to handle datetime and Pydantic objects.
        Automatically removes dead connections. Times out slow clients.
        """
        timeout = config.get_float("ws_send_timeout_seconds") or 5.0
        encoded = jsonable_encoder(message)
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await asyncio.wait_for(ws.send_json(encoded), timeout=timeout)
            except (ConnectionError, RuntimeError, asyncio.TimeoutError) as exc:
                logger.debug("WebSocket send failed, marking dead: %s", exc)
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)

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
        happened yet when the await returns. A failed read or send is logged
        at ERROR by the send task; the next call schedules a fresh one.

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
    ) -> None:
        """Broadcast a chat message to all connected clients.

        Does NOT persist to activity log — chat messages live in the messages table.
        The ``agent_id`` tells the frontend which agent's chat panel this belongs to.
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

    async def broadcast_channel_presence(
        self,
        *,
        channel_id: str,
        agent_id: str,
        agent_name: str,
        phase: str,
        ahead: int | None = None,
    ) -> None:
        """Broadcast one member's in-flight thinking/working presence."""
        if db.is_channel_archived(channel_id):
            return
        data: dict[str, Any] = {
            "channel_id": channel_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "phase": phase,
        }
        if ahead is not None:
            data["ahead"] = ahead
        await self.broadcast({
            "type": "channel_presence",
            "data": data,
        })

    async def broadcast_agent_presence(
        self,
        *,
        agent_id: str,
        agent_name: str,
        phase: str,
        ahead: int | None = None,
        channel_id: str | None = None,
    ) -> None:
        """Broadcast desk presence for one agent: thinking, queued, or idle."""
        data: dict[str, Any] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "phase": phase,
        }
        if ahead is not None:
            data["ahead"] = ahead
        if channel_id:
            data["channel_id"] = channel_id
        await self.broadcast({
            "type": "agent_presence",
            "data": data,
        })

    async def broadcast_channel_updated(self, channel: dict[str, Any]) -> None:
        """Broadcast a channel summary update to all connected clients."""
        await self.broadcast({"type": "channel_updated", "data": channel})

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


# Module-level singleton — imported by api.routes and wired into RuntimeServices in main.py
manager = ConnectionManager()
