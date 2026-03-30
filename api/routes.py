"""BossMod AI — REST API routes + WebSocket endpoint.

Agent CRUD, map data, world state, settings, and real-time
WebSocket broadcasting for live Canvas and Activity updates.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from starlette.responses import FileResponse

from pydantic import BaseModel

from api.websocket import manager
from core import config
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries
from core.agent_loop.deliverables import build_work_contract
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.task_roles import default_task_owner_id
from core.llm import context_builder
from core.llm.template_engine import TemplateError, validate_template
from core.prompting.runtime_prompt_lint import lint_runtime_prompts
from core.runtime import runtime_services
from core.messaging import route_human_dm, route_human_channel_message
from core.models.message import HUMAN_SENDER_ID
from core.models import (
    Agent,
    AgentCreate,
    AgentPromptHistoryPolicy,
    AgentPromptHistoryPolicyUpdate,
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
from core.world.tilemap import get_room_at
import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".json", ".py", ".js", ".ts", ".yaml", ".yml",
    ".toml", ".csv", ".xml", ".html", ".css", ".log", ".cfg", ".ini",
    ".sh", ".bash", ".env", ".sql", ".graphql", ".jsx", ".tsx", ".svg",
    ".rst", ".tex", ".makefile", ".dockerfile", ".gitignore",
}


class ActivationBody(BaseModel):
    content: str = "You have been manually activated."


class MeetingMessageBody(BaseModel):
    content: str


class ChannelCreateBody(BaseModel):
    name: str | None = None
    agent_ids: list[str]


class ChannelMessageBody(BaseModel):
    content: str


class CompanyFileSaveBody(BaseModel):
    path: str
    content: str


class CompanyFileCreateBody(BaseModel):
    path: str  # parent directory path
    name: str
    kind: Literal["file", "folder"]


class CompanyFileDeleteBody(BaseModel):
    path: str


class CompanyFileRenameBody(BaseModel):
    path: str
    new_name: str


class CompanyFileMoveBody(BaseModel):
    source: str
    destination: str


class RuntimeContractsBody(BaseModel):
    decision: str
    execution: str
    trigger_event: str
    conversation_envelope: str
    file_deliverable_guidance: str
    communication_snapshot: str


class RuntimeContractTemplateOverridesBody(BaseModel):
    decision: str | None = None
    execution: str | None = None
    trigger_event: str | None = None
    conversation_envelope: str | None = None
    file_deliverable_guidance: str | None = None
    communication_snapshot: str | None = None


class RuntimeContractPreviewBody(BaseModel):
    contract_kind: Literal["decision", "execution"]
    trigger_type: str = "human_chat"
    scope: Literal["contract", "bundle"] = "bundle"
    templates: RuntimeContractTemplateOverridesBody | None = None


class RuntimeControlBody(BaseModel):
    paused: bool


class CliPolicyRuleBody(BaseModel):
    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    category: str = "general"
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool = True
    priority: int = 0


class CliPolicyRuleUpdateBody(BaseModel):
    tier: str | None = None
    pattern: str | None = None
    match_mode: str | None = None
    agent_id: str | None = None
    description: str | None = None
    category: str | None = None
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class CliPolicySimulateBody(BaseModel):
    command: str
    agent_id: str | None = None


class CliSimulatorExecuteBody(BaseModel):
    command: str
    agent_id: str
    content: str | None = None


class CliApprovalDecisionBody(BaseModel):
    decision_note: str | None = None


_RUNTIME_CONTRACT_KEYS = {
    "decision": "runtime_contract_decision",
    "execution": "runtime_contract_execution",
    "trigger_event": "runtime_block_trigger_event",
    "conversation_envelope": "runtime_block_conversation_envelope",
    "file_deliverable_guidance": "runtime_block_file_deliverable_guidance",
    "communication_snapshot": "runtime_block_communication_snapshot",
}
_RUNTIME_PREVIEW_TRIGGERS = [
    "human_chat",
    "peer_message",
    "task_assigned",
    "session_message",
    "session_response",
    "channel_message",
    "channel_response",
    "activity_resumed",
    "watchdog_status_ping",
    "social",
]


def _validate_authored_prompt_template(template: str) -> None:
    """Validate one authored prompt template against the shared template engine."""
    validate_template(
        template,
        allowed_paths=context_builder.AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _runtime_contracts_payload() -> dict[str, object]:
    """Return the current runtime contract settings and template metadata."""
    return {
        "decision": config.require(_RUNTIME_CONTRACT_KEYS["decision"]),
        "execution": config.require(_RUNTIME_CONTRACT_KEYS["execution"]),
        "trigger_event": config.require(_RUNTIME_CONTRACT_KEYS["trigger_event"]),
        "conversation_envelope": config.require(_RUNTIME_CONTRACT_KEYS["conversation_envelope"]),
        "file_deliverable_guidance": config.require(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"]),
        "communication_snapshot": config.require(_RUNTIME_CONTRACT_KEYS["communication_snapshot"]),
        "allowed_variables": context_builder.template_variable_metadata(),
        "template_syntax": context_builder.template_syntax_examples(),
        "preview_triggers": list(_RUNTIME_PREVIEW_TRIGGERS),
        "prompt_health": lint_runtime_prompts().to_payload(),
    }


def _runtime_contract_template_overrides(
    body: RuntimeContractsBody | RuntimeContractTemplateOverridesBody | None,
) -> dict[str, str]:
    """Map runtime-contract request fields to prompt-setting keys."""
    if body is None:
        return {}

    overrides: dict[str, str] = {}
    for field_name, setting_key in _RUNTIME_CONTRACT_KEYS.items():
        value = getattr(body, field_name, None)
        if value is not None:
            overrides[setting_key] = value
    return overrides


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

    # Send unified activity feed
    await ws.send_json(jsonable_encoder({"type": "unified_feed", "data": manager.unified_feed}))
    await ws.send_json(jsonable_encoder({"type": "runtime_state", "data": runtime_services.status_payload()}))

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


@router.get("/metrics/dashboard")
async def get_metrics_dashboard() -> dict[str, object]:
    """Return aggregated dashboard metrics for the Company overview."""
    return await asyncio.to_thread(db.get_dashboard_metrics)


@router.get("/company/files")
async def get_company_files(path: str = "/") -> dict[str, object]:
    """Return a browsable file view rooted at the company workspace."""
    return await asyncio.to_thread(_build_company_files_payload, path)


@router.put("/company/files")
async def save_company_file(body: CompanyFileSaveBody) -> dict[str, object]:
    """Write content back to a file in the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None:
        raise HTTPException(400, "Invalid path")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, "File not found")

    await asyncio.to_thread(resolved.write_text, body.content, encoding="utf-8")
    stat = await asyncio.to_thread(resolved.stat)
    virtual_path = "/" + str(resolved.relative_to(root.resolve())).replace("\\", "/")
    return {"status": "ok", "path": virtual_path, "size_bytes": stat.st_size}


