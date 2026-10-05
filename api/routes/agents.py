"""Agent CRUD, desk, chat, meetings, channels, and runtime reset."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from api.routes._desk import _build_agent_desk_payload
from api.routes._shared import (
    _IMAGE_MIME_TYPES,
    _available_folder_opener_options,
    _launch_file_explorer,
)
from api.websocket import manager
from core import config
from core.agent_loop import activity_runtime
from core.agent_pack import agent_is_edited, apply_template_to_agent, template_contract_hash
from core.agent_repository import agent_repository
from core.bm_cli.approval_gate import global_auto_approve_enabled
from core.bm_cli.fs_commands import write_virtual_text
from core.bm_cli.virtual_fs import resolve_cli_path
from core.llm.thinking import unoffered
from core.messaging import route_human_channel_message, route_human_dm
from core.models import (
    Agent,
    AgentCreate,
    AgentPromptHistoryPolicy,
    AgentPromptHistoryPolicyUpdate,
    AgentUpdate,
)
from core.models.agent_template import AgentTemplate
from core.models.message import HUMAN_SENDER_ID
from core.agent_loop.channel_host import is_thread_paused, pause_thread, paused_thread_ids, resume_thread
from core.channel_archive import archive_thread_as_operator, reopen_thread_as_operator
from core.channel_members import ThreadSeatError, seat_agent_in_thread
from core.tasking.service import list_open_origin_tasks_for_channel
from core.tasking.transitions import IllegalTaskTransition
from core.runtime import runtime_services
from core.agent_loop.task_origin_mirrors import mirror_origin_status
from core.tasking.transitions import transition_task
from core.world.seating import DeskNotAChair, DeskTaken, choose_desk, place_agent_at_desk
from core.world.tilemap import get_room_at
import db
from core.attachments import company_path
from core.bm_cli.floor_roots import company_root
from db.attachments import AttachmentLinkError, get_attachments_for_messages
from db.floors import LOBBY_ID


def _requested_desk(desk_x: int | None, desk_y: int | None) -> tuple[int, int] | None:
    """The chair a body asks for, or None when it names no complete desk."""
    if desk_x is None or desk_y is None:
        return None
    return (desk_x, desk_y)


def _checked_desk(
    floor_id: str,
    requested: tuple[int, int] | None,
    *,
    exclude_agent_id: str | None = None,
) -> tuple[int | None, int | None]:
    """Resolve the desk a create or patch writes, as HTTP errors.

    Raises:
        HTTPException: 400 when ``requested`` is not a desk chair, 409 when
            another agent on ``floor_id`` already holds it.
    """
    try:
        desk = choose_desk(floor_id, requested, exclude_agent_id=exclude_agent_id)
    except DeskNotAChair as exc:
        raise HTTPException(400, str(exc)) from exc
    except DeskTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    if desk is None:
        return None, None
    return desk


router = APIRouter()


class ActivationBody(BaseModel):
    content: str = "You have been manually activated."
    attachment_ids: list[str] | None = None


class MeetingMessageBody(BaseModel):
    content: str


class ChannelCreateBody(BaseModel):
    name: str | None = None
    agent_ids: list[str]


class ChannelMessageBody(BaseModel):
    content: str
    attachment_ids: list[str] | None = None


class ChannelRenameBody(BaseModel):
    name: str


class ChannelCliAutoApproveBody(BaseModel):
    enabled: bool


class AgentCliAutoApproveBody(BaseModel):
    enabled: bool


class ChannelMemberBody(BaseModel):
    agent_id: str


class AgentDeskSaveBody(BaseModel):
    path: str
    content: str


# A thread name is one line in the roster rail and one line in the chat header.
# The cap is the same 120 characters the agent form gives a specialty, so the
# two operator-typed labels that share a rail agree about what fits.
CHANNEL_NAME_MAX_LENGTH = 120


def _validate_ai_choice(
    connection_id: str | None,
    thinking_social: str,
    thinking_work: str,
) -> None:
    """Check an agent's AI connection and thinking levels before they are saved.

    Args:
        connection_id: The connection the agent will use, or None when it
            stays unlinked (allowed only with both levels at ``default``).
        thinking_social: The Social activation's thinking choice.
        thinking_work: The Work activation's thinking choice.

    Raises:
        HTTPException: 400 when the connection does not exist, has no model,
            or does not offer a chosen level (the detail names the field).
    """
    levels = None
    if connection_id is not None:
        conn = db.get_connection_by_id(connection_id)
        if conn is None:
            raise HTTPException(400, "AI connection not found")
        if not (conn.model or "").strip():
            raise HTTPException(
                400, f"AI connection '{conn.name}' has no model. Set one in Settings → Connections first.",
            )
        levels = conn.thinking_levels
    missing = unoffered(levels, {"thinking_social": thinking_social, "thinking_work": thinking_work})
    if missing:
        raise HTTPException(400, f"Thinking level not offered by this AI connection: {', '.join(missing)}")


# ─── Agents CRUD ───

@router.get("/agents")
async def list_agents() -> list[Agent]:
    return db.list_agents()


# Registered BEFORE /agents/{agent_id}: FastAPI matches in order, and the
# literal "vacation" would otherwise be read as an agent id.
@router.get("/agents/vacation")
async def list_vacationing_agents() -> list[dict[str, object]]:
    """Return the agents on vacation, most recently sent home first."""
    return [
        {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "color": agent.color,
            "vacation_since": _iso_or_none(agent.vacation_since),
        }
        for agent in db.list_vacationing_agents()
    ]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, object]:
    """Return one agent, plus whether Global auto-approve is on.

    Raises:
        HTTPException: 404 when no agent has ``agent_id``.
        ConfigError: The Global auto-approve setting is missing or not a boolean.
    """
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _serialize_agent(agent)


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
async def list_channels(status: str = "active") -> list[dict[str, object]]:
    """Return shared channels with roster and latest message previews."""
    wanted = (status or "active").strip().lower()
    if wanted not in {"active", "archived"}:
        raise HTTPException(400, "status must be active or archived")
    channels = db.list_channels(status=wanted)
    if not channels:
        return []
    channel_ids = [channel.id for channel in channels]
    # A fixed number of reads however many threads there are: each lookup
    # below is one batch for every channel, and the global flag is read once.
    members = db.list_channel_member_details_for(channel_ids)
    latest = db.get_latest_channel_messages(channel_ids)
    paused = paused_thread_ids(channel_ids)
    auto_approve_global = global_auto_approve_enabled()
    return [
        _serialize_channel_summary(
            channel,
            members=members[channel.id],
            latest_message=latest.get(channel.id),
            conversation_paused=channel.id in paused,
            auto_approve_global=auto_approve_global,
        )
        for channel in channels
    ]


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

    from core.floors import FloorDenied

    try:
        channel = db.create_channel(
            name=name,
            member_agent_ids=member_ids,
            created_by=HUMAN_SENDER_ID,
        )
    except FloorDenied as exc:
        raise HTTPException(403, str(exc)) from exc
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


@router.patch("/channels/{channel_id}")
async def rename_channel(channel_id: str, body: ChannelRenameBody):
    """Rename one shared thread.

    Metadata only: the roster, the transcript and the archive state are all
    untouched. The 404 is raised before the write rather than inferred from a
    ``None`` return, so an unknown thread and a failed update are never the
    same answer.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Thread name cannot be empty")
    if len(name) > CHANNEL_NAME_MAX_LENGTH:
        raise HTTPException(
            400, f"Thread name must be {CHANNEL_NAME_MAX_LENGTH} characters or fewer",
        )
    if db.get_channel(channel_id) is None:
        raise HTTPException(404, "Thread not found")

    renamed = db.update_channel(channel_id, name=name)
    if renamed is None:
        raise HTTPException(404, "Thread not found")
    members = db.list_channel_member_details(renamed.id)
    latest = db.get_latest_channel_message(renamed.id)
    summary = _serialize_channel_summary(renamed, members=members, latest_message=latest)
    # Every other surface holding this thread's name — the rail, an open
    # transcript in another window — learns about it the same way archive and
    # reopen are learned about.
    await manager.broadcast_channel_updated(summary)
    return summary


