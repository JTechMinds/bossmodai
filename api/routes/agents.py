"""Agent CRUD, desk, chat, meetings, channels, and runtime reset."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.redaction import serialize_secret_field
from api.routes._desk import _build_agent_desk_payload
from api.routes._shared import (
    _available_folder_opener_options,
    _launch_file_explorer,
    _validate_authored_prompt_template,
)
from api.websocket import manager
from core import config
from core.agent_loop import activity_runtime
from core.bm_cli.virtual_fs import resolve_cli_path
from core.llm.template_engine import TemplateError
from core.messaging import route_human_channel_message, route_human_dm
from core.models import (
    Agent,
    AgentCreate,
    AgentPromptHistoryPolicy,
    AgentPromptHistoryPolicyUpdate,
    AgentUpdate,
)
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking.transitions import transition_task
from core.world.seating import place_agent_at_desk
from core.world.tilemap import first_unoccupied_chair, get_room_at
import db


def _auto_assign_desk(
    desk_x: int | None,
    desk_y: int | None,
    *,
    exclude_agent_id: str | None = None,
) -> tuple[int | None, int | None]:
    """Fill an empty desk assignment with the next unoccupied map chair."""
    if desk_x is not None and desk_y is not None:
        return desk_x, desk_y
    picked = first_unoccupied_chair(db.list_agents(), exclude_agent_id=exclude_agent_id)
    if picked is None:
        return None, None
    return picked


router = APIRouter()


class ActivationBody(BaseModel):
    content: str = "You have been manually activated."


class MeetingMessageBody(BaseModel):
    content: str


class ChannelCreateBody(BaseModel):
    name: str | None = None
    agent_ids: list[str]


class ChannelMessageBody(BaseModel):
    content: str


def _credentials_from_connection(connection_id: str | None) -> dict[str, Any]:
    """Resolve stored connection secrets so the UI never needs raw keys."""
    if not connection_id:
        return {}
    conn = db.get_connection_by_id(connection_id)
    if conn is None:
        raise HTTPException(400, "Connection not found")
    return {
        "api_base_url": conn.api_base_url,
        "api_key": conn.api_key,
        "extra_body": conn.extra_body,
    }


def _apply_connection_credentials(fields: dict[str, Any]) -> dict[str, Any]:
    """Fill api_base_url / api_key / extra_body from connection_id when omitted."""
    connection_id = fields.pop("connection_id", None)
    if not connection_id:
        return fields
    creds = _credentials_from_connection(connection_id)
    if not fields.get("api_base_url"):
        fields["api_base_url"] = creds["api_base_url"]
    if not fields.get("api_key"):
        fields["api_key"] = creds["api_key"]
    if fields.get("extra_body") is None:
        fields["extra_body"] = creds["extra_body"]
    return fields


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


@router.get("/company/agents")
async def list_company_agents(include: str | None = None) -> list[dict[str, object]]:
    """Return the live company roster for the Company tab.

    Pass ``?include=stats`` to merge per-agent task/token stats into each entry.
    """
    agents = [_serialize_company_agent(item) for item in db.get_world_state()]
    if include == "stats":
        stats = await asyncio.to_thread(db.get_agent_stats_batch)
        for agent in agents:
            agent_stats = stats.get(agent["id"], {})
            agent["tasks_completed"] = agent_stats.get("tasks_completed", 0)
            agent["tokens_used"] = agent_stats.get("tokens_used", 0)
            agent["current_task"] = agent_stats.get("current_task")
    return agents


@router.get("/channels")
async def list_channels() -> list[dict[str, object]]:
    """Return active shared channels with roster and latest message previews."""
    items = []
    for channel in db.list_channels():
        members = db.list_channel_member_details(channel.id)
        latest = db.get_latest_channel_message(channel.id)
        items.append(_serialize_channel_summary(channel, members=members, latest_message=latest))
    return items


@router.post("/channels")
async def create_channel(body: ChannelCreateBody):
    """Create one shared thread, or reopen the active thread with this roster."""
    member_ids = list(dict.fromkeys(agent_id for agent_id in body.agent_ids if isinstance(agent_id, str) and agent_id.strip()))
    if not member_ids:
        raise HTTPException(400, "Select at least one agent")

    agents = db.get_agents_by_ids(member_ids)
    missing = [agent_id for agent_id in member_ids if agent_id not in agents]
    if missing:
        raise HTTPException(404, f"Agents not found: {', '.join(missing)}")

    existing = db.find_active_channel_for_members(member_ids)
    if existing is not None:
        members = db.list_channel_member_details(existing.id)
        latest = db.get_latest_channel_message(existing.id)
        summary = _serialize_channel_summary(existing, members=members, latest_message=latest)
        summary["reused"] = True
        return JSONResponse(summary, status_code=200)

    if body.name and body.name.strip():
        name = body.name.strip()
    else:
        member_names = [agents[agent_id].name for agent_id in member_ids]
        if len(member_names) <= 3:
            name = ", ".join(member_names)
        else:
            name = f"{', '.join(member_names[:3])} +{len(member_names) - 3}"

    channel = db.create_channel(
        name=name,
        member_agent_ids=member_ids,
        created_by=HUMAN_SENDER_ID,
    )
    members = db.list_channel_member_details(channel.id)
    summary = _serialize_channel_summary(channel, members=members, latest_message=None)
    summary["reused"] = False
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_created",
        detail=f'Created shared thread "{channel.name}"',
        agent_name=None,
    )
    return JSONResponse(summary, status_code=201)


@router.post("/channels/{channel_id}/archive")
async def archive_channel(channel_id: str):
    """Archive one shared thread so it leaves the active Threads list."""
    return await _archive_channel(channel_id)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Archive one shared thread (soft delete)."""
    return await _archive_channel(channel_id)


