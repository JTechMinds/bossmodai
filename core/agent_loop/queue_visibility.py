"""Event-driven in-thread queue visibility.

One agent has one turn queue. When that agent is working and more turns
wait behind the current one, the origin thread (or Focus/DM) shows Debra's
locked line ``Busy — {N} queued``. Depth changes replace the same line.
Empty queue or idle clears it. No heartbeat; no parallel execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

import db
from core.agent_loop import activity_runtime
from core.agent_loop.task_origin_mirrors import origin_thread_target
from core.models import Agent
from core.models.channel import ChannelArchivedError

logger = logging.getLogger(__name__)

QUEUE_VISIBILITY_KIND = "queue_visibility"
BUSY_QUEUED_PREFIX = "Busy — "

VisibilityTarget = Literal["channel", "chat"]


def format_busy_queued_line(depth: int) -> str:
    """Return Debra's locked backlog one-liner."""
    return f"{BUSY_QUEUED_PREFIX}{max(int(depth), 0)} queued"


def waiting_queue_depth(agent_id: str) -> int:
    """Return how many turns wait behind the current one."""
    return db.count_waiting_triggers(agent_id)


def agent_is_busy(agent_id: str) -> bool:
    """Return whether the agent is mid-work and can have a visible backlog.

    Claimed trigger is the in-flight turn. Non-idle status covers a live
    activity whose trigger row has not been claimed yet this tick.
    """
    if db.has_claimed_trigger(agent_id):
        return True
    state = db.get_agent_state(agent_id)
    return bool(state and state.status and state.status != "idle")


def resolve_visibility_target(agent_id: str) -> tuple[VisibilityTarget, str | None]:
    """Pick the origin thread if work started there, else Focus/DM."""
    task_id = activity_runtime.get_active_task_id(agent_id)
    if task_id:
        task = db.get_task(task_id)
        if origin_thread_target(task) == "channel":
            channel_id = str(getattr(task, "notification_channel_id", "") or "").strip()
            if channel_id and not db.is_channel_archived(channel_id):
                return "channel", channel_id

    for status in ("claimed", "queued"):
        for row in db.list_agent_triggers(agent_id, status=status, limit=20):
            target = _target_from_trigger_row(row)
            if target is not None:
                return target
    return "chat", None


def sync_queue_visibility(agent_id: str) -> dict[str, Any]:
    """Upsert or clear the live Busy line. No broadcast."""
    agent = db.get_agent(agent_id)
    if agent is None:
        return {"action": "noop", "agent_id": agent_id, "depth": 0}

    depth = waiting_queue_depth(agent_id)
    busy = agent_is_busy(agent_id)
    if not busy or depth <= 0:
        return _clear_lines(agent)

    target, channel_id = resolve_visibility_target(agent_id)
    content = format_busy_queued_line(depth)
    if target == "channel" and channel_id:
        posted = _upsert_channel_line(agent, channel_id, content)
        if posted:
            _clear_chat_line(agent.id)
            _clear_other_channel_lines(agent.id, keep_channel_id=channel_id)
            return {
                "action": posted["action"],
                "agent_id": agent.id,
                "depth": depth,
                "content": content,
                "target": "channel",
                **posted,
            }
    posted = _upsert_chat_line(agent, content)
    _clear_other_channel_lines(agent.id, keep_channel_id=None)
    return {
        "action": posted["action"],
        "agent_id": agent.id,
        "depth": depth,
        "content": content,
        "target": "chat",
        **posted,
    }