@router.patch("/channels/{channel_id}/cli-auto-approve")
async def set_channel_cli_auto_approve(channel_id: str, body: ChannelCliAutoApproveBody):
    """Turn per-thread CLI auto-approve on or off.

    Writes only that flag. Default policy, Soft-block, and Deny picks stay.
    """
    if db.get_channel(channel_id) is None:
        raise HTTPException(404, "Thread not found")
    updated = db.update_channel(channel_id, cli_auto_approve=body.enabled)
    if updated is None:
        raise HTTPException(404, "Thread not found")
    members = db.list_channel_member_details(updated.id)
    latest = db.get_latest_channel_message(updated.id)
    summary = _serialize_channel_summary(updated, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    return summary


@router.post("/channels/{channel_id}/pause")
async def pause_channel_thread(channel_id: str):
    """Pause one thread. Host-side. Does not cancel board work."""
    return await _set_channel_pause(channel_id, paused=True)


@router.post("/channels/{channel_id}/resume")
async def resume_channel_thread(channel_id: str):
    """Resume one paused thread without opening a Talk round."""
    return await _set_channel_pause(channel_id, paused=False)


async def _set_channel_pause(channel_id: str, *, paused: bool) -> dict[str, object]:
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        raise HTTPException(404, "Thread not found")
    marker = pause_thread(channel.id) if paused else resume_thread(channel.id)
    if marker:
        await manager.broadcast_channel_message(
            channel_id=marker["channel_id"],
            content=marker["content"],
            author_type=marker.get("author_type") or "system",
            author_name=marker.get("author_name") or "BossMod",
            message_id=marker.get("message_id"),
            created_at=marker.get("created_at"),
            notification_kind=marker.get("notification_kind"),
        )
    members = db.list_channel_member_details(channel.id)
    latest = db.get_latest_channel_message(channel.id)
    summary = _serialize_channel_summary(channel, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    return summary


@router.post("/channels/{channel_id}/archive")
async def archive_channel(channel_id: str, cancel_open_tasks: bool = False):
    """Archive one shared thread so it leaves the active Threads list."""
    return await _archive_channel(channel_id, cancel_open_tasks=cancel_open_tasks)


@router.post("/channels/{channel_id}/reopen")
async def reopen_channel(channel_id: str):
    """Restore one archived thread to the active list and unseal the room."""
    try:
        opened = reopen_thread_as_operator(channel_id)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, "Thread not found") from exc
        raise
    members = db.list_channel_member_details(opened.id)
    latest = db.get_latest_channel_message(opened.id)
    summary = _serialize_channel_summary(opened, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_reopened",
        detail=f'Reopened thread "{opened.name}"',
        agent_name=None,
    )
    return summary


@router.get("/channels/{channel_id}/open-tasks")
async def list_channel_open_tasks(channel_id: str):
    """Return non-terminal tasks whose origin is this thread. Archive never infers this."""
    channel = db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "Thread not found")
    tasks = list_open_origin_tasks_for_channel(channel.id)
    return {
        "count": len(tasks),
        "tasks": [task.model_dump(mode="json") for task in tasks],
    }


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Archive one shared thread (soft delete). Does not cancel origin tasks."""
    return await _archive_channel(channel_id, cancel_open_tasks=False)


async def _archive_channel(channel_id: str, *, cancel_open_tasks: bool = False) -> dict[str, object]:
    try:
        archived, _posted_lines = await archive_thread_as_operator(
            channel_id,
            cancel_open_tasks=cancel_open_tasks,
            services=runtime_services,
            on_before_seal=_broadcast_archive_side_effects,
        )
    except IllegalTaskTransition as exc:
        raise HTTPException(
            409,
            f"Illegal task status transition: {exc.from_status} → {exc.to_status}",
        ) from exc
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, "Thread not found") from exc
        raise
    members = db.list_channel_member_details(archived.id)
    latest = db.get_latest_channel_message(archived.id)
    summary = _serialize_channel_summary(archived, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_archived",
        detail=f'Archived thread "{archived.name}"',
        agent_name=None,
    )
    if cancel_open_tasks:
        await manager.broadcast_world_state()
    return summary


async def _broadcast_archive_side_effects(posted_lines: list[dict[str, object]]) -> None:
    """Paint cancel/archive transcript lines while the thread is still writable."""
    for posted in posted_lines:
        extra = posted.get("channel_message") if isinstance(posted, dict) else None
        if extra:
            await manager.broadcast_channel_message(
                channel_id=extra["channel_id"],
                content=extra["content"],
                author_type=extra.get("author_type") or "system",
                author_name=extra.get("author_name") or "BossMod",
                message_id=extra.get("message_id"),
                created_at=extra.get("created_at"),
                notification_kind=extra.get("notification_kind"),
            )
        chat = posted.get("chat_message") if isinstance(posted, dict) else None
        if chat:
            await manager.broadcast_chat_message(
                agent_id=chat["agent_id"],
                content=chat["content"],
                from_type=chat.get("from_type") or "system",
                from_name=chat.get("from_name") or "BossMod",
                message_type=chat.get("message_type"),
                message_id=chat.get("message_id"),
                created_at=chat.get("created_at"),
                notification_kind=chat.get("notification_kind"),
            )
        # Only an agent delete that ended a hosted meeting produces this: the
        # meeting is painted per attendee conversation, so once per agent.
        meeting = posted.get("meeting_message") if isinstance(posted, dict) else None
        if meeting:
            for agent_id in posted["agent_ids"]:
                await manager.broadcast_meeting_message(agent_id=agent_id, **meeting)


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str, limit: int = 80):
    """Return one shared channel with roster and transcript."""
    channel = db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "Channel not found")

    rows = db.list_channel_messages(channel.id, limit=limit)
    attachments = get_attachments_for_messages([item.id for item in rows])
    messages = [
        _serialize_channel_message(item, attachments[item.id])
        for item in rows
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
    if not content and not body.attachment_ids:
        raise HTTPException(400, "Channel message content cannot be empty")

    try:
        result = await route_human_channel_message(
            channel_id=channel.id,
            channel_name=channel.name,
            content=content,
            from_name="Human Operator",
            broadcast_manager=manager,
            services=runtime_services,
            attachment_ids=body.attachment_ids,
        )
    except AttachmentLinkError as exc:
        raise HTTPException(422, _attachment_link_detail(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    message = result["message"]
    refreshed_channel = db.get_channel(channel.id) or channel
    await manager.broadcast_channel_updated(
        _serialize_channel_summary(refreshed_channel, members=result["members"], latest_message=message)
    )
    return {
        "status": "ok",
        "message": _serialize_channel_message(
            message, get_attachments_for_messages([message.id])[message.id],
        ),
        "member_count": len(result["members"]),
    }


@router.post("/channels/{channel_id}/members")
async def seat_channel_member(channel_id: str, body: ChannelMemberBody):
    """Seat one live agent into an existing thread. History is not rewritten."""
    try:
        seated = seat_agent_in_thread(channel_id, body.agent_id)
    except ThreadSeatError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    members = db.list_channel_member_details(seated.id)
    latest = db.get_latest_channel_message(seated.id)
    summary = _serialize_channel_summary(seated, members=members, latest_message=latest)
    await manager.broadcast_channel_updated(summary)
    await manager.broadcast_activity(
        event="channel_member_seated",
        detail=f'Seated a teammate in "{seated.name}"',
        agent_name=None,
    )
    return summary


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


@router.put("/agents/{agent_id}/desk")
async def save_agent_desk_file(agent_id: str, body: AgentDeskSaveBody) -> dict[str, object]:
    """Write an operator's edit back to an existing file on an agent's desk.

    The path is agent-virtual (``/me/...``, ``/projects/...``) and resolves in
    the agent's own namespace, the same one the desk GET reads from. The write
    goes through the CLI's ``write_virtual_text`` so it gets the CLI size cap,
    write normalization and the ``/me`` auto-commit: an operator edit shows up
    in the agent's workspace history like any other write.

    Args:
        agent_id: The agent whose desk holds the file.
        body: ``path`` (agent-virtual) and the full new ``content``. Empty
            content is allowed; clearing a file is a legitimate edit.

    Returns:
        ``{"status": "ok", "path": <virtual path>, "commit_sha": <sha|None>}``.

    Raises:
        HTTPException: 404 when the agent or the file does not exist (save
            edits an existing file and never creates one); 400 when the path
            is outside the agent's roots or the write is refused, for example
            over the CLI write size limit.
    """
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    try:
        resolved = resolve_cli_path(agent.storage_key, "/", body.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if resolved.real_path is None or not resolved.exists or not resolved.real_path.is_file():
        raise HTTPException(404, "File not found")

    try:
        outcome = await asyncio.to_thread(
            write_virtual_text,
            agent,
            cwd="/",
            raw_path=resolved.virtual_path,
            content=body.content,
            allow_empty=True,
            reason=f"operator edit {resolved.virtual_path}",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {"status": "ok", "path": outcome.virtual_path, "commit_sha": outcome.commit_sha}


@router.get("/agents/{agent_id}/desk/raw")
async def get_agent_desk_file_raw(agent_id: str, path: str = Query(..., min_length=1)):
    """Return the raw bytes of one file on an agent's desk.

    The desk GET returns text previews only; the shared file viewer needs the
    bytes to preview a desk image. The path resolves in the agent's namespace
    through ``resolve_cli_path``, the same jail the desk GET uses.

    Args:
        agent_id: The agent whose desk holds the file.
        path: An agent-virtual path (``/me/...``, ``/projects/...``).

    Returns:
        A ``FileResponse`` typed from the suffix for known images, otherwise
        ``application/octet-stream``.

    Raises:
        HTTPException: 404 when the agent or the file does not exist; 400 when
            the path is outside the agent's roots or names a directory.
    """
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    try:
        resolved = resolve_cli_path(agent.storage_key, "/", path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if resolved.real_path is None or not resolved.exists:
        raise HTTPException(404, "File not found")
    if not resolved.real_path.is_file():
        raise HTTPException(400, "Path is not a file")

    real_path = resolved.real_path
    mime_type = _IMAGE_MIME_TYPES.get(real_path.suffix.lower(), "application/octet-stream")
    return FileResponse(str(real_path), media_type=mime_type)


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


def _pack_link_for_hire(template_id: str | None) -> dict[str, Any] | None:
    """Return the pack link a hire from ``template_id`` records, or ``None``.

    A ``catalog`` or ``url`` template links the new agent to its pack, keyed
    the way the template is (``pack_id`` / ``source_url``) so the link
    survives an uninstall and reinstall. The stored contract hash is the
    TEMPLATE's, not the submitted fields', so text edited in the form before
    hiring correctly reads as "edited" later. A ``local`` template has no pack
    to update from and links nothing.

    Raises:
        HTTPException: 400 ``Template not found`` for an unknown id.
    """
    if template_id is None:
        return None
    template = db.get_agent_template(template_id)
    if template is None:
        raise HTTPException(400, "Template not found")
    if template.source not in ("catalog", "url"):
        return None
    return {
        "pack_id": template.pack_id,
        "pack_source_url": template.source_url,
        "commit_sha": template.commit_sha,
        "commit_date": template.commit_date,
        "content_hash": template.content_hash,
        "contract_hash": template_contract_hash(template),
    }


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate) -> Agent:
    _validate_ai_choice(body.connection_id, body.thinking_social, body.thinking_work)
    pack_link = _pack_link_for_hire(body.template_id)
    # The same home-floor rule db.create_agent applies, so the desk is checked
    # against the floor the agent will actually be written onto.
    floor_id = (body.floor_id or "").strip() or LOBBY_ID
    desk_x, desk_y = _checked_desk(floor_id, _requested_desk(body.desk_x, body.desk_y))
    try:
        agent = agent_repository.create(
            name=body.name,
            role=body.role,
            description=body.description,
            done_fail_bar=body.done_fail_bar,
            communication=body.communication,
            color=body.color,
            desk_x=desk_x,
            desk_y=desk_y,
            connection_id=body.connection_id,
            thinking_social=body.thinking_social,
            thinking_work=body.thinking_work,
            floor_id=body.floor_id,
            pack_link=pack_link,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    place_agent_at_desk(agent.id, agent.desk_x, agent.desk_y)
    # Broadcast to all connected clients
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_created",
        detail=f"Agent \"{agent.name}\" created",
        agent_name=agent.name,
    )
    return agent


class VacationReturnBody(BaseModel):
    """The floor an agent on vacation comes back to."""

    floor_id: str


@router.post("/agents/{agent_id}/return")
async def return_agent_from_vacation(agent_id: str, body: VacationReturnBody) -> Agent:
    """Bring one agent back from vacation onto a floor.

    404 when the agent or the floor is missing, 409 when the agent is not
    on vacation.
    """
    from core.floors import bring_back

    try:
        agent = bring_back(agent_id, body.floor_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await manager.broadcast_world_state()
    return agent


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate) -> Agent:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    current = db.get_agent(agent_id)
    if not current:
        raise HTTPException(404, "Agent not found")
    if {"connection_id", "thinking_social", "thinking_work"} & fields.keys():
        _validate_ai_choice(
            fields.get("connection_id", current.connection_id),
            fields.get("thinking_social", current.thinking_social),
            fields.get("thinking_work", current.thinking_work),
        )
    next_desk_x = fields["desk_x"] if "desk_x" in fields else current.desk_x
    next_desk_y = fields["desk_y"] if "desk_y" in fields else current.desk_y
    # Checked only when the patch changes the desk, or the agent has none to
    # auto-assign: a name-only edit must never fail on the stored desk. A
    # vacationer is on no floor, so there is nothing to check its desk
    # against yet: the patch is stored as-is and bring_back reconciles it.
    desk_patched = "desk_x" in fields or "desk_y" in fields
    unassigned = current.desk_x is None or current.desk_y is None
    if current.floor_id is not None and (desk_patched or unassigned):
        assigned_x, assigned_y = _checked_desk(
            current.floor_id,
            _requested_desk(next_desk_x, next_desk_y),
            exclude_agent_id=agent_id,
        )
        if assigned_x != next_desk_x or assigned_y != next_desk_y:
            fields["desk_x"] = assigned_x
            fields["desk_y"] = assigned_y
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
        # The id, not the name: a rename makes the name stale for any client
        # whose roster has not caught up yet (world updates are coalesced).
        extra={"agent_id": agent.id},
    )
    return agent


def _linked_template(agent: Agent) -> AgentTemplate | None:
    """Return the installed template an agent's pack link names, or ``None``."""
    return db.find_agent_template(pack_id=agent.pack_id, source_url=agent.pack_source_url)


