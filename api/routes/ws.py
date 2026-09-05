"""WebSocket endpoint."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from api.auth import websocket_authorized
from api.websocket import manager
from core.runtime import runtime_services
import db

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time connection for Canvas agent positions and Activity log.

    On connect, sends the full world state and recent activity history.
    Then keeps the connection alive, forwarding broadcasts.
    """
    if not websocket_authorized(ws):
        await ws.close(code=4401)
        return

    await manager.connect(ws)

    # Send initial world state
    world = db.get_world_state()
    await ws.send_json(jsonable_encoder({"type": "world_update", "data": world}))

    # Send unified activity feed
    await ws.send_json(jsonable_encoder({"type": "unified_feed", "data": manager.unified_feed}))
    await ws.send_json(jsonable_encoder({"type": "runtime_state", "data": runtime_services.status_payload()}))

    try:
        while True:
            # Keep connection alive; future: handle chat messages here
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
