"""BossMod AI — REST API routes + WebSocket endpoint.

Agent CRUD, map data, world state, settings, and real-time
WebSocket broadcasting for live Canvas and Activity updates.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel

from api.websocket import manager
from core import config
from core.agent_loop.action_contract import render_action_contract
from core.agent_loop.decision_contract import render_decision_contract
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.dispatcher import dispatcher
from core.runtime import runtime_services
from core.models.message import HUMAN_SENDER_ID
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
from core.world.tilemap import get_map_data
from core.world.simulation import simulation
import db

logger = logging.getLogger(__name__)

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


@router.get("/agents/{agent_id}/api-key")
async def get_agent_api_key(agent_id: str):
    """Return the agent's API key (for the agent editor only)."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return {"api_key": agent.api_key}


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate) -> Agent:
    agent = db.create_agent(
        name=body.name,
        role=body.role,
        prompt_template=body.prompt_template,
        color=body.color,
        desk_x=body.desk_x,
        desk_y=body.desk_y,
        model_social=body.model_social,
        model_work=body.model_work,
        model_reasoning=body.model_reasoning,
        model_extraction=body.model_extraction,
        model_self_queue=body.model_self_queue,
        api_base_url=body.api_base_url,
        api_key=body.api_key,
        extra_body=body.extra_body,
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


# ─── Agent messages ───

@router.get("/agents/{agent_id}/messages")
async def get_agent_messages(agent_id: str, limit: int = 50):
    """Return formatted chat history for an agent, ready for frontend rendering."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    max_limit = config.get_int("api_message_limit_max") or 200
    limit = min(limit, max_limit)
    thread = db.get_human_chat_thread(agent_id, limit=limit)
    formatted = db.get_formatted_messages(thread, human_label="You")

    # Add from_type classification for the frontend
    result = []
    for msg in formatted:
        if msg["message_type"] == "system":
            from_type = "system"
        elif msg["from_agent"] == HUMAN_SENDER_ID:
            from_type = "human"
        elif msg["from_agent"] == agent_id:
            from_type = "agent"
        else:
            from_type = "other"

        result.append({
            "id": msg["id"],
            "content": msg["content"],
            "from": from_type,
            "from_name": msg["from_name"],
            "message_type": msg["message_type"],
            "created_at": msg["created_at"],
        })

    return result


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
    if body.assigned_to:
        dispatcher.enqueue_trigger(**build_task_assigned_trigger(task))
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
    """Queue a human chat trigger for an agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    content = body.content if body else "You have been manually activated."

    # Persist the human message
    human_msg = db.create_message(
        from_agent=HUMAN_SENDER_ID,
        to_agent=agent_id,
        content=content,
        message_type="human",
    )

    # Broadcast human message to all connected clients
    await manager.broadcast_chat_message(
        agent_id=agent_id,
        content=content,
        from_type="human",
        from_name="You",
        message_type="human",
        message_id=human_msg.id,
        created_at=human_msg.created_at,
    )

    dispatcher.enqueue_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={
            "content": content,
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    return {"status": "ok", "message": "Message queued"}


@router.delete("/agents/{agent_id}/chat-history")
async def clear_agent_chat_history(agent_id: str):
    """Delete the direct human <-> agent chat thread."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    deleted = db.delete_human_chat_thread(agent_id)
    await manager.broadcast_chat_reset(agent_id)
    await manager.broadcast_activity(
        event="chat_history_cleared",
        detail=f'Chat history cleared for "{agent.name}"',
        agent_name=agent.name,
    )
    return {"status": "ok", "deleted_messages": deleted}


@router.post("/agents/{agent_id}/reset-runtime")
async def reset_agent_runtime(agent_id: str):
    """Force-reset an agent's active runtime state and open trigger queue."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    state = db.get_agent_state(agent_id)
    if not state:
        raise HTTPException(500, "Agent state not found")

    await dispatcher.reset_agent(agent_id)
    simulation.clear_agent_path(agent_id)

    blocked_task_id = None
    open_activities = [
        activity
        for activity in db.list_activities(agent_id=agent_id, limit=100)
        if activity.status in {"active", "paused"} and activity.task_id
    ]
    if open_activities:
        task = db.get_task(open_activities[0].task_id)
        if task and task.status in ("pending", "accepted", "active"):
            db.update_task(
                task.id,
                status="blocked",
                status_note="Runtime reset by human operator.",
                watchdog_pinged_at=None,
            )
            blocked_task_id = task.id

    deleted_triggers = db.delete_open_triggers(agent_id)
    cancelled_activities = db.cancel_open_activities(agent_id, detail="Runtime reset by human operator.")
    db.update_agent_state(agent_id, status="idle")
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_runtime_reset",
        detail=f'Runtime reset for "{agent.name}"',
        agent_name=agent.name,
        extra={
            "deleted_triggers": deleted_triggers,
            "blocked_task_id": blocked_task_id,
            "cancelled_activities": cancelled_activities,
        },
    )

    return {
        "status": "ok",
        "deleted_triggers": deleted_triggers,
        "blocked_task_id": blocked_task_id,
        "cancelled_activities": cancelled_activities,
    }


# ─── Diagnostics ───

@router.get("/diagnostics")
async def list_diagnostics(agent_id: str | None = None, limit: int = 50):
    """Return recent diagnostic summaries (no context/response blobs)."""
    max_limit = config.get_int("api_diagnostics_limit_max") or 200
    limit = min(limit, max_limit)
    return db.get_diagnostics(agent_id=agent_id, limit=limit)


@router.get("/diagnostics/{diagnostic_id}")
async def get_diagnostic_detail(diagnostic_id: str):
    """Return a single diagnostic entry with full detail."""
    entry = db.get_diagnostic(diagnostic_id)
    if not entry:
        raise HTTPException(404, "Diagnostic entry not found")
    return entry


# ─── Settings ───

@router.get("/settings")
async def get_settings(category: str | None = None):
    return db.get_settings(category)


@router.get("/runtime/contracts")
async def get_runtime_contracts():
    return {
        "decision": render_decision_contract(),
        "execution": render_action_contract(),
    }


@router.post("/settings/reseed")
async def reseed_settings():
    """Force all seed settings back to their defaults."""
    db.force_reseed()
    config.reload()
    return {"status": "ok", "detail": "All seed settings reset to defaults"}


@router.post("/settings/reseed-application")
async def reseed_application():
    """Recreate the brand-new application database from the current schema."""
    await runtime_services.reseed_application_data()
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="application_reseeded",
        detail="Application data reseeded from the current schema defaults",
    )
    return {"status": "ok", "detail": "Application database recreated from current schema defaults"}


@router.put("/settings/{key}")
async def set_setting(key: str, value: str, category: str = "general"):
    try:
        result = db.set_setting(key, value, category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        extra_body=body.extra_body,
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
    base = body.api_base_url.rstrip("/")
    if base.endswith("/chat/completions") or base.endswith("/completions"):
        return {
            "ok": False,
            "error": "Use the API base URL, not a completions endpoint. Example: https://host/v1",
        }

    headers = {}
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(base + "/models", headers=headers)
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
