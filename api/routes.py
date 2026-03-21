"""BossMod AI — REST API routes + WebSocket endpoint.

Agent CRUD, map data, world state, settings, and real-time
WebSocket broadcasting for live Canvas and Activity updates.
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel

from api.websocket import manager
from core import config
from core.agent_loop.loop import run_turn
from core.models import Agent, AgentCreate, AgentUpdate, Task, TaskCreate
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
