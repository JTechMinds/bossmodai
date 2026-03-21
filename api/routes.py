"""BossMod AI — REST API routes + WebSocket endpoint.

Agent CRUD, map data, world state, settings, and real-time
WebSocket broadcasting for live Canvas and Activity updates.
"""

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel

from api.websocket import manager
from core import config
from core.agent_loop.loop import run_turn
from core.models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    AIConnection,
    AIConnectionCreate,
    AIConnectionUpdate,
    AIPersonality,
    AIPersonalityCreate,
    AIPersonalityUpdate,
    Task,
    TaskCreate,
)
from core.world.simulation import simulation
from core.world.tilemap import get_map_data
import db

router = APIRouter(prefix="/api")


# ─── WebSocket ───

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time connection for Canvas agent positions and Activity log.

    On connect, sends the full world state and recent activity history.
    Then keeps the connection alive, forwarding broadcasts.
    """
    await manager.connect(ws)

    # Send initial world state
    world = db.get_world_state()
    await ws.send_json(jsonable_encoder({"type": "world_update", "data": world}))

    # Send recent activity history
    await ws.send_json(jsonable_encoder({"type": "activity_log", "data": manager.activity_log}))

    try:
        while True:
            # Keep connection alive; future: handle chat messages here
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ─── Map ───

@router.get("/map")
async def get_map():
    """Return the office tilemap data for the Canvas renderer."""
    return get_map_data()


# ─── World state ───

@router.get("/world")
async def get_world_state():
    """Return all agents with their positions/status for Canvas rendering."""
    return db.get_world_state()


# ─── Agents CRUD ───

@router.get("/agents")
async def list_agents() -> list[Agent]:
    return db.list_agents()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Agent:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate) -> Agent:
    agent = db.create_agent(
        name=body.name,
        role=body.role,
        color=body.color,
        desk_x=body.desk_x,
        desk_y=body.desk_y,
    )
    # Broadcast to all connected clients
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_created",
        detail=f"Agent \"{agent.name}\" created",
        agent_name=agent.name,
    )
    return agent


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate) -> Agent:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    agent = db.update_agent(agent_id, **fields)
    if not agent:
        raise HTTPException(404, "Agent not found")

    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_updated",
        detail=f"Agent \"{agent.name}\" updated",
        agent_name=agent.name,
    )
    return agent


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    # Fetch name before deleting for the activity message
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    db.delete_agent(agent_id)

    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_deleted",
        detail=f"Agent \"{agent.name}\" deleted",
        agent_name=agent.name,
    )


# ─── Tasks CRUD ───

@router.get("/tasks")
async def list_tasks(
    assigned_to: str | None = None,
    status: str | None = None,
) -> list[Task]:
    return db.list_tasks(assigned_to=assigned_to, status=status)


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate) -> Task:
    task = db.create_task(
        title=body.title,
        description=body.description,
        project=body.project,
        assigned_to=body.assigned_to,
    )
    await manager.broadcast_activity(
        event="task_created",
        detail=f"Task \"{task.title}\" created" + (f" → {body.assigned_to}" if body.assigned_to else ""),
    )
    return task


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> Task:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


# ─── Agent activation (manual trigger) ───

class ActivationBody(BaseModel):
    content: str = "You have been manually activated."


@router.post("/agents/{agent_id}/activate")
async def activate_agent(agent_id: str, body: ActivationBody | None = None):
    """Manually trigger an agent turn."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    state = db.get_agent_state(agent_id)
    if not state:
        raise HTTPException(500, "Agent state not found")

    content = body.content if body else "You have been manually activated."
    trigger = {"type": "manual", "content": content}

    result = await run_turn(agent, state, trigger)

    # If the action was walk_to, register path with simulation
    if result.get("path") and result.get("agent_id"):
        simulation.set_agent_path(result["agent_id"], result["path"])

    return {"status": "ok", "result": result}


# ─── Settings ───

@router.get("/settings")
async def get_settings(category: str | None = None):
    return db.get_settings(category)


@router.put("/settings/{key}")
async def set_setting(key: str, value: str, category: str = "general"):
    result = db.set_setting(key, value, category)
    config.reload()  # Invalidate cache so changes take effect immediately
    return result


# ─── AI Connections CRUD ───

@router.get("/connections")
async def list_connections() -> list[AIConnection]:
    return db.list_connections()


@router.get("/connections/{connection_id}")
async def get_connection(connection_id: str) -> AIConnection:
    conn = db.get_connection_by_id(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


@router.post("/connections", status_code=201)
async def create_connection(body: AIConnectionCreate) -> AIConnection:
    return db.create_connection(
        name=body.name,
        api_base_url=body.api_base_url,
        api_key=body.api_key,
        model=body.model,
    )


@router.patch("/connections/{connection_id}")
async def update_connection(connection_id: str, body: AIConnectionUpdate) -> AIConnection:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    conn = db.update_connection(connection_id, **fields)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str):
    if not db.delete_connection(connection_id):
        raise HTTPException(404, "Connection not found")


class TestConnectionBody(BaseModel):
    api_base_url: str
    api_key: str | None = None
    model: str | None = None


@router.post("/connections/test")
async def test_connection(body: TestConnectionBody):
    """Test an AI connection by hitting GET {base_url}/models.

    Verifies the host is reachable, auth works, and the response
    is OpenAI-compatible. Optionally checks the model exists.
    """
    url = body.api_base_url.rstrip("/")
    # Normalize: if URL ends with /v1, use it; otherwise append /v1
    if not url.endswith("/v1"):
        url += "/v1"
    url += "/models"

    headers = {}
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection failed — check the URL"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Connection timed out after 10s"}
    except Exception as exc:
        return {"ok": False, "error": f"Request failed: {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "error": "Authentication failed — check your API key"}
    if resp.status_code == 403:
        return {"ok": False, "error": "Access denied — API key lacks permissions"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Server returned {resp.status_code}"}

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": "Response is not valid JSON"}

    models_list = data.get("data")
    if not isinstance(models_list, list):
        return {"ok": False, "error": "Response missing 'data' array — may not be OpenAI-compatible"}

    model_ids = [m.get("id", "") for m in models_list]

    if body.model and body.model not in model_ids:
        return {
            "ok": True,
            "warning": f"Connected, but model '{body.model}' not found in {len(model_ids)} available models",
            "models": model_ids[:20],
        }

    return {
        "ok": True,
        "models_count": len(model_ids),
        "models": model_ids[:20],
    }


# ─── AI Personalities CRUD ───

@router.get("/personalities")
async def list_personalities() -> list[AIPersonality]:
    return db.list_personalities()


@router.get("/personalities/{personality_id}")
async def get_personality(personality_id: str) -> AIPersonality:
    p = db.get_personality(personality_id)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.post("/personalities", status_code=201)
async def create_personality(body: AIPersonalityCreate) -> AIPersonality:
    return db.create_personality(
        name=body.name,
        prompt_template=body.prompt_template,
    )


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: AIPersonalityUpdate) -> AIPersonality:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    p = db.update_personality(personality_id, **fields)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.delete("/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str):
    if not db.delete_personality(personality_id):
        raise HTTPException(404, "Personality not found")
