"""Operator archive: seal the thread, deny origin consent, optionally cancel work."""

from __future__ import annotations

from typing import Any

import db
from core.bm_cli.host_path_consent import resume_host_path_consent
from core.models import Channel
from core.models.channel import THREAD_ARCHIVED_CANCEL_LINE, THREAD_ARCHIVED_CONSENT_DENY
from core.tasking.service import cancel_tasks_as_operator, list_open_origin_tasks_for_channel


async def archive_thread_as_operator(
    channel_id: str,
    *,
    cancel_open_tasks: bool,
    services: Any,
    on_before_seal: Any = None,
) -> tuple[Channel, list[dict[str, Any]]]:
    """Seal one thread. Archive-only leaves tasks; cancel also kills origin work.

    Always denies pending host-path consent on this origin and drops queued
    channel-bound triggers so nothing new lands in the archived room.
    ``on_before_seal`` runs after transcript writes and before the status flip
    so live clients can paint the last lines.
    """
    channel = db.get_channel(channel_id)
    if channel is None:
        raise ValueError("Thread not found")
    if channel.status == "archived":
        return channel, []

    posted_lines: list[dict[str, Any]] = []
    cancelled_any = False
    if cancel_open_tasks:
        open_tasks = list_open_origin_tasks_for_channel(channel.id)
        if open_tasks:
            _cancelled, posted_lines = cancel_tasks_as_operator([task.id for task in open_tasks])
            cancelled_any = True

    await deny_pending_consent_for_archived_channel(
        channel.id,
        services=services,
        wake=not cancel_open_tasks,
    )

    if cancelled_any:
        system_line = _post_archive_cancel_line(channel.id)
        if system_line:
            posted_lines.append({"channel_message": system_line})

    db.delete_queued_triggers_for_channel(channel.id)
    if on_before_seal is not None:
        await on_before_seal(posted_lines)
    archived = db.archive_channel(channel.id)
    if archived is None:
        raise ValueError("Thread not found")
    return archived, posted_lines


def reopen_thread_as_operator(channel_id: str) -> Channel:
    """Put one archived thread back on the active list and unseal the room."""
    channel = db.get_channel(channel_id)
    if channel is None:
        raise ValueError("Thread not found")
    opened = db.reopen_channel(channel.id)
    if opened is None:
        raise ValueError("Thread not found")
    return opened


async def deny_pending_consent_for_archived_channel(
    channel_id: str,
    *,
    services: Any,
    wake: bool,
) -> list[Any]:
    """Resolve pending origin-thread consent with the locked archived deny copy."""
    resolved: list[Any] = []
    for request in db.list_pending_consent_for_channel(channel_id):
        updated = await resume_host_path_consent(
            request.id,
            decision="deny",
            services=services,
            decision_by="system",
            note=THREAD_ARCHIVED_CONSENT_DENY,
            enqueue_resume=wake,
            omit_origin_channel=True,
        )
        if updated is not None:
            resolved.append(updated)
    return resolved


def _post_archive_cancel_line(channel_id: str) -> dict[str, Any]:
    """Persist the locked Cancel-tasks-&-archive system line before the seal."""
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="system",
        author_name="BossMod",
        content=THREAD_ARCHIVED_CANCEL_LINE,
        source_channel="channel",
        notification_kind="task_update",
    )
    return {
        "channel_id": channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": message.author_name or "BossMod",
        "message_id": message.id,
        "created_at": message.created_at,
        "notification_kind": message.notification_kind,
    }
