"""BossMod AI — Shared human message ingress for direct and channel chat.

Reusable functions for persisting, broadcasting, and triggering agent
responses to human messages. Used by both ``api/routes.py`` (web UI)
and ``integrations/telegram/bot.py`` (Telegram bot) so delivery logic
lives in exactly one place.
"""

from __future__ import annotations

from typing import Any

import db
from core.floors import AgentOnVacation, agent_id_on_vacation
from core.agent_loop.channel_host import prepare_human_channel_message
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.loop_breathing import off_request_loop
from core.agent_loop.thread_supersede import cancel_queued_older_thread_rounds
from core.models.message import HUMAN_SENDER_ID


async def route_human_dm(
    *,
    agent_id: str,
    content: str,
    from_name: str,
    trigger_from_name: str = "Human Operator",
    broadcast_manager: Any,
    services: Any,
) -> dict[str, Any]:
    """Persist a human DM, broadcast to WebSocket clients, enqueue an agent trigger.

    ``from_name`` is displayed to connected UI clients (e.g. "You" for the web UI).
    ``trigger_from_name`` is what the agent sees in its prompt context.

    Returns a dict with ``message_id``.

    Raises AgentOnVacation before anything is written when the agent is on
    vacation: a message they will never answer must not look delivered.
    """
    if agent_id_on_vacation(agent_id):
        raise AgentOnVacation()
    human_msg = db.create_message(
        from_agent=HUMAN_SENDER_ID,
        to_agent=agent_id,
        content=content,
        message_type="human",
    )
    await broadcast_manager.broadcast_chat_message(
        agent_id=agent_id,
        content=content,
        from_type="human",
        from_name=from_name,
        message_type="human",
        message_id=human_msg.id,
        created_at=human_msg.created_at,
    )
    await services.enqueue_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={
            "content": content,
            "from_name": trigger_from_name,
            "source_message_id": human_msg.id,
        },
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
) -> dict[str, Any]:
    """Persist a human channel message, broadcast, enqueue triggers for all members.

    Returns a dict with ``message_id``, ``round_id``, and ``members`` list.
    """
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name=from_name,
        content=content,
        source_channel="channel",
    )
    await broadcast_manager.broadcast_channel_message(
        channel_id=channel_id,
        content=message.content,
        author_type=message.author_type,
        author_name=message.author_name,
        message_id=message.id,
        created_at=message.created_at,
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
