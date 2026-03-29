"""BossMod AI — Shared human message ingress for direct and channel chat.

Reusable functions for persisting, broadcasting, and triggering agent
responses to human messages. Used by both ``api/routes.py`` (web UI)
and ``integrations/telegram/bot.py`` (Telegram bot) so delivery logic
lives in exactly one place.
"""

from __future__ import annotations

from typing import Any

import db
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
    """
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

    round_record = db.create_channel_response_round(
        channel_id=channel_id,
        source_message_id=message.id,
    )
    for member in members:
        agent_id = member.get("id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            continue
        db.create_channel_response_candidate(round_id=round_record.id, agent_id=agent_id)
        await services.enqueue_trigger(
            agent_id=agent_id,
            trigger_type="channel_message",
            source_channel="channel",
            payload={
                "content": message.content,
                "channel_id": channel_id,
                "round_id": round_record.id,
                "from_name": from_name,
                "author_type": "human",
                "source_message_id": message.id,
                "channel_name": channel_name,
            },
        )

    return {
        "message_id": message.id,
        "message": message,
        "round_id": round_record.id,
        "members": members,
    }