def schedule_queue_visibility(agent_id: str) -> dict[str, Any]:
    """Persist a depth change and broadcast when a loop is running."""
    change = sync_queue_visibility(agent_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return change
    if change.get("action") not in {None, "noop"}:
        loop.create_task(broadcast_queue_visibility(change))
    return change


async def emit_queue_visibility(agent_id: str) -> dict[str, Any]:
    """Persist a depth change and broadcast it."""
    change = sync_queue_visibility(agent_id)
    await broadcast_queue_visibility(change)
    return change


async def broadcast_queue_visibility(change: dict[str, Any]) -> None:
    """Fan out an upsert or clear to the origin thread or Focus/DM."""
    from core.runtime.events import runtime_events as manager

    action = change.get("action")
    if action in {None, "noop"}:
        return
    content = str(change.get("content") or "")
    if change.get("target") == "channel" and change.get("channel_id"):
        await manager.broadcast_channel_message(
            channel_id=str(change["channel_id"]),
            content=content,
            author_type="system",
            author_name=str(change.get("author_name") or "system"),
            message_id=change.get("message_id"),
            created_at=change.get("created_at"),
            notification_kind=QUEUE_VISIBILITY_KIND,
            author_agent_id=change.get("agent_id"),
        )
        return
    agent_id = change.get("agent_id")
    if not agent_id:
        return
    await manager.broadcast_chat_message(
        agent_id=str(agent_id),
        content=content,
        from_type="system",
        from_name=str(change.get("from_name") or change.get("author_name") or "system"),
        message_type="system",
        message_id=change.get("message_id"),
        created_at=change.get("created_at"),
        notification_kind=QUEUE_VISIBILITY_KIND,
    )


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _target_from_trigger_row(row: dict[str, Any]) -> tuple[VisibilityTarget, str] | None:
    payload = _parse_payload(row.get("payload"))
    channel_id = payload.get("channel_id")
    if isinstance(channel_id, str) and channel_id.strip() and not db.is_channel_archived(channel_id.strip()):
        return "channel", channel_id.strip()
    task_id = row.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    task = db.get_task(task_id)
    if origin_thread_target(task) != "channel":
        return None
    origin = str(getattr(task, "notification_channel_id", "") or "").strip()
    if origin and not db.is_channel_archived(origin):
        return "channel", origin
    return None


def _upsert_chat_line(agent: Agent, content: str) -> dict[str, Any]:
    existing = db.get_queue_visibility_notification(agent.id)
    if existing is not None:
        if (existing.content or "").strip() == content:
            return {
                "action": "noop",
                "message_id": existing.id,
                "created_at": existing.created_at,
                "from_name": agent.name,
            }
        updated = db.update_notification_content(existing.id, content)
        row = updated or existing
        return {
            "action": "upsert",
            "message_id": row.id,
            "created_at": row.created_at,
            "from_name": agent.name,
        }
    stored = db.create_notification(
        agent_id=agent.id,
        kind=QUEUE_VISIBILITY_KIND,
        content=content,
        source_channel="chat",
        policy="all",
        chat_visible=True,
        prompt_visibility=False,
    )
    return {
        "action": "upsert",
        "message_id": stored.id,
        "created_at": stored.created_at,
        "from_name": agent.name,
    }


def _upsert_channel_line(agent: Agent, channel_id: str, content: str) -> dict[str, Any]:
    if db.is_channel_archived(channel_id):
        return {}
    existing = db.find_queue_visibility_channel_message(agent_id=agent.id, channel_id=channel_id)
    if existing is not None:
        if (existing.content or "").strip() == content:
            return {
                "action": "noop",
                "channel_id": existing.channel_id,
                "message_id": existing.id,
                "created_at": existing.created_at,
                "author_name": existing.author_name,
            }
        updated = db.update_channel_message_content(existing.id, content)
        row = updated or existing
        return {
            "action": "upsert",
            "channel_id": row.channel_id,
            "message_id": row.id,
            "created_at": row.created_at,
            "author_name": row.author_name,
        }
    try:
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="system",
            author_name=agent.name,
            content=content,
            source_channel="channel",
            author_agent_id=agent.id,
            notification_kind=QUEUE_VISIBILITY_KIND,
        )
    except ChannelArchivedError:
        return {}
    return {
        "action": "upsert",
        "channel_id": message.channel_id,
        "message_id": message.id,
        "created_at": message.created_at,
        "author_name": message.author_name,
    }


def _clear_chat_line(agent_id: str) -> dict[str, Any] | None:
    existing = db.get_queue_visibility_notification(agent_id)
    if existing is None:
        return None
    db.delete_notification(existing.id)
    return existing


def _clear_other_channel_lines(agent_id: str, *, keep_channel_id: str | None) -> None:
    for row in db.list_queue_visibility_channel_messages(agent_id):
        if keep_channel_id and row.channel_id == keep_channel_id:
            continue
        db.delete_channel_message(row.id)


def _clear_lines(agent: Agent) -> dict[str, Any]:
    """Remove every live Busy line for this agent."""
    chat = _clear_chat_line(agent.id)
    channels = list(db.list_queue_visibility_channel_messages(agent.id))
    for row in channels:
        db.delete_channel_message(row.id)
    if channels:
        row = channels[0]
        return {
            "action": "clear",
            "agent_id": agent.id,
            "depth": 0,
            "content": "",
            "target": "channel",
            "channel_id": row.channel_id,
            "message_id": row.id,
            "created_at": row.created_at,
            "author_name": row.author_name,
        }
    if chat is not None:
        return {
            "action": "clear",
            "agent_id": agent.id,
            "depth": 0,
            "content": "",
            "target": "chat",
            "message_id": chat.id,
            "created_at": chat.created_at,
            "from_name": agent.name,
        }
    return {"action": "noop", "agent_id": agent.id, "depth": 0, "content": ""}