async def _archive_channel(channel_id: str) -> dict[str, object]:
    channel = db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "Thread not found")
    archived = db.archive_channel(channel.id)
    if archived is None:
        raise HTTPException(404, "Thread not found")
    members = db.list_channel_member_details(archived.id)
    latest = db.get_latest_channel_message(archived.id)
    summary = _serialize_channel_summary(archived, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_archived",
        detail=f'Archived thread "{archived.name}"',
        agent_name=None,
    )
    return summary


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str, limit: int = 80):
    """Return one shared channel with roster and transcript."""
    channel = db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "Channel not found")

    messages = [
        _serialize_channel_message(item)
        for item in db.list_channel_messages(channel.id, limit=limit)
    ]
    members = db.list_channel_member_details(channel.id)
    return {
        "channel": _serialize_channel_summary(channel, members=members, latest_message=db.get_latest_channel_message(channel.id)),
        "messages": messages,
    }


@router.post("/channels/{channel_id}/messages")
async def create_channel_message(channel_id: str, body: ChannelMessageBody):
    """Append a shared human message to one channel and start a reply round."""
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        raise HTTPException(404, "Channel not found")

    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Channel message content cannot be empty")

    try:
        result = await route_human_channel_message(
            channel_id=channel.id,
            channel_name=channel.name,
            content=content,
            from_name="Human Operator",
            broadcast_manager=manager,
            services=runtime_services,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    message = result["message"]
    refreshed_channel = db.get_channel(channel.id) or channel
    await manager.broadcast_channel_updated(
        _serialize_channel_summary(refreshed_channel, members=result["members"], latest_message=message)
    )
    return {
        "status": "ok",
        "message": _serialize_channel_message(message),
        "member_count": len(result["members"]),
    }


@router.get("/agents/{agent_id}/api-key")
async def get_agent_api_key(agent_id: str):
    """Return whether an agent has an API key, plus last-4 only."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return serialize_secret_field("api_key", agent.api_key)


@router.get("/agents/{agent_id}/prompt-history-policy")
async def get_agent_prompt_history_policy(agent_id: str) -> AgentPromptHistoryPolicy:
    """Return the backend-owned prompt-history policy for one agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return db.ensure_agent_prompt_history_policy(agent_id)


@router.get("/agents/{agent_id}/desk")
async def get_agent_desk(agent_id: str, path: str = "/me"):
    """Return a browsable desk/file view rooted in the agent's bounded workspace."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    return await asyncio.to_thread(_build_agent_desk_payload, agent, path)


@router.post("/agents/{agent_id}/desk/open-folder")
async def open_agent_desk_folder(agent_id: str, path: str = "/me"):
    """Open one bounded Desk directory in the host file explorer."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    try:
        resolved = resolve_cli_path(agent.storage_key, "/", path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if resolved.real_path is None or not resolved.exists:
        raise HTTPException(404, "Path not found")

    target = resolved.real_path.parent if resolved.real_path.is_file() else resolved.real_path
    opener = config.get("desktop_open_folder_handler")
    if not opener:
        raise HTTPException(
            409,
            {
                "code": "desk_open_folder_handler_required",
                "message": "Choose a desktop folder opener before opening Desk folders.",
                "options": _available_folder_opener_options(),
            },
        )
    try:
        _launch_file_explorer(target, opener=opener)
    except OSError as exc:
        raise HTTPException(
            409,
            {
                "code": "desk_open_folder_handler_invalid",
                "message": str(exc),
                "options": _available_folder_opener_options(),
            },
        ) from exc

    return {"status": "ok", "path": str(target)}


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate) -> Agent:
    if body.prompt_template is not None:
        try:
            _validate_authored_prompt_template(body.prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    creds = _apply_connection_credentials({
        "connection_id": body.connection_id,
        "api_base_url": body.api_base_url,
        "api_key": body.api_key,
        "extra_body": body.extra_body,
    })
    desk_x, desk_y = _auto_assign_desk(body.desk_x, body.desk_y)
    agent = db.create_agent(
        name=body.name,
        role=body.role,
        description=body.description,
        done_fail_bar=body.done_fail_bar,
        prompt_template=body.prompt_template,
        color=body.color,
        desk_x=desk_x,
        desk_y=desk_y,
        model_social=body.model_social,
        model_work=body.model_work,
        model_reasoning=body.model_reasoning,
        model_extraction=body.model_extraction,
        model_self_queue=body.model_self_queue,
        api_base_url=creds.get("api_base_url"),
        api_key=creds.get("api_key"),
        extra_body=creds.get("extra_body"),
    )
    place_agent_at_desk(agent.id, agent.desk_x, agent.desk_y)
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
    fields = _apply_connection_credentials(body.model_dump(exclude_none=True))
    if not fields:
        raise HTTPException(400, "No fields to update")
    current = db.get_agent(agent_id)
    if not current:
        raise HTTPException(404, "Agent not found")
    next_desk_x = fields["desk_x"] if "desk_x" in fields else current.desk_x
    next_desk_y = fields["desk_y"] if "desk_y" in fields else current.desk_y
    assigned_x, assigned_y = _auto_assign_desk(
        next_desk_x,
        next_desk_y,
        exclude_agent_id=agent_id,
    )
    if assigned_x != next_desk_x or assigned_y != next_desk_y:
        fields["desk_x"] = assigned_x
        fields["desk_y"] = assigned_y
    prompt_template = fields.get("prompt_template")
    if isinstance(prompt_template, str):
        try:
            _validate_authored_prompt_template(prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    agent = db.update_agent(agent_id, **fields)
    if not agent:
        raise HTTPException(404, "Agent not found")
    previous_desk = (current.desk_x, current.desk_y)
    next_desk = (agent.desk_x, agent.desk_y)
    if next_desk != previous_desk:
        place_agent_at_desk(agent.id, agent.desk_x, agent.desk_y)

    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_updated",
        detail=f"Agent \"{agent.name}\" updated",
        agent_name=agent.name,
    )
    return agent


@router.patch("/agents/{agent_id}/prompt-history-policy")
async def update_agent_prompt_history_policy(
    agent_id: str,
    body: AgentPromptHistoryPolicyUpdate,
) -> AgentPromptHistoryPolicy:
    """Patch one agent's prompt-history policy."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    policy = db.update_agent_prompt_history_policy(agent_id, **fields)
    await manager.broadcast_activity(
        event="agent_prompt_history_policy_updated",
        detail=f'Prompt history policy updated for "{agent.name}"',
        agent_name=agent.name,
    )
    return policy


@router.delete("/agents", status_code=200)
async def delete_all_agents():
    """Delete every agent, their DB history, and their artifact files from disk."""
    import shutil as _shutil

    from core.bm_cli.filesystem import agents_artifact_root, ensure_artifact_roots

    agents = db.list_agents()
    for agent in agents:
        db.delete_agent(agent.id)

    # Wipe agent artifact directories from disk
    root = agents_artifact_root()
    if root.exists():
        _shutil.rmtree(root)
    ensure_artifact_roots()

    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="all_agents_deleted",
        detail=f"All {len(agents)} agent(s) deleted with artifacts",
    )
    return {"status": "ok", "deleted": len(agents)}


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
    notifications = db.list_notifications(agent_id=agent_id, limit=limit, chat_visible=True)
    notification_links = db.list_notification_links([item.id for item in notifications])
    notifications.reverse()
    formatted = db.get_formatted_messages(thread, human_label="You")
    formatted.extend(
        [
            {
                "id": item.id,
                "from_agent": "__notification__",
                "from_name": agent.name,
                "to_agent": agent_id,
                "content": item.content,
                "message_type": "system",
                "notification_kind": item.kind,
                "desk_path": (
                    notification_links[item.id].target_path
                    if item.id in notification_links and notification_links[item.id].target_kind == "desk"
                    else None
                ),
                "host_path_consent": (
                    db.get_consent_request(notification_links[item.id].target_path).as_card()
                    if item.id in notification_links
                    and notification_links[item.id].target_kind == "host_path_consent"
                    and db.get_consent_request(notification_links[item.id].target_path)
                    else None
                ),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in notifications
        ]
    )
    formatted.sort(key=lambda item: item.get("created_at") or "")
    formatted = formatted[-limit:]

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
            "notification_kind": msg.get("notification_kind"),
            "desk_path": msg.get("desk_path"),
            "host_path_consent": msg.get("host_path_consent"),
            "created_at": msg["created_at"],
        })

    return result


@router.get("/agents/{agent_id}/notifications")
async def get_agent_notifications(
    agent_id: str,
    limit: int = 50,
    chat_visible: bool | None = None,
    prompt_visible: bool | None = None,
):
    """Return stored notifications for an agent, including hidden inbox updates."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    max_limit = config.get_int("api_message_limit_max") or 200
    notifications = db.list_notifications(
        agent_id=agent_id,
        limit=min(limit, max_limit),
        chat_visible=chat_visible,
        prompt_visible=prompt_visible,
    )
    notification_links = db.list_notification_links([item.id for item in notifications])
    return [
        {
            "id": item.id,
            "agent_id": item.agent_id,
            "task_id": item.task_id,
            "activity_id": item.activity_id,
            "kind": item.kind,
            "content": item.content,
            "source_channel": item.source_channel,
            "policy": item.policy,
            "chat_visible": item.chat_visible,
            "prompt_visibility": item.prompt_visibility,
            "desk_path": (
                notification_links[item.id].target_path
                if item.id in notification_links and notification_links[item.id].target_kind == "desk"
                else None
            ),
            "host_path_consent": (
                db.get_consent_request(notification_links[item.id].target_path).as_card()
                if item.id in notification_links
                and notification_links[item.id].target_kind == "host_path_consent"
                and db.get_consent_request(notification_links[item.id].target_path)
                else None
            ),
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in notifications
    ]


def _serialize_agent_trigger(row: dict[str, Any]) -> dict[str, Any]:
    """Operator-facing trigger row. Omits internal claim-lease material."""
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"raw": payload}
    created_at = row.get("created_at")
    return {
        "id": row.get("id"),
        "agent_id": row.get("agent_id"),
        "trigger_type": row.get("trigger_type"),
        "source_channel": row.get("source_channel"),
        "payload": payload if isinstance(payload, dict) else {},
        "task_id": row.get("task_id"),
        "status": row.get("status"),
        "retry_count": row.get("retry_count"),
        "failure_reason": row.get("failure_reason"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


@router.get("/agents/{agent_id}/triggers")
async def get_agent_triggers(
    agent_id: str,
    status: str | None = None,
    limit: int = 50,
):
    """Return recent durable wake-up triggers for one agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    max_limit = config.get_int("api_message_limit_max") or 200
    rows = db.list_agent_triggers(agent_id, status=status, limit=min(max(limit, 1), max_limit))
    return [_serialize_agent_trigger(row) for row in rows]


def _meeting_room_name(room_id: str) -> str:
    """Render a user-facing meeting room label."""
    if room_id == "meeting_room":
        return "Meeting Room"
    return room_id.replace("_", " ").title()


def _serialize_meeting_session_message(item) -> dict[str, object]:
    """Serialize a meeting session message for API responses."""
    return {
        "id": item.id,
        "session_id": item.session_id,
        "author_type": item.author_type,
        "author_agent_id": item.author_agent_id,
        "author_name": item.author_name,
        "content": item.content,
        "source_channel": item.source_channel,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _serialize_channel_message(item) -> dict[str, object]:
    """Serialize one shared channel transcript message."""
    consent_card = None
    consent_id = getattr(item, "consent_id", None)
    if consent_id:
        request = db.get_consent_request(consent_id)
        if request is not None:
            consent_card = request.as_card()
    return {
        "id": item.id,
        "channel_id": item.channel_id,
        "author_type": item.author_type,
        "author_agent_id": item.author_agent_id,
        "author_name": item.author_name,
        "content": item.content,
        "source_channel": item.source_channel,
        "notification_kind": getattr(item, "notification_kind", None),
        "host_path_consent": consent_card,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _serialize_company_agent(item: dict[str, object]) -> dict[str, object]:
    """Serialize one company roster row with runtime state and location label."""
    x = int(item.get("x") or 0)
    y = int(item.get("y") or 0)
    room = get_room_at(x, y)
    location_name = room["name"] if room else "Unknown"
    idle_since_raw = item.get("idle_since")
    idle_since_iso = (
        idle_since_raw.isoformat() if hasattr(idle_since_raw, "isoformat") else idle_since_raw
    )
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "role": item.get("role"),
        "description": item.get("description"),
        "done_fail_bar": item.get("done_fail_bar"),
        "color": item.get("color"),
        "status": item.get("status") or "idle",
        "currentActivityKind": item.get("currentActivityKind"),
        "x": x,
        "y": y,
        "location": location_name,
        "idle_since": idle_since_iso,
    }


def _serialize_channel_summary(channel, *, members: list[dict[str, object]] | None = None, latest_message=None) -> dict[str, object]:
    """Serialize one shared channel summary for list and realtime updates."""
    latest = None
    if latest_message is not None:
        latest = {
            "content": latest_message.content,
            "author_name": latest_message.author_name,
            "created_at": latest_message.created_at.isoformat() if latest_message.created_at else None,
        }
    return {
        "id": channel.id,
        "name": channel.name,
        "kind": channel.kind,
        "status": channel.status,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "updated_at": channel.updated_at.isoformat() if channel.updated_at else None,
        "archived_at": channel.archived_at.isoformat() if getattr(channel, "archived_at", None) else None,
        "member_count": len(members or []),
        "members": members or [],
        "latest_message": latest,
    }


# ─── Agent activation (manual trigger) ───


@router.post("/agents/{agent_id}/activate")
async def activate_agent(agent_id: str, body: ActivationBody | None = None):
    """Route a human direct request to chat or work for an agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    content = body.content if body else "You have been manually activated."

    await route_human_dm(
        agent_id=agent_id,
        content=content,
        from_name="You",
        broadcast_manager=manager,
        services=runtime_services,
    )

    return {"status": "ok", "message": "Message queued"}


@router.get("/agents/{agent_id}/meeting-session")
async def get_agent_meeting_session(agent_id: str, limit: int = 50):
    """Return the active shared meeting session for one selected agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    session = db.get_active_meeting_session_for_agent(agent_id)
    if session is None:
        return {"active": False}

    max_limit = config.get_int("api_message_limit_max") or 200
    messages = [
        _serialize_meeting_session_message(item)
        for item in db.list_meeting_session_messages(session.id, limit=min(limit, max_limit))
    ]
    return {
        "active": True,
        "session": {
            "id": session.id,
            "title": session.title,
            "room_id": session.room_id,
            "room_name": _meeting_room_name(session.room_id),
            "participants": db.list_active_meeting_participants(session.room_id),
            "messages": messages,
        },
    }


@router.post("/agents/{agent_id}/meeting-session/messages")
async def create_agent_meeting_session_message(agent_id: str, body: MeetingMessageBody):
    """Append a shared human message to the selected agent's active meeting session."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Meeting message content cannot be empty")

    session = db.get_active_meeting_session_for_agent(agent_id)
    if session is None:
        raise HTTPException(409, "Agent is not currently in an active meeting")

    message = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="meeting",
    )

    await manager.broadcast_meeting_message(
        agent_id=None,
        session_id=session.id,
        content=message.content,
        author_type=message.author_type,
        author_name=message.author_name,
        message_id=message.id,
        created_at=message.created_at,
    )

    round_record = db.create_meeting_response_round(
        session_id=session.id,
        source_message_id=message.id,
    )
    participants = db.list_active_meeting_participants(session.room_id)
    for participant in participants:
        participant_id = participant.get("id")
        if not isinstance(participant_id, str) or not participant_id.strip():
            continue
        db.create_meeting_response_candidate(
            round_id=round_record.id,
            agent_id=participant_id,
        )
        await runtime_services.enqueue_trigger(
            agent_id=participant_id,
            trigger_type="session_message",
            source_channel="chat",
            payload={
                "content": message.content,
                "session_id": session.id,
                "round_id": round_record.id,
                "from_name": "Human Operator",
                "author_type": "human",
                "source_message_id": message.id,
                "meeting_title": session.title,
            },
        )

    return {
        "status": "ok",
        "message": _serialize_meeting_session_message(message),
        "participant_count": len(participants),
    }


@router.delete("/agents/{agent_id}/chat-history")
async def clear_agent_chat_history(agent_id: str):
    """Delete the direct human <-> agent chat thread."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    deleted = db.delete_human_chat_thread(agent_id)
    deleted_notifications = db.delete_agent_notifications(agent_id)
    await manager.broadcast_chat_reset(agent_id)
    await manager.broadcast_activity(
        event="chat_history_cleared",
        detail=f'Chat history cleared for "{agent.name}"',
        agent_name=agent.name,
    )
    return {
        "status": "ok",
        "deleted_messages": deleted,
        "deleted_notifications": deleted_notifications,
    }


@router.post("/agents/{agent_id}/reset-runtime")
async def reset_agent_runtime(agent_id: str):
    """Force-reset an agent's active runtime state and open trigger queue."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    state = db.get_agent_state(agent_id)
    if not state:
        raise HTTPException(500, "Agent state not found")

    await runtime_services.reset_agent_runtime(agent_id)

    reset_note = "Runtime reset by human operator."
    blocked_task_ids: list[str] = []
    seen_task_ids: set[str] = set()
    open_work_activities = [
        activity
        for activity in db.list_activities(agent_id=agent_id, limit=100)
        if activity.status in {"active", "paused"}
        and activity.kind == "work"
        and activity.task_id
    ]
    for activity in open_work_activities:
        task_id = activity.task_id
        if not task_id or task_id in seen_task_ids:
            continue
        task = db.get_task(task_id)
        if task and task.status in ("pending", "accepted", "active", "waiting"):
            transition_task(
                task.id,
                "blocked",
                reason=reset_note,
                actor="Human Operator",
                actor_type="human",
                status_note=reset_note,
                watchdog_pinged_at=None,
            )
            seen_task_ids.add(task.id)
            blocked_task_ids.append(task.id)

    blocked_task_id = blocked_task_ids[0] if blocked_task_ids else None

    deleted_triggers = db.delete_open_triggers(agent_id)
    cancelled_activities = db.cancel_open_activities(agent_id, detail=reset_note)
    activity_runtime.refresh_agent_status(agent_id)
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_runtime_reset",
        detail=f'Runtime reset for "{agent.name}"',
        agent_name=agent.name,
        extra={
            "deleted_triggers": deleted_triggers,
            "blocked_task_id": blocked_task_id,
            "blocked_task_ids": blocked_task_ids,
            "cancelled_activities": cancelled_activities,
        },
    )

    return {
        "status": "ok",
        "deleted_triggers": deleted_triggers,
        "blocked_task_id": blocked_task_id,
        "blocked_task_ids": blocked_task_ids,
        "cancelled_activities": cancelled_activities,
    }