@router.post("/company/files/open-folder")
async def open_company_folder(body: dict) -> dict[str, object]:
    """Open a company workspace folder in the host file explorer."""
    from core.bm_cli.filesystem import artifacts_root

    raw_path = body.get("path", "/")
    root = artifacts_root()
    safe = _resolve_safe_company_path(root, raw_path)
    if safe is None or not safe.exists():
        raise HTTPException(404, "Path not found")

    target = safe if safe.is_dir() else safe.parent
    opener = config.get("desktop_open_folder_handler")
    if opener is None:
        raise HTTPException(
            409,
            {
                "code": "desk_open_folder_handler_required",
                "message": "Choose a folder opener once and BossMod will remember it.",
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


# ── Company file operations (create / delete / rename / move / copy / search / raw) ──


_INVALID_NAME_RE = re.compile(r"(/|\.\.)")
_GIT_NAME_RE = re.compile(r"^\.git")

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


def _validate_name(name: str) -> None:
    """Reject names containing path separators, traversal tokens, or .git* prefixes."""
    if not name or _INVALID_NAME_RE.search(name) or _GIT_NAME_RE.match(name):
        raise HTTPException(400, "Invalid name")


@router.post("/company/files/create", status_code=201)
async def create_company_file(body: CompanyFileCreateBody) -> dict[str, object]:
    """Create an empty file or folder in the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    parent = _resolve_safe_company_path(root, body.path)
    if parent is None or not parent.exists() or not parent.is_dir():
        raise HTTPException(400, "Invalid parent path")

    _validate_name(body.name)
    target = parent / body.name

    if target.exists():
        raise HTTPException(409, "Target already exists")

    if body.kind == "file":
        await asyncio.to_thread(target.touch)
    else:
        await asyncio.to_thread(target.mkdir)

    virtual_path = "/" + str(target.resolve().relative_to(root.resolve())).replace("\\", "/")
    return {"status": "ok", "path": virtual_path}


@router.delete("/company/files")
async def delete_company_file(body: CompanyFileDeleteBody) -> dict[str, object]:
    """Delete a file or empty directory from the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "Path not found")

    if resolved.is_dir():
        if any(resolved.iterdir()):
            raise HTTPException(409, "Directory is not empty")
        await asyncio.to_thread(resolved.rmdir)
    else:
        await asyncio.to_thread(resolved.unlink)

    return {"status": "ok"}


@router.patch("/company/files/rename")
async def rename_company_file(body: CompanyFileRenameBody) -> dict[str, object]:
    """Rename a file or folder in the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    resolved = _resolve_safe_company_path(root, body.path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "Path not found")

    _validate_name(body.new_name)
    new_target = resolved.parent / body.new_name

    await asyncio.to_thread(resolved.rename, new_target)

    virtual_path = "/" + str(new_target.resolve().relative_to(root.resolve())).replace("\\", "/")
    return {"status": "ok", "path": virtual_path}


@router.post("/company/files/move")
async def move_company_file(body: CompanyFileMoveBody) -> dict[str, object]:
    """Move a file or folder within the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    source = _resolve_safe_company_path(root, body.source)
    destination = _resolve_safe_company_path(root, body.destination)
    if source is None or not source.exists():
        raise HTTPException(404, "Source not found")
    if destination is None or not destination.exists() or not destination.is_dir():
        raise HTTPException(400, "Destination must be an existing directory")

    target = destination / source.name
    if target.exists():
        raise HTTPException(409, "Target already exists in destination")

    await asyncio.to_thread(shutil.move, str(source), str(target))

    virtual_path = "/" + str(target.resolve().relative_to(root.resolve())).replace("\\", "/")
    return {"status": "ok", "path": virtual_path}


@router.post("/company/files/copy")
async def copy_company_file(body: CompanyFileMoveBody) -> dict[str, object]:
    """Copy a file or folder within the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    source = _resolve_safe_company_path(root, body.source)
    destination = _resolve_safe_company_path(root, body.destination)
    if source is None or not source.exists():
        raise HTTPException(404, "Source not found")
    if destination is None or not destination.exists() or not destination.is_dir():
        raise HTTPException(400, "Destination must be an existing directory")

    target = destination / source.name
    if target.exists():
        raise HTTPException(409, "Target already exists in destination")

    if source.is_dir():
        await asyncio.to_thread(shutil.copytree, str(source), str(target))
    else:
        await asyncio.to_thread(shutil.copy2, str(source), str(target))

    virtual_path = "/" + str(target.resolve().relative_to(root.resolve())).replace("\\", "/")
    return {"status": "ok", "path": virtual_path}


@router.get("/company/files/search")
async def search_company_files(q: str = Query(..., min_length=1)) -> list[dict[str, object]]:
    """Search for files and folders by name across the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    def _search() -> list[dict[str, object]]:
        root = artifacts_root()
        root_resolved = root.resolve()
        query_lower = q.lower()
        results: list[dict[str, object]] = []

        for dirpath, dirnames, filenames in os.walk(root_resolved):
            dirnames[:] = [d for d in dirnames if not d.startswith(".git")]

            for name in dirnames + filenames:
                if name.startswith(".git"):
                    continue
                if query_lower not in name.lower():
                    continue
                full = Path(dirpath) / name
                is_dir = full.is_dir()
                stat_result = full.stat()
                virtual = "/" + str(full.relative_to(root_resolved)).replace("\\", "/")
                results.append({
                    "name": name,
                    "path": virtual,
                    "is_dir": is_dir,
                    "size_bytes": None if is_dir else stat_result.st_size,
                    "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                })
                if len(results) >= 100:
                    break
            if len(results) >= 100:
                break

        _annotate_agent_names(results)
        return results

    return await asyncio.to_thread(_search)


@router.get("/company/files/raw")
async def get_company_file_raw(path: str = Query(..., min_length=1)):
    """Return the raw bytes of a file from the company workspace."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    resolved = _resolve_safe_company_path(root, path)
    if resolved is None or not resolved.exists():
        raise HTTPException(404, "File not found")
    if not resolved.is_file():
        raise HTTPException(400, "Path is not a file")

    suffix = resolved.suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(str(resolved), media_type=mime_type)


@router.get("/channels")
async def list_channels() -> list[dict[str, object]]:
    """Return active shared channels with roster and latest message previews."""
    items = []
    for channel in db.list_channels():
        members = db.list_channel_member_details(channel.id)
        latest = db.get_latest_channel_message(channel.id)
        items.append(_serialize_channel_summary(channel, members=members, latest_message=latest))
    return items


@router.post("/channels", status_code=201)
async def create_channel(body: ChannelCreateBody):
    """Create one shared channel from the selected company roster members."""
    member_ids = list(dict.fromkeys(agent_id for agent_id in body.agent_ids if isinstance(agent_id, str) and agent_id.strip()))
    if not member_ids:
        raise HTTPException(400, "Select at least one agent")

    agents = db.get_agents_by_ids(member_ids)
    missing = [agent_id for agent_id in member_ids if agent_id not in agents]
    if missing:
        raise HTTPException(404, f"Agents not found: {', '.join(missing)}")

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
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_created",
        detail=f'Created shared channel "{channel.name}"',
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
    """Return the agent's API key (for the agent editor only)."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return {"api_key": agent.api_key}


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
    if opener is None:
        raise HTTPException(
            409,
            {
                "code": "desk_open_folder_handler_required",
                "message": "Choose a folder opener once and BossMod will remember it.",
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
    prompt_template = fields.get("prompt_template")
    if isinstance(prompt_template, str):
        try:
            _validate_authored_prompt_template(prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
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
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in notifications
    ]


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
    return {
        "id": item.id,
        "channel_id": item.channel_id,
        "author_type": item.author_type,
        "author_agent_id": item.author_agent_id,
        "author_name": item.author_name,
        "content": item.content,
        "source_channel": item.source_channel,
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
        "member_count": len(members or []),
        "members": members or [],
        "latest_message": latest,
    }


def _build_agent_desk_payload(agent: Agent, path: str) -> dict[str, object]:
    """Build one filesystem-style Desk payload in a worker thread."""
    try:
        resolved = resolve_cli_path(agent.storage_key, "/", path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if resolved.real_path is None and resolved.virtual_path != "/":
        raise HTTPException(404, "Path not found")
    if resolved.real_path is not None and not resolved.exists:
        raise HTTPException(404, "Path not found")

    if resolved.real_path is not None and resolved.real_path.is_file():
        content, truncated = _read_desk_file_preview(resolved.real_path)
        artifact = db.get_artifact_by_absolute_path(str(resolved.real_path.resolve()))
        return {
            "kind": "file",
            "path": resolved.virtual_path,
            "name": Path(resolved.virtual_path).name,
            "breadcrumbs": _desk_breadcrumbs(resolved.virtual_path),
            "artifact": _serialize_artifact(artifact),
            "content": content,
            "truncated": truncated,
        }

    entries = _list_virtual_root_entries(agent) if resolved.virtual_path == "/" else _list_desk_entries(agent, resolved)
    return {
        "kind": "directory",
        "path": resolved.virtual_path,
        "name": _desk_display_name(resolved.virtual_path),
        "breadcrumbs": _desk_breadcrumbs(resolved.virtual_path),
        "entries": entries,
    }


def _read_desk_file_preview(path: Path, limit_chars: int = 20_000) -> tuple[str, bool]:
    """Read a bounded UTF-8 preview for one desk file."""
    configured_limit = config.get_int("desk_preview_max_chars")
    if configured_limit is not None and configured_limit > 0:
        limit_chars = configured_limit
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read(limit_chars + 1)
    return content[:limit_chars], len(content) > limit_chars


def _list_virtual_root_entries(agent: Agent) -> list[dict[str, object]]:
    """Return the virtual filesystem mounts without scanning nested contents."""
    entries: list[dict[str, object]] = []
    for mount in virtual_root_entries():
        path = f"/{mount.rstrip('/')}"
        resolved = resolve_cli_path(agent.storage_key, "/", path)
        updated_at = None
        if resolved.real_path is not None and resolved.real_path.exists():
            updated_at = datetime.fromtimestamp(resolved.real_path.stat().st_mtime).isoformat()
        entries.append(
            {
                "name": mount.rstrip("/"),
                "path": path,
                "is_dir": True,
                "artifact": None,
                "size_bytes": None,
                "updated_at": updated_at,
                "category": "mount",
            }
        )
    return entries


def _list_desk_entries(
    agent: Agent,
    resolved,
    *,
    exclude_names: set[str] | None = None,
    directories_only: bool = False,
) -> list[dict[str, object]]:
    if resolved.real_path is None or not resolved.real_path.exists() or not resolved.real_path.is_dir():
        return []

    scanned: list[dict[str, object]] = []
    child_paths: list[str] = []
    with os.scandir(resolved.real_path) as iterator:
        for entry in iterator:
            name = entry.name
            if name in {".git", ".gitignore", ".gitattributes"}:
                continue
            if exclude_names and name in exclude_names:
                continue
            is_dir = entry.is_dir()
            if directories_only and not is_dir:
                continue
            stat_result = entry.stat()
            absolute_path = str(Path(entry.path).resolve())
            if not is_dir:
                child_paths.append(absolute_path)
            scanned.append(
                {
                    "name": name,
                    "path": _child_virtual_path(resolved.virtual_path, name),
                    "is_dir": is_dir,
                    "absolute_path": absolute_path,
                    "size_bytes": None if is_dir else stat_result.st_size,
                    "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                }
            )

    scanned.sort(key=lambda item: (not bool(item["is_dir"]), str(item["name"]).lower()))
    artifact_map = {
        artifact.absolute_path: artifact
        for artifact in db.list_artifacts(absolute_paths=child_paths, limit=max(len(child_paths), 1))
    }

    entries: list[dict[str, object]] = []
    for item in scanned:
        artifact = artifact_map.get(str(item["absolute_path"]))
        entries.append(
            {
                "name": item["name"],
                "path": item["path"],
                "is_dir": item["is_dir"],
                "artifact": _serialize_artifact(artifact),
                "size_bytes": item["size_bytes"],
                "updated_at": item["updated_at"],
                "category": _entry_category(str(item["path"]), artifact, is_dir=bool(item["is_dir"])),
            }
        )
    return entries


def _entry_category(path: str, artifact, *, is_dir: bool = False) -> str:
    if path == "/me":
        return "workspace"
    if path == "/projects":
        return "projects"
    if artifact is not None:
        return artifact.category
    if is_dir:
        return "folder"
    if path.startswith("/projects/"):
        return "project"
    if path.startswith("/me/notes/") or path == "/me/notes":
        return "note"
    return "output"


def _serialize_artifact(artifact) -> dict[str, object] | None:
    if artifact is None:
        return None
    return {
        "id": artifact.id,
        "title": artifact.title,
        "task_id": artifact.task_id,
        "virtual_path": artifact.virtual_path,
        "category": artifact.category,
        "size_bytes": artifact.size_bytes,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def _desk_breadcrumbs(path: str) -> list[dict[str, str]]:
    if path in {"", "/"}:
        return [{"label": "/", "path": "/"}]
    parts = [item for item in path.strip("/").split("/") if item]
    breadcrumbs: list[dict[str, str]] = [{"label": "/", "path": "/"}]
    current = ""
    for part in parts:
        current += f"/{part}"
        label = part
        breadcrumbs.append({"label": label, "path": current})
    return breadcrumbs


def _child_virtual_path(parent: str, name: str) -> str:
    if parent in {"", "/"}:
        return f"/{name}"
    return f"{parent.rstrip('/')}/{name}"


def _desk_display_name(path: str) -> str:
    if path == "/":
        return "Workspace"
    if path == "/me":
        return "Desk"
    if path == "/projects":
        return "Projects"
    return Path(path).name or path


def _resolve_safe_company_path(root: Path, raw_path: str) -> Path | None:
    """Resolve a user-supplied path against the artifacts root, rejecting traversal."""
    cleaned = raw_path.strip().lstrip("/") or "."
    candidate = (root / cleaned).resolve()
    root_resolved = root.resolve()
    if candidate == root_resolved or root_resolved in candidate.parents:
        return candidate
    return None


def _annotate_agent_names(items: list[dict], name_key: str = "name") -> None:
    """Detect agent_XXXX keys in a list of dicts and attach agent_name."""
    agent_keys = [
        str(d.get(name_key, ""))
        for d in items
        if re.match(r"^agent_\d{4}$", str(d.get(name_key, "")))
    ]
    if not agent_keys:
        for d in items:
            d.setdefault("agent_name", None)
        return
    from db.agent_storage_identities import get_agent_names_by_storage_keys

    name_map = get_agent_names_by_storage_keys(agent_keys)
    for d in items:
        d["agent_name"] = name_map.get(str(d.get(name_key)))


def _build_company_files_payload(path: str) -> dict[str, object]:
    """Build a filesystem-style payload for the company workspace browser."""
    from core.bm_cli.filesystem import artifacts_root

    root = artifacts_root()
    resolved = _resolve_safe_company_path(root, path)
    if resolved is None:
        raise HTTPException(400, "Invalid path")
    if not resolved.exists():
        raise HTTPException(404, "Path not found")

    virtual_path = "/" + str(resolved.relative_to(root.resolve())).replace("\\", "/")
    if virtual_path == "/.":
        virtual_path = "/"

    if resolved.is_file():
        stat = resolved.stat()
        binary = resolved.suffix.lower() not in _TEXT_FILE_EXTENSIONS
        content, truncated = ("", False) if binary else _read_desk_file_preview(resolved)
        return {
            "kind": "file",
            "path": virtual_path,
            "name": resolved.name,
            "breadcrumbs": _company_breadcrumbs(virtual_path),
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "content": content,
            "truncated": truncated,
            "binary": binary,
        }

    entries: list[dict[str, object]] = []
    with os.scandir(resolved) as iterator:
        for entry in iterator:
            name = entry.name
            if name in {".git", ".gitignore", ".gitattributes"}:
                continue
            is_dir = entry.is_dir()
            stat_result = entry.stat()
            child_path = _child_virtual_path(virtual_path, name)
            entries.append({
                "name": name,
                "path": child_path,
                "is_dir": is_dir,
                "size_bytes": None if is_dir else stat_result.st_size,
                "updated_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
            })
    entries.sort(key=lambda e: (not bool(e["is_dir"]), str(e["name"]).lower()))
    _annotate_agent_names(entries)

    return {
        "kind": "directory",
        "path": virtual_path,
        "name": Path(virtual_path).name if virtual_path != "/" else "Company Workspace",
        "breadcrumbs": _company_breadcrumbs(virtual_path),
        "entries": entries,
    }


def _company_breadcrumbs(path: str) -> list[dict[str, str]]:
    """Build breadcrumb trail for the company file browser."""
    if path in {"", "/"}:
        return [{"label": "Company", "path": "/"}]
    parts = [item for item in path.strip("/").split("/") if item]
    breadcrumbs: list[dict[str, str]] = [{"label": "Company", "path": "/"}]
    current = ""
    for part in parts:
        current += f"/{part}"
        breadcrumbs.append({"label": part, "path": current})
    _annotate_agent_names(breadcrumbs, name_key="label")
    return breadcrumbs


def _launch_file_explorer(path: Path, *, opener: str) -> None:
    """Open a directory in the host platform's file explorer."""
    subprocess.Popen(_file_explorer_command(path, opener=opener))


def _file_explorer_command(path: Path, *, opener: str) -> list[str]:
    """Return the platform-specific file-explorer command for one directory."""
    selected = opener.strip()
    if not selected:
        raise OSError("No folder opener is configured")

    if selected == "system":
        if sys.platform.startswith("darwin"):
            return ["open", str(path)]
        if sys.platform.startswith("win"):
            return ["explorer", str(path)]
        if shutil.which("xdg-open"):
            return ["xdg-open", str(path)]
        raise OSError("System default opener is unavailable on this machine")

    if sys.platform.startswith("win") and selected.lower() == "explorer":
        return ["explorer", str(path)]
    if sys.platform.startswith("darwin") and selected == "open":
        return ["open", str(path)]
    if shutil.which(selected):
        return [selected, str(path)]
    raise OSError(f'Configured folder opener "{selected}" was not found on PATH')


def _available_folder_opener_options() -> list[dict[str, str]]:
    """Return detected folder opener choices for the current platform."""
    if sys.platform.startswith("darwin"):
        return [{"value": "open", "label": "Finder", "description": "Use macOS Finder."}]
    if sys.platform.startswith("win"):
        return [{"value": "explorer", "label": "File Explorer", "description": "Use Windows File Explorer."}]

    options: list[dict[str, str]] = []
    known_linux_openers = (
        ("nautilus", "Nautilus"),
        ("dolphin", "Dolphin"),
        ("nemo", "Nemo"),
        ("thunar", "Thunar"),
        ("pcmanfm", "PCManFM"),
        ("caja", "Caja"),
        ("konqueror", "Konqueror"),
        ("lxqt-filemanager", "LXQt File Manager"),
    )
    for binary, label in known_linux_openers:
        if shutil.which(binary):
            options.append(
                {
                    "value": binary,
                    "label": label,
                    "description": f"Open folders with {label}.",
                }
            )
    if shutil.which("xdg-open"):
        options.append(
            {
                "value": "system",
                "label": "System Default",
                "description": "Use the desktop's default folder opener.",
            }
        )
    return options


# ─── Tasks CRUD ───

@router.get("/tasks")
async def list_tasks(
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    status: str | None = None,
):
    tasks = db.list_tasks(
        assigned_to=assigned_to,
        owner_id=owner_id,
        requester_id=requester_id,
        parent_task_id=parent_task_id,
        status=status,
    )
    # Resolve agent UUIDs to human-readable names
    agent_ids = {t.assigned_to for t in tasks if t.assigned_to}
    agent_ids |= {t.requester_id for t in tasks if t.requester_id}
    agent_ids |= {t.owner_id for t in tasks if t.owner_id}
    agent_names: dict[str, str] = {}
    for aid in agent_ids:
        agent = db.get_agent(aid)
        if agent:
            agent_names[aid] = agent.name
    return [
        {
            **t.model_dump(mode="json"),
            "assigned_to_name": agent_names.get(t.assigned_to) if t.assigned_to else None,
            "requester_name": agent_names.get(t.requester_id) if t.requester_id else None,
            "owner_name": agent_names.get(t.owner_id) if t.owner_id else None,
        }
        for t in tasks
    ]


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate) -> Task:
    work_contract = body.work_contract
    source_channel = body.source_channel or "api"
    notification_policy = body.notification_policy or "completion_blocked"
    requester_id = body.requester_id or HUMAN_SENDER_ID
    parent_task = None
    if requester_id != HUMAN_SENDER_ID and not db.get_agent(requester_id):
        raise HTTPException(404, "Requester agent not found")
    if body.owner_id == HUMAN_SENDER_ID:
        raise HTTPException(400, "Task owner must be an agent, not the human operator")
    if body.owner_id and not db.get_agent(body.owner_id):
        raise HTTPException(404, "Owner agent not found")
    if body.parent_task_id:
        parent_task = db.get_task(body.parent_task_id)
        if not parent_task:
            raise HTTPException(404, "Parent task not found")
    if work_contract is not None:
        if body.assigned_to:
            agent = db.get_agent(body.assigned_to)
            if not agent:
                raise HTTPException(404, "Assigned agent not found")
            cli_state = db.ensure_agent_cli_state(agent.id)
            work_contract = build_work_contract(
                work_contract.deliverables,
                agent_storage_key=agent.storage_key,
                cwd=cli_state.cwd,
            )
        elif any(item.type == "file" and not item.path.startswith("/") for item in work_contract.deliverables):
            raise HTTPException(
                400,
                "Task work_contract file deliverables must use absolute BossMod CLI paths when assigned_to is omitted.",
            )
    owner_id = body.owner_id or default_task_owner_id(
        assignee_id=body.assigned_to,
        requester_id=requester_id,
        created_by=HUMAN_SENDER_ID,
        parent_task=parent_task,
    )

    task = db.create_task(
        title=body.title,
        description=body.description,
        project=body.project,
        assigned_to=body.assigned_to,
        requester_id=requester_id,
        owner_id=owner_id,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=body.parent_task_id,
        work_contract=work_contract,
        source_channel=source_channel,
        notification_policy=notification_policy,
        notification_channel_id=body.notification_channel_id,
    )
    if body.assigned_to:
        await runtime_services.enqueue_trigger(**build_task_assigned_trigger(task))
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


# ─── Unified Activity Feed ───


@router.get("/activity/feed")
async def get_activity_feed(
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    category: str | None = None,
    agent_name: str | None = None,
):
    """Return a paginated, filterable unified feed from all activity sources."""
    limit = min(max(limit, 1), 200)
    valid_categories = {"agent", "task", "error", "system"}
    if category and category not in valid_categories:
        raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(valid_categories))}")
    return db.get_unified_feed(
        limit=limit,
        offset=offset,
        search=search or None,
        category=category or None,
        agent_name=agent_name or None,
    )


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


# ─── CLI Policy ───

@router.get("/cli-policy/virtual-commands")
async def list_virtual_commands():
    """Return the read-only virtual command registry for the UI."""
    from core.bm_cli.command_registry import (
        VIRTUAL_COMMAND_REGISTRY,
        VIRTUAL_CATEGORIES,
    )

    commands = [
        {
            "name": cmd.name,
            "category": cmd.category,
            "description": cmd.description,
            "usage_syntax": cmd.usage_syntax,
            "help_text": cmd.help_text,
        }
        for cmd in VIRTUAL_COMMAND_REGISTRY.values()
    ]

    categories = [
        {"name": k, "description": v}
        for k, v in VIRTUAL_CATEGORIES.items()
    ]

    return {"commands": commands, "categories": categories}


# Rules CRUD
@router.get("/cli-policy/rules")
async def list_cli_policy_rules(tier: str | None = None, agent_id: str | None = None):
    return db.list_cli_policy_rules(tier=tier, agent_id=agent_id)


@router.post("/cli-policy/rules", status_code=201)
async def create_cli_policy_rule(body: CliPolicyRuleBody):
    # Validate tier
    if body.tier not in ("never_allowed", "always_allowed", "approval_required"):
        raise HTTPException(400, f"Invalid tier: {body.tier}")
    if body.match_mode not in ("exact", "prefix", "glob"):
        raise HTTPException(400, f"Invalid match_mode: {body.match_mode}")
    if not body.pattern.strip():
        raise HTTPException(400, "Pattern cannot be empty")
    rule = db.create_cli_policy_rule(
        tier=body.tier,
        pattern=body.pattern.strip(),
        match_mode=body.match_mode,
        agent_id=body.agent_id,
        description=body.description,
        category=body.category,
        usage_syntax=body.usage_syntax,
        help_text=body.help_text,
        enabled=body.enabled,
        priority=body.priority,
    )
    # Invalidate policy engine cache
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return rule


@router.put("/cli-policy/rules/{rule_id}")
async def update_cli_policy_rule(rule_id: str, body: CliPolicyRuleUpdateBody):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "tier" in fields and fields["tier"] not in ("never_allowed", "always_allowed", "approval_required"):
        raise HTTPException(400, f"Invalid tier: {fields['tier']}")
    if "match_mode" in fields and fields["match_mode"] not in ("exact", "prefix", "glob"):
        raise HTTPException(400, f"Invalid match_mode: {fields['match_mode']}")
    updated = db.update_cli_policy_rule(rule_id, **fields)
    if updated is None:
        raise HTTPException(404, "Rule not found")
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return updated


@router.delete("/cli-policy/rules/{rule_id}")
async def delete_cli_policy_rule(rule_id: str):
    deleted = db.delete_cli_policy_rule(rule_id)
    if not deleted:
        raise HTTPException(404, "Rule not found")
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return {"ok": True}


@router.post("/cli-policy/rules/seed-defaults")
async def seed_cli_policy_rules():
    # Delete all existing rules first, then re-seed. Existing approval
    # requests retain history but drop their matched_rule_id reference.
    with db.transaction():
        db.execute("UPDATE cli_approval_requests SET matched_rule_id = NULL WHERE matched_rule_id IS NOT NULL")
        db.execute("DELETE FROM cli_policy_rules")
        db.seed_default_cli_policy_rules()
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return {"ok": True, "message": "Default rules re-seeded"}


# Approvals
@router.get("/cli-policy/approvals")
async def list_cli_approval_requests(status: str | None = None, agent_id: str | None = None, limit: int = 50):
    return db.list_cli_approval_requests(status=status, agent_id=agent_id, limit=min(limit, 200))


@router.post("/cli-policy/approvals/{request_id}/approve")
async def approve_cli_request(request_id: str):
    approval = db.approve_cli_approval_request(request_id)
    if approval is None:
        raise HTTPException(404, "Approval request not found or already resolved")
    # Create a trigger so the agent resumes with the approved command
    db.create_agent_trigger(
        agent_id=approval.agent_id,
        trigger_type="cli_approval_resolved",
        source_channel="system",
        payload={
            "approval_request_id": approval.id,
            "command": approval.command,
            "content": approval.content,
            "cwd": approval.cwd,
            "status": "approved",
        },
    )
    await manager.broadcast_activity(
        event="cli_approval_approved",
        detail=f"Command approved: {approval.command}",
    )
    return approval


@router.post("/cli-policy/approvals/{request_id}/reject")
async def reject_cli_request(request_id: str, body: CliApprovalDecisionBody | None = None):
    note = body.decision_note if body else None
    rejection = db.reject_cli_approval_request(request_id, decision_note=note)
    if rejection is None:
        raise HTTPException(404, "Approval request not found or already resolved")
    # Create a trigger so the agent knows the command was rejected
    db.create_agent_trigger(
        agent_id=rejection.agent_id,
        trigger_type="cli_approval_resolved",
        source_channel="system",
        payload={
            "approval_request_id": rejection.id,
            "command": rejection.command,
            "status": "rejected",
            "decision_note": note,
        },
    )
    await manager.broadcast_activity(
        event="cli_approval_rejected",
        detail=f"Command rejected: {rejection.command}" + (f" — {note}" if note else ""),
    )
    return rejection


# Simulator — policy dry-run (lightweight check without execution)
@router.post("/cli-policy/simulate")
async def simulate_cli_policy(body: CliPolicySimulateBody):
    from core.bm_cli.policies import evaluate_command_policy
    from core.bm_cli.runtime import VIRTUAL_COMMANDS
    command = body.command.strip()
    if not command:
        raise HTTPException(400, "Command cannot be empty")
    try:
        decision = evaluate_command_policy(command, VIRTUAL_COMMANDS, body.agent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    matched_rule = None
    if decision.matched_rule_id:
        matched_rule = db.get_cli_policy_rule(decision.matched_rule_id)
    return {
        "command": command,
        "agent_id": body.agent_id,
        "decision": {
            "allowed": decision.allowed,
            "tier": decision.tier,
            "executor": decision.executor,
            "approval_required": decision.approval_required,
            "message": decision.message,
            "matched_rule_id": decision.matched_rule_id,
        },
        "matched_rule": matched_rule,
    }


# Simulator — full interactive execution (runs the real BM_CLI pipeline)
@router.post("/cli-policy/simulator/execute")
async def simulator_execute(body: CliSimulatorExecuteBody):
    """Execute a command through the full BM_CLI pipeline as a specific agent.

    This is the interactive simulator — it actually runs the command (virtual or
    shell) and returns real output, exactly as the agent would experience it.
    Approval-required commands return the approval gate instead of executing.
    """
    from core.bm_cli.runtime import execute_bm_cli
    from fastapi.encoders import jsonable_encoder

    if not body.command.strip():
        raise HTTPException(400, "Command cannot be empty")

    agent = db.get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(404, f"Agent not found: {body.agent_id}")

    state = db.get_agent_state(body.agent_id)
    if state is None:
        raise HTTPException(404, f"Agent state not found: {body.agent_id}")

    cli_result = execute_bm_cli(
        agent,
        state,
        body.command.strip(),
        body.content,
        trigger_type="simulator",
    )

    return {
        "command": cli_result.command,
        "ok": cli_result.ok,
        "exit_code": cli_result.exit_code,
        "executor": cli_result.executor,
        "kind": cli_result.kind,
        "output": cli_result.prompt_content,
        "detail": cli_result.detail,
        "cwd": cli_result.cwd,
        "approval_required": cli_result.approval_required,
        "approval_request_id": cli_result.approval_request_id,
        "matched_rule_id": cli_result.matched_rule_id,
    }


# ─── Settings ───

@router.get("/settings")
async def get_settings(category: str | None = None):
    return db.get_settings(category)


@router.get("/settings/desktop-open-folder-options")
async def get_desktop_open_folder_options():
    return {
        "current": config.get("desktop_open_folder_handler"),
        "options": _available_folder_opener_options(),
    }


@router.get("/runtime/contracts")
async def get_runtime_contracts():
    return _runtime_contracts_payload()


@router.get("/runtime/state")
async def get_runtime_state():
    return runtime_services.status_payload()


@router.put("/runtime/state")
async def set_runtime_state(body: RuntimeControlBody):
    was_paused = runtime_services.is_paused()
    if body.paused:
        payload = await runtime_services.pause()
        if not was_paused:
            await manager.broadcast_activity(
                event="runtime_paused",
                detail="Emergency pause engaged. AI runtime services stopped.",
            )
    else:
        payload = await runtime_services.resume()
        if was_paused:
            await manager.broadcast_activity(
                event="runtime_resumed",
                detail="AI runtime services resumed.",
            )
    await manager.broadcast_runtime_state(payload)
    return payload


@router.put("/runtime/contracts")
async def set_runtime_contracts(body: RuntimeContractsBody):
    try:
        _validate_authored_prompt_template(body.decision)
        _validate_authored_prompt_template(body.execution)
        _validate_authored_prompt_template(body.trigger_event)
        _validate_authored_prompt_template(body.conversation_envelope)
        _validate_authored_prompt_template(body.file_deliverable_guidance)
        _validate_authored_prompt_template(body.communication_snapshot)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc

    with db.transaction():
        db.set_setting(_RUNTIME_CONTRACT_KEYS["decision"], body.decision, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["execution"], body.execution, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["trigger_event"], body.trigger_event, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["conversation_envelope"], body.conversation_envelope, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"], body.file_deliverable_guidance, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["communication_snapshot"], body.communication_snapshot, "advanced")
    config.reload()
    return _runtime_contracts_payload()


@router.post("/runtime/contracts/reset")
async def reset_runtime_contracts():
    """Reset runtime contracts and runtime-owned prompt blocks back to seeded defaults."""
    with db.transaction():
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["decision"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["execution"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["trigger_event"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["conversation_envelope"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["communication_snapshot"])
    config.reload()
    return _runtime_contracts_payload()


@router.post("/runtime/contracts/preview")
async def preview_runtime_contract(body: RuntimeContractPreviewBody):
    if body.trigger_type not in _RUNTIME_PREVIEW_TRIGGERS:
        raise HTTPException(400, f"Unsupported preview trigger: {body.trigger_type}")
    template_overrides = _runtime_contract_template_overrides(body.templates)
    try:
        for template in template_overrides.values():
            _validate_authored_prompt_template(template)
        prompt_health = lint_runtime_prompts(template_overrides).to_payload()
        if body.scope == "contract":
            rendered = context_builder.preview_runtime_contract(
                contract_kind=body.contract_kind,
                trigger_type=body.trigger_type,
                template_overrides=template_overrides,
            )
            messages: list[dict[str, str]] = []
        else:
            preview = context_builder.preview_prompt_bundle(
                contract_kind=body.contract_kind,
                trigger_type=body.trigger_type,
                template_overrides=template_overrides,
            )
            rendered = str(preview["rendered"])
            messages = list(preview["messages"])
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "contract_kind": body.contract_kind,
        "trigger_type": body.trigger_type,
        "scope": body.scope,
        "rendered": rendered,
        "messages": messages,
        "prompt_health": prompt_health,
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


@router.post("/settings/{key}/reset")
async def reset_setting_to_default(key: str):
    """Reset one seeded setting back to its default value."""
    try:
        result = db.reset_setting_to_seed(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config.reload()
    return result


@router.put("/settings/{key}")
async def set_setting(key: str, value: str, category: str = "general"):
    if key == "system_prompt_template" or key in _RUNTIME_CONTRACT_KEYS.values():
        try:
            _validate_authored_prompt_template(value)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
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
    try:
        _validate_authored_prompt_template(body.prompt_template)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return db.create_personality(
        name=body.name,
        prompt_template=body.prompt_template,
    )


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: AIPersonalityUpdate) -> AIPersonality:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    prompt_template = fields.get("prompt_template")
    if isinstance(prompt_template, str):
        try:
            _validate_authored_prompt_template(prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    p = db.update_personality(personality_id, **fields)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.delete("/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str):
    if not db.delete_personality(personality_id):
        raise HTTPException(404, "Personality not found")