@router.get("/agents/{agent_id}/pack-status")
async def get_agent_pack_status(agent_id: str) -> dict[str, object]:
    """Whether an agent hired from a pack is behind its installed template.

    A local read only: the agent's pack link is compared with the template
    installed under the same natural key. No catalog fetch — the marketplace
    is where the catalog moves; the desk only says whether this agent has
    caught up with what is installed.

    Returns:
        ``{linked, pack_id, template_id, template_title, installed,
        current_date, available_date, available_content_hash,
        update_available, edited}``. ``linked`` is false (and everything else
        empty) for an agent not hired from a pack. The dates are ISO-8601
        committer dates of the version the agent was last written from and of
        the installed template's; ``current_date`` is ``None`` for a link made
        before dates were recorded. ``available_content_hash`` is what
        ``POST …/pack-update`` must send back as ``expected_content_hash`` and
        is never displayed; it and ``available_date`` are ``None`` unless
        ``update_available`` (``available_date`` also when the template
        predates recorded dates).

    Raises:
        HTTPException: 404 when the agent does not exist.
    """
    agent = db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if not (agent.pack_id or agent.pack_source_url):
        return {
            "linked": False,
            "pack_id": None,
            "template_id": None,
            "template_title": None,
            "installed": False,
            "current_date": None,
            "available_date": None,
            "available_content_hash": None,
            "update_available": False,
            "edited": False,
        }
    template = _linked_template(agent)
    update_available = template is not None and template.content_hash != agent.pack_content_hash
    return {
        "linked": True,
        "pack_id": agent.pack_id,
        "template_id": template.id if template is not None else None,
        "template_title": template.title if template is not None else None,
        "installed": template is not None,
        "current_date": agent.pack_commit_date.isoformat() if agent.pack_commit_date else None,
        "available_date": (
            template.commit_date.isoformat() if update_available and template.commit_date else None
        ),
        "available_content_hash": template.content_hash if update_available else None,
        "update_available": update_available,
        "edited": agent_is_edited(agent),
    }


