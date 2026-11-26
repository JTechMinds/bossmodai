"""WebSocket endpoint."""

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth import websocket_authorized
from api.websocket import manager
from core.runtime import runtime_services
import db

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time connection for Canvas agent positions and Activity log.

    On connect, the screen is sent four frames in order: ``world_update``
    (the full world state), ``unified_feed`` (recent activity history),
    ``runtime_state`` and ``presence_snapshot`` (who is thinking or queued
    right now). ``manager.connect`` queues them before the screen is
    registered for broadcasts, so every broadcast the screen gets arrives
    after them. Then the route keeps the connection open while the screen's own
    sender (``api/websocket.py``) delivers broadcasts, and unregisters the
    screen however the connection ends.
    """
    if not websocket_authorized(ws):
        await ws.close(code=4401)
        return

    def initial_frames() -> list[dict[str, Any]]:
        # Called inside connect, which queues these with no await before the
        # screen is registered: no broadcast can interleave with them, so the
        # presence snapshot is never overtaken by an older presence delta.
        return [
            {"type": "world_update", "data": db.get_world_state()},
            {"type": "unified_feed", "data": manager.unified_feed},
            {"type": "runtime_state", "data": runtime_services.status_payload()},
            {"type": "presence_snapshot", "data": runtime_services.presence_snapshot()},
        ]

    await manager.connect(ws, initial_frames=initial_frames)
    try:
        while True:
            # Keep connection alive; future: handle chat messages here
            await ws.receive_text()
    except WebSocketDisconnect:
        # How a screen leaves: by design, not a failure.
        pass
    finally:
        # Every way out, including an unexpected error, which propagates after this.
        await manager.disconnect(ws)
