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

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def activity_log(self) -> list[dict[str, Any]]:
        """Load recent activity from the database."""
        rows = db.get_recent_activity_log_entries(self._max_log_size)
        return [
            {
                "event": r["event"],
                "detail": r["detail"],
                "agent_name": r.get("agent_name"),
                "timestamp": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]

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
        for ws in self._connections:
            try:
                await asyncio.wait_for(ws.send_json(encoded), timeout=timeout)
            except (ConnectionError, RuntimeError, asyncio.TimeoutError) as exc:
                logger.debug("WebSocket send failed, marking dead: %s", exc)
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)

    async def broadcast_world_state(self) -> None:
        """Fetch current world state from DB and broadcast to all clients."""
        world = db.get_world_state()
        await self.broadcast({"type": "world_update", "data": world})

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
            },
        })

    async def broadcast_chat_reset(self, agent_id: str) -> None:
        """Broadcast that an agent's chat history was cleared."""
        await self.broadcast({
            "type": "chat_reset",
            "data": {"agent_id": agent_id},
        })

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
        """Persist an activity event to the database and broadcast to all clients."""
        db.create_activity_log_entry(event=event, detail=detail, agent_name=agent_name)

        entry: dict[str, Any] = {
            "event": event,
            "detail": detail,
            "agent_name": agent_name,
        }
        if extra:
            entry.update(extra)

        await self.broadcast({"type": "activity", "data": entry})


# Module-level singleton — imported by routes.py
manager = ConnectionManager()
