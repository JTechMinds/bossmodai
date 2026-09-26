"""BossMod AI — Attachment upload/download/preview endpoints."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from core import config
from core.attachments import (
    BLOCKLIST,
    derive_storage_root,
    detect_mime_type,
    detect_preview_tier,
    get_file_extension,
    is_blocklisted,
    sanitize_file_name,
)
from db import attachments as db_att

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _get_company_root() -> str:
    """Resolve the company data root for attachment storage."""
    root = config.get("company_root")
    if not root:
        # Fallback: derive from the project's own location
        root = str(Path(__file__).resolve().parent.parent.parent)
    return root


def _get_max_size_mb() -> int:
    """Read the size cap from settings (not cached per spec §7)."""
    raw = config.get_live("bossmod.attach.max_size_mb")
    if raw is None:
        return 10
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 10


@router.post("/upload", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    message_context: str = Form(...),
    original_name: str = Form(...),
) -> dict[str, Any]:
    """Upload a file attachment. Validates, stores on disk, inserts DB row."""
    # Parse context
    try:
        ctx = json.loads(message_context)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail={"error": "Invalid message_context JSON", "code": "BAD_CONTEXT"})

    ctx_type = ctx.get("type", "")
    ctx_id = ctx.get("id", "")
    valid_types = {"thread", "direct", "channel", "unscoped"}
    if ctx_type not in valid_types:
        raise HTTPException(status_code=400, detail={"error": f"Invalid context type: {ctx_type}", "code": "BAD_CONTEXT"})

    # Validate file name
    safe_name = sanitize_file_name(original_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail={"error": "Invalid file name", "code": "INVALID_NAME"})

    ext = get_file_extension(original_name)

    # Blocklist check
    if is_blocklisted(ext):
        raise HTTPException(status_code=415, detail={"error": f"File type .{ext} is not allowed", "code": "BLOCKLISTED"})

    # Read file content
    content = await file.read()
    file_size = len(content)

    # Size cap check (server-side, not trusting client)
    max_bytes = _get_max_size_mb() * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(status_code=413, detail={"error": f"File exceeds {max_bytes // (1024*1024)} MB limit", "code": "SIZE_EXCEEDED"})

    # Derive storage path
    base_dir = _get_company_root()
    storage_root = derive_storage_root(ctx_type, ctx_id, base_dir)
    os.makedirs(storage_root, exist_ok=True)

    # Write file to disk
    disk_name = f"{uuid.uuid4()}_{safe_name}"
    storage_path = os.path.join(storage_root, disk_name)
    try:
        with open(storage_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to write file: {e}", "code": "READ_FAILED"})

    # DB row (message_id = "pending" until send)
    mime = detect_mime_type(ext)
    tier = detect_preview_tier(ext)
    att = db_att.create_attachment(
        message_id="pending",
        file_name=safe_name,
        file_size=file_size,
        mime_type=mime,
        storage_path=storage_path,
        preview_tier=tier,
    )

    return {
        "id": att.id,
        "file_name": att.file_name,
        "file_size": att.file_size,
        "mime_type": att.mime_type,
        "storage_path": att.storage_path,
        "preview_tier": att.preview_tier,
    }


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
    )