class AgentPackUpdateBody(BaseModel):
    """The template version the operator reviewed on the desk."""

    expected_content_hash: str


@router.post("/agents/{agent_id}/pack-update")
async def update_agent_from_pack(agent_id: str, body: AgentPackUpdateBody) -> Agent:
    """Rewrite one agent's contract from its installed pack template.

    Overwrites description, done bar and communication only, and records the
    new pack version, in one transaction. Broadcasts like ``PATCH``.

    Failure modes: 404 when the agent does not exist; 409
    ``{"code": "not_linked"}`` for an agent not hired from a pack, 409
    ``template_not_installed`` when its pack's template is not installed, and
    409 ``stale_template`` when the installed template is no longer the
    version the operator saw (``expected_content_hash``) — re-read the status.
    """
    agent = db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if not (agent.pack_id or agent.pack_source_url):
        raise HTTPException(
            409, {"code": "not_linked", "message": "This agent was not hired from a pack."},
        )
    template = _linked_template(agent)
    if template is None:
        raise HTTPException(
            409,
            {"code": "template_not_installed", "message": "The pack this agent came from is not installed."},
        )
    if template.content_hash != body.expected_content_hash:
        raise HTTPException(
            409,
            {"code": "stale_template", "message": "The installed pack changed since you looked. Review it again."},
        )
    with db.transaction():
        updated = apply_template_to_agent(agent, template)
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="agent_updated",
        detail=f"Agent \"{updated.name}\" updated from pack {template.title}",
        agent_name=updated.name,
        extra={"agent_id": updated.id},
    )
    return updated


