"""BossMod AI — Shared human message ingress for direct and channel chat.

Reusable functions for persisting, broadcasting, and triggering agent
responses to human messages. Used by both ``api/routes.py`` (web UI)
and ``integrations/telegram/bot.py`` (Telegram bot) so delivery logic
lives in exactly one place.
"""

from __future__ import annotations

from typing import Any

import db
from core import config
from core.floors import AgentOnVacation, agent_id_on_vacation
from core.agent_loop.channel_host import prepare_human_channel_message
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.loop_breathing import off_request_loop
from core.agent_loop.thread_supersede import cancel_queued_older_thread_rounds
from core.models.message import HUMAN_SENDER_ID
from db import attachments as db_att
from db.connection import transaction


def _link_attachments(
    attachment_ids: list[str],
    message_id: str,
    *,
    context_type: str,
    context_id: str,
) -> list[dict[str, Any]] | None:
    """Link a send's pending uploads to its message and serialize them.

    Must run inside the transaction that inserted the message, so a refused
    link rolls the message back with it.

    Args:
        attachment_ids: Ids from the composer; empty when the send has none.
        message_id: The message just inserted.
        context_type: ``direct`` or ``thread``.
        context_id: The agent id or channel id the message went to.

    Returns:
        The client-facing attachment dicts, or None when nothing was attached.

    Raises:
        AttachmentLinkError: Over ``bossmod.attach.max_per_message``, or any id
            is unknown, already sent, or from another conversation.
    """
    if not attachment_ids:
        return None
    limit = config.require_int("bossmod.attach.max_per_message")
    if len(set(attachment_ids)) > limit:
        raise db_att.AttachmentLinkError(f"At most {limit} attachments per message.")
    linked = db_att.link_pending_attachments(
        attachment_ids,
        message_id,
        context_type=context_type,
        context_id=context_id,
    )
    return [
        {"id": a.id, "file_name": a.file_name, "file_size": a.file_size,
         "mime_type": a.mime_type, "preview_tier": a.preview_tier}
        for a in linked
    ]


def _with_attachment_ids(
    payload: dict[str, Any],
    attachments: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Add the linked ids to a trigger payload so the turn can expand them."""
    if attachments:
        payload["attachment_ids"] = [a["id"] for a in attachments]
    return payload


async def route_human_dm(
    *,
    agent_id: str,
    content: str,
    from_name: str,
    trigger_from_name: str = "Human Operator",
    broadcast_manager: Any,
    services: Any,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a human DM, broadcast to WebSocket clients, enqueue an agent trigger.

    ``from_name`` is displayed to connected UI clients (e.g. "You" for the web UI).
    ``trigger_from_name`` is what the agent sees in its prompt context.

    ``attachment_ids`` are pending uploads made for this agent's DM. They
    are linked in the same transaction as the message and their ids ride on
    the trigger payload so the turn can hand them to the model.

    Returns a dict with ``message_id``.

    Raises AgentOnVacation before anything is written when the agent is on
    vacation: a message they will never answer must not look delivered.
    Raises AttachmentLinkError, with nothing written, broadcast or enqueued,
    when the attachments cannot all be linked.
    """
    if agent_id_on_vacation(agent_id):
        raise AgentOnVacation()
    # Validate and link before anything is broadcast or enqueued: a refused
    # link must leave neither a message row nor a wake behind.
    with transaction():
        human_msg = db.create_message(
            from_agent=HUMAN_SENDER_ID,
            to_agent=agent_id,
            content=content,
            message_type="human",
        )
        attachments = _link_attachments(
            attachment_ids or [],
            human_msg.id,
            context_type="direct",
            context_id=agent_id,
        )
    await broadcast_manager.broadcast_chat_message(
        agent_id=agent_id,
        content=content,
        from_type="human",
        from_name=from_name,
        message_type="human",
        message_id=human_msg.id,
        created_at=human_msg.created_at,
        attachments=attachments,
    )
    await services.enqueue_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload=_with_attachment_ids(
            {
                "content": content,
                "from_name": trigger_from_name,
                "source_message_id": human_msg.id,
            },
            attachments,
        ),
    )
    return {
        "message_id": human_msg.id,
        "routed_as": "human_chat",
    }


async def route_human_channel_message(
    *,
    channel_id: str,
    channel_name: str,
    content: str,
    from_name: str,
    broadcast_manager: Any,
    services: Any,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a human channel message, broadcast, enqueue triggers for all members.

    ``attachment_ids`` are pending uploads made for this thread; they are
    linked in the same transaction as the message and carried on every wake.

    Returns a dict with ``message_id``, ``round_id``, and ``members`` list.

    Raises AttachmentLinkError, with nothing written, broadcast or enqueued,
    when the attachments cannot all be linked.
    """
    # Same ordering as a DM: nothing is broadcast or woken for a refused link.
    with transaction():
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="human",
            author_name=from_name,
            content=content,
            source_channel="channel",
        )
        attachments = _link_attachments(
            attachment_ids or [],
            message.id,
            context_type="thread",
            context_id=channel_id,
        )
    await broadcast_manager.broadcast_channel_message(
        channel_id=channel_id,
        content=message.content,
        author_type=message.author_type,
        author_name=message.author_name,
        message_id=message.id,
        created_at=message.created_at,
        attachments=attachments,
    )

    members = db.list_channel_member_details(channel_id)
    if not members:
        raise ValueError("Channel has no members")

    cancel_queued_older_thread_rounds(
        channel_id=channel_id,
        keep_source_message_id=message.id,
    )
    prepared = prepare_human_channel_message(channel_id, message.content)
    if prepared.marker:
        marker = prepared.marker
        await broadcast_manager.broadcast_channel_message(
            channel_id=marker["channel_id"],
            content=marker["content"],
            author_type=marker.get("author_type") or "system",
            author_name=marker.get("author_name") or "BossMod",
            message_id=marker.get("message_id"),
            created_at=marker.get("created_at"),
            notification_kind=marker.get("notification_kind"),
        )
    trigger_requests = (
        await off_request_loop(
            start_channel_peer_round,
            channel_id=channel_id,
            message_id=message.id,
            content=message.content,
            from_name=from_name,
            author_type="human",
            channel_name=channel_name,
            attachment_ids=[a["id"] for a in attachments] if attachments else None,
        )
        if prepared.allow_round
        else []
    )
    for request in trigger_requests:
        await services.enqueue_trigger(
            agent_id=request["agent_id"],
            trigger_type=request["trigger_type"],
            source_channel=request["source_channel"],
            payload=request["payload"],
        )

    round_id = ""
    if trigger_requests:
        round_id = str(trigger_requests[0].get("payload", {}).get("round_id") or "")

    return {
        "message_id": message.id,
        "message": message,
        "round_id": round_id,
        "members": members,
    }
