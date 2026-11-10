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

    On connect, sends four frames in order: ``world_update`` (the full world
    state), ``unified_feed`` (recent activity history), ``runtime_state`` and
    ``presence_snapshot`` (who is thinking or queued right now). Then keeps
    the connection alive, forwarding broadcasts.
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
    # Read and handed to send_json in one statement, with no await between.
    # Every agent_presence delta is an idempotent set or delete, so however
    # this interleaves with a concurrent broadcast, the client converges.
    await ws.send_json(jsonable_encoder({"type": "presence_snapshot", "data": runtime_services.presence_snapshot()}))

    try:
        while True:
            # Keep connection alive; future: handle chat messages here
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