@router.patch("/agents/{agent_id}/cli-auto-approve")
async def set_agent_cli_auto_approve(agent_id: str, body: AgentCliAutoApproveBody) -> dict[str, object]:
    """Turn CLI auto-approve on or off for one agent's DM.

    The DM counterpart of ``set_channel_cli_auto_approve``. It also covers
    the agent's work with no origin thread (DM-assigned, Focus or scheduled
    tasks). Writes only that flag. Default policy, Soft-block, and Deny
    picks stay, and Global auto-approve, when on, still overrides it.

    Args:
        agent_id: The agent whose DM flag changes.
        body: ``{enabled: bool}``.

    Returns:
        The agent payload, with ``cli_auto_approve_dm`` and
        ``cli_auto_approve_global``.

    Raises:
        HTTPException: 404 when no agent has ``agent_id``.
        ConfigError: The Global auto-approve setting is missing or not a boolean.
    """
    if db.get_agent(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    updated = db.set_agent_cli_auto_approve_dm(agent_id, body.enabled)
    if updated is None:
        raise HTTPException(404, "Agent not found")
    return _serialize_agent(updated)


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
    """Delete every agent, their DB history, their artifact files, and their standing prefs.

    Each agent's live turn is cancelled first, so nothing writes while its
    rows and files go.
    """
    for agent in db.list_agents():
        await runtime_services.reset_agent_runtime(agent.id)
    deleted = agent_repository.delete_all()

    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="all_agents_deleted",
        detail=f"All {deleted} agent(s) deleted with artifacts",
    )
    return {"status": "ok", "deleted": deleted}


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    # Fetch name before deleting for the activity message
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Before any row changes: a live turn must not keep writing for an agent
    # that is being deleted.
    await runtime_services.reset_agent_runtime(agent_id)
    try:
        posted_lines = agent_repository.delete(agent_id)
    except LookupError as exc:
        raise HTTPException(404, "Agent not found") from exc

    # Same painting as a thread archive's task cancels: the origin lines of
    # the open tasks this delete cancelled, plus the "Meeting ended" line of
    # each meeting it hosted, to every other participant.
    await _broadcast_archive_side_effects(posted_lines)
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
                "task_id": item.task_id,
                "host_path_consent": (
                    db.get_consent_request(notification_links[item.id].target_path).as_card()
                    if item.id in notification_links
                    and notification_links[item.id].target_kind == "host_path_consent"
                    and db.get_consent_request(notification_links[item.id].target_path)
                    else None
                ),
                "cli_approval": (
                    db.get_cli_approval_request(notification_links[item.id].target_path).as_card()
                    if item.id in notification_links
                    and notification_links[item.id].target_kind == "cli_approval"
                    and db.get_cli_approval_request(notification_links[item.id].target_path)
                    else None
                ),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in notifications
        ]
    )
    formatted.sort(key=lambda item: item.get("created_at") or "")
    formatted = formatted[-limit:]

    # Notification rows have their own ids and never carry attachments; the
    # batch maps them to empty lists like any message without files.
    attachments = get_attachments_for_messages([msg["id"] for msg in formatted])

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
            "task_id": msg.get("task_id"),
            "host_path_consent": msg.get("host_path_consent"),
            "cli_approval": msg.get("cli_approval"),
            "attachments": [_serialize_attachment(att) for att in attachments[msg["id"]]],
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
            "cli_approval": (
                db.get_cli_approval_request(notification_links[item.id].target_path).as_card()
                if item.id in notification_links
                and notification_links[item.id].target_kind == "cli_approval"
                and db.get_cli_approval_request(notification_links[item.id].target_path)
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


def _serialize_attachment(att) -> dict[str, object]:
    """The client-facing fields of one linked attachment (never its disk path).

    ``company_path`` is the Files-browser path the chat opens the file with;
    None for a file stored outside the company root.
    """
    return {
        "id": att.id,
        "file_name": att.file_name,
        "file_size": att.file_size,
        "mime_type": att.mime_type,
        "preview_tier": att.preview_tier,
        "company_path": company_path(att.storage_path, company_root()),
    }


def _attachment_link_detail(exc: AttachmentLinkError) -> dict[str, object]:
    """The 422 body for a send whose attachments could not be linked."""
    return {"error": str(exc), "code": "ATTACHMENT_LINK", "missing_ids": exc.missing_ids}


def _serialize_channel_message(item, attachments) -> dict[str, object]:
    """Serialize one shared channel transcript message.

    Args:
        item: The channel message row.
        attachments: Its linked attachments, pre-fetched in one batch by the
            caller via ``get_attachments_for_messages``.

    Returns:
        The transcript dict the thread source renders.
    """
    consent_card = None
    consent_id = getattr(item, "consent_id", None)
    if consent_id:
        request = db.get_consent_request(consent_id)
        if request is not None:
            consent_card = request.as_card()
    approval_card = None
    approval_id = getattr(item, "approval_id", None)
    if approval_id:
        approval = db.get_cli_approval_request(approval_id)
        if approval is not None:
            approval_card = approval.as_card()
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
        "cli_approval": approval_card,
        "desk_path": getattr(item, "desk_path", None),
        "task_id": getattr(item, "task_id", None),
        "attachments": [_serialize_attachment(att) for att in attachments],
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
        "floor_id": item.get("floor_id"),
        "vacation_since": _iso_or_none(item.get("vacation_since")),
    }


