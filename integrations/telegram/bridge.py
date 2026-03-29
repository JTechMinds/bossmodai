"""BossMod AI — Telegram event bridge.

Receives runtime events from ``RuntimeServices._dispatch_event()`` and
forwards relevant ones to Telegram users with active sessions.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot

import db
from integrations.telegram import formatters
from integrations.telegram.sessions import (
    list_active_sessions,
    list_sessions_for_agent,
    list_sessions_for_channel,
)

logger = logging.getLogger(__name__)

_SIGNIFICANT_ACTIVITY_EVENTS = {
    "task_created",
    "task_completed",
    "task_blocked",
    "task_stalled",
    "guardian_alert",
}


class TelegramEventBridge:
    """Subscribe to runtime events and forward them to Telegram users."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def dispatch(self, kind: str, data: dict[str, Any]) -> None:
        """Route one runtime event to the appropriate handler."""
        handler = self._HANDLERS.get(kind)
        if handler is not None:
            try:
                await handler(self, data)
            except Exception:
                logger.warning("Telegram bridge handler failed for %s", kind, exc_info=True)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_chat_message(self, data: dict[str, Any]) -> None:
        """Forward agent DM replies to Telegram users in a session with that agent."""
        if data.get("from") == "human" or data.get("from_type") == "human":
            return
        agent_id = data.get("agent_id")
        if not agent_id:
            return

        content = data.get("content", "")
        from_name = data.get("from_name", "Agent")
        notification_kind = data.get("notification_kind")

        sessions = list_sessions_for_agent(agent_id)
        if sessions:
            text = formatters.format_agent_reply(from_name, content)
            for session in sessions:
                await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

        if notification_kind and notification_kind != "receipt":
            await self._push_notification_to_all(from_name, content, notification_kind, agent_id)

    async def _on_channel_message(self, data: dict[str, Any]) -> None:
        """Forward channel messages to Telegram users in that channel."""
        if data.get("author_type") == "human":
            return
        channel_id = data.get("channel_id")
        if not channel_id:
            return

        content = data.get("content", "")
        author_name = data.get("author_name", "Agent")

        sessions = list_sessions_for_channel(channel_id)
        if not sessions:
            return

        text = formatters.format_agent_reply(author_name, content)
        for session in sessions:
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

    async def _on_activity(self, data: dict[str, Any]) -> None:
        """Push significant activity events to all active Telegram sessions."""
        event = data.get("event", "")
        if event not in _SIGNIFICANT_ACTIVITY_EVENTS:
            return

        text = formatters.format_notification(event, data)
        if text is None:
            return

        for session in list_active_sessions():
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

    async def _on_feed_update(self, data: dict[str, Any]) -> None:
        """Check feed updates for approval-related notifications."""
        entry = data.get("entry", {})
        category = entry.get("category", "")
        if category != "task":
            return

        kind = entry.get("notification_kind") or entry.get("kind", "")
        if kind not in ("completion", "blocked", "handoff", "abandoned"):
            return

        content = entry.get("content", "")
        agent_name = entry.get("agent_name", "")
        if not content:
            return

        text = formatters.format_notification(kind, {"detail": content, "agent_name": agent_name})
        if text is None:
            return

        for session in list_active_sessions():
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _push_notification_to_all(
        self,
        agent_name: str,
        content: str,
        kind: str,
        agent_id: str,
    ) -> None:
        """Push a notification to all active sessions NOT already DMing this agent."""
        dm_user_ids = {s.telegram_user_id for s in list_sessions_for_agent(agent_id)}
        text = formatters.format_notification(kind, {"detail": content, "agent_name": agent_name})
        if text is None:
            return

        for session in list_active_sessions():
            if session.telegram_user_id in dm_user_ids:
                continue
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

        await self._check_approval_push(agent_id)

    async def _check_approval_push(self, agent_id: str) -> None:
        """Send inline-button approval cards if new pending requests exist."""
        try:
            requests = db.list_cli_approval_requests(status="pending", agent_id=agent_id)
            if not requests:
                return
            agent = db.get_agent(agent_id)
            sessions = list_active_sessions()
            for req in requests:
                text, keyboard = formatters.format_approval_card(req, agent)
                for session in sessions:
                    await self._send_safe(
                        session.telegram_user_id,
                        text,
                        parse_mode="MarkdownV2",
                        reply_markup=keyboard,
                    )
        except Exception:
            logger.debug("Approval push check failed", exc_info=True)

    async def _send_safe(self, chat_id: int, text: str, **kwargs: Any) -> None:
        """Send a message, swallowing errors so one bad send doesn't block others."""
        try:
            await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception:
            logger.debug("Failed to send Telegram message to %s", chat_id, exc_info=True)

    _HANDLERS: dict[str, Any] = {
        "chat_message": _on_chat_message,
        "channel_message": _on_channel_message,
        "activity": _on_activity,
        "feed_update": _on_feed_update,
    }
