"""BossMod AI — Attachment upload/download/preview endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

import db
from core import config
from core.attachments import (
    ContextType,
    detect_mime_type,
    detect_preview_tier,
    get_file_extension,
    is_blocklisted,
    sanitize_file_name,
    storage_dir,
)
from core.bm_cli.floor_roots import floor_root
from core.floors import VACATION_DENY
from core.models import Attachment
from db import attachments as db_att

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attachments", tags=["attachments"])

# Read size per upload chunk. Bounds memory while the size cap is enforced;
# it is an I/O granularity, not a policy limit.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

# The browser must never re-sniff a stored file into something executable.
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


class UploadContext(BaseModel):
    """The conversation an upload is for, as the composer sends it."""

    type: ContextType
    id: str = Field(min_length=1)


def _bad_context(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message, "code": "BAD_CONTEXT"})


def _conversation_floor(ctx: UploadContext) -> str:
    """Resolve the floor that owns a conversation, from the database.

    The client-supplied id is only trusted once it has matched a real agent
    or channel row, which is what keeps it safe to use as a path component.

    Args:
        ctx: The validated upload context.

    Returns:
        The floor id of the agent (``direct``) or thread (``thread``).

    Raises:
        HTTPException: 404 when no such live conversation exists or it has no
            floor; 409 when the agent is on vacation.
    """
    if ctx.type == "direct":
        agent = db.get_agent(ctx.id)
        if agent is None:
            raise HTTPException(404, {"error": "Agent not found", "code": "NOT_FOUND"})
        if not agent.floor_id:
            raise HTTPException(409, {"error": VACATION_DENY, "code": "ON_VACATION"})
        return agent.floor_id
    channel = db.get_channel(ctx.id)
    if channel is None or channel.status != "active" or not channel.floor_id:
        raise HTTPException(404, {"error": "Thread not found", "code": "NOT_FOUND"})
    return channel.floor_id


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload in chunks, refusing it as soon as it passes ``max_bytes``.

    Raises:
        HTTPException: 413 once more than ``max_bytes`` has arrived.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": f"File exceeds {max_bytes // (1024 * 1024)} MB limit",
                    "code": "SIZE_EXCEEDED",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _public(att: Attachment) -> dict[str, Any]:
    """The client-facing fields of one attachment. The disk path never leaves."""
    return {
        "id": att.id,
        "file_name": att.file_name,
        "file_size": att.file_size,
        "mime_type": att.mime_type,
        "preview_tier": att.preview_tier,
    }


@router.post("/upload", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    message_context: str = Form(...),
    original_name: str = Form(...),
) -> dict[str, Any]:
    """Store one upload for a conversation as a pending attachment.

    The file is written under the conversation's floor at
    ``.attachments/<direct|thread>/<id>/`` and stays pending until a send
    links it to a message.

    Args:
        file: The uploaded bytes.
        message_context: JSON ``{"type": "direct"|"thread", "id": <agent or thread id>}``.
        original_name: The operator's file name; sanitized before use.

    Returns:
        ``{id, file_name, file_size, mime_type, preview_tier}``.

    Raises:
        HTTPException: 400 bad context or name; 404 unknown conversation;
            409 agent on vacation; 413 over ``bossmod.attach.max_size_mb``;
            415 blocklisted type; 500 when the file cannot be written.
    """
    try:
        ctx = UploadContext.model_validate_json(message_context)
    except ValidationError as exc:
        raise _bad_context(f"Invalid message_context: {exc.errors()[0]['msg']}") from exc

    safe_name = sanitize_file_name(original_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail={"error": "Invalid file name", "code": "INVALID_NAME"})

    ext = get_file_extension(original_name)
    if is_blocklisted(ext):
        raise HTTPException(status_code=415, detail={"error": f"File type .{ext} is not allowed", "code": "BLOCKLISTED"})

    floor_id = _conversation_floor(ctx)

    max_bytes = config.require_int("bossmod.attach.max_size_mb") * 1024 * 1024
    content = await _read_capped(file, max_bytes)

    target_dir = storage_dir(floor_root(floor_id), ctx.type, ctx.id)
    disk_name = f"{uuid.uuid4()}_{safe_name}"
    storage_path = target_dir / disk_name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
    except OSError as exc:
        logger.error("Attachment write failed at %s: %s", storage_path, exc)
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to store the file", "code": "READ_FAILED"},
        ) from exc

    att = db_att.create_attachment(
        message_id=db_att.PENDING_MESSAGE_ID,
        file_name=safe_name,
        file_size=len(content),
        mime_type=detect_mime_type(ext),
        storage_path=str(storage_path),
        preview_tier=detect_preview_tier(ext),
        context_type=ctx.type,
        context_id=ctx.id,
    )
    return _public(att)


@router.get("/limits")
async def attachment_limits() -> dict[str, int]:
    """Return the operator-set upload limits the composer enforces up front.

    Returns:
        ``{max_size_mb, max_per_message}``.

    Raises:
        core.config.ConfigError: A limit setting is missing or not an integer.
    """
    return {
        "max_size_mb": config.require_int("bossmod.attach.max_size_mb"),
        "max_per_message": config.require_int("bossmod.attach.max_per_message"),
    }


@router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: str) -> Response:
    """Discard a pending upload the operator removed before sending.

    Args:
        attachment_id: The upload's id.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 unknown id; 409 already linked to a message
            (sent files are part of the transcript); 500 when the file exists
            but cannot be removed.
    """
    att = db_att.get_attachment_by_id(attachment_id)
    if att is None:
        raise HTTPException(404, {"error": "Attachment not found", "code": "NOT_FOUND"})
    removed = db_att.delete_pending_attachment(attachment_id)
    if removed is None:
        raise HTTPException(409, {"error": "Attachment was already sent", "code": "ALREADY_LINKED"})
    try:
        os.remove(removed.storage_path)
    except FileNotFoundError:
        logger.warning("Pending attachment %s had no file at %s", removed.id, removed.storage_path)
    except OSError as exc:
        logger.error("Could not remove attachment file %s: %s", removed.storage_path, exc)
        raise HTTPException(
            500, {"error": "The file could not be removed", "code": "DELETE_FAILED"},
        ) from exc
    return Response(status_code=204)


@router.get("/{attachment_id}")
async def download_attachment(attachment_id: str) -> FileResponse:
    """Download a stored attachment file."""
    att = db_att.get_attachment_by_id(attachment_id)
    if att is None or not os.path.isfile(att.storage_path):
        raise HTTPException(status_code=404, detail={"error": "Attachment not found", "code": "NOT_FOUND"})
    return FileResponse(
        path=att.storage_path,
        media_type=att.mime_type,
        filename=att.file_name,
        headers=_NOSNIFF,
    )


@router.get("/{attachment_id}/preview")
async def preview_attachment(attachment_id: str) -> Response:
    """Return image preview for T1 files; 204 for others."""
    att = db_att.get_attachment_by_id(attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail={"error": "Attachment not found", "code": "NOT_FOUND"})
    if att.preview_tier != "image" or not os.path.isfile(att.storage_path):
        return Response(status_code=204)
    return FileResponse(
        path=att.storage_path,
        media_type=att.mime_type,
        headers=_NOSNIFF,
    )