def _serialize_agent(agent: Agent) -> dict[str, object]:
    """Serialize one agent for the single-agent routes.

    The Agent model's own JSON, plus ``cli_auto_approve_global`` so the DM's
    auto-approve switch can be greyed out without a second request, and
    ``connection`` — ``{id, name, model}`` of the linked AI connection, or
    None when unlinked or the connection is gone — for the desk and the
    form. Never a secret.

    Raises:
        ConfigError: The Global auto-approve setting is missing or not a boolean.
    """
    payload: dict[str, object] = agent.model_dump(mode="json")
    payload["cli_auto_approve_global"] = global_auto_approve_enabled()
    conn = db.get_connection_by_id(agent.connection_id) if agent.connection_id else None
    payload["connection"] = (
        {"id": conn.id, "name": conn.name, "model": conn.model} if conn is not None else None
    )
    return payload


def _iso_or_none(value: object) -> object:
    """A datetime as ISO text; anything else (None, a stored string) unchanged."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _serialize_channel_summary(
    channel,
    *,
    members: list[dict[str, object]] | None = None,
    latest_message=None,
    conversation_paused: bool | None = None,
    auto_approve_global: bool | None = None,
) -> dict[str, object]:
    """Serialize one shared channel summary for list and realtime updates.

    Args:
        channel: The channel.
        members: Its member rows (``list_channel_member_details`` shape).
        latest_message: Its newest message, or ``None``.
        conversation_paused: Whether the thread is in host Paused. ``None``
            reads it now; a list passes it from one batch read.
        auto_approve_global: Settings' global auto-approve. ``None`` reads it
            now; a list reads it once and passes it to every row.

    Raises:
        ConfigError: ``auto_approve_global`` is ``None`` and the setting is
            missing or invalid (``global_auto_approve_enabled``).
    """
    if conversation_paused is None:
        conversation_paused = is_thread_paused(channel.id)
    if auto_approve_global is None:
        auto_approve_global = global_auto_approve_enabled()
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
        "conversation_paused": conversation_paused,
        "cli_auto_approve": bool(getattr(channel, "cli_auto_approve", False)),
        # Global on overrides the thread flag; the header greys its switch.
        "cli_auto_approve_global": auto_approve_global,
        "floor_id": getattr(channel, "floor_id", None),
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
    attachment_ids = body.attachment_ids if body else None

    from core.floors import AgentOnVacation

    try:
        await route_human_dm(
            agent_id=agent_id,
            content=content,
            from_name="You",
            broadcast_manager=manager,
            services=runtime_services,
            attachment_ids=attachment_ids,
        )
    except AgentOnVacation as exc:
        raise HTTPException(409, str(exc)) from exc
    except AttachmentLinkError as exc:
        raise HTTPException(422, _attachment_link_detail(exc)) from exc

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
            posted = mirror_origin_status(
                task=task,
                agent=agent,
                kind="waiting",
                reason=reset_note,
            )
            if posted.get("channel_message"):
                extra = posted["channel_message"]
                await manager.broadcast_channel_message(
                    channel_id=extra["channel_id"],
                    content=extra["content"],
                    author_type=extra.get("author_type") or "system",
                    author_name=extra.get("author_name") or agent.name,
                    message_id=extra.get("message_id"),
                    created_at=extra.get("created_at"),
                    notification_kind=extra.get("notification_kind"),
                )
            seen_task_ids.add(task.id)
            blocked_task_ids.append(task.id)

    blocked_task_id = blocked_task_ids[0] if blocked_task_ids else None

    deleted_triggers = db.delete_open_triggers(agent_id)
    cancelled_activities = db.cancel_open_activities(agent_id, detail=reset_note)
    db.delete_agent_work_snapshots(agent_id)
    activity_runtime.refresh_agent_status(agent_id)
    from core.agent_loop.queue_visibility import emit_queue_visibility

    await emit_queue_visibility(agent_id)
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
