"""BossMod AI — Telegram event bridge.

Receives runtime events from ``RuntimeServices._dispatch_event()``.
Forwards agent speech in each user's open DM or thread. Sends each CLI
approval card and each process check-in card once to every allowlisted
user. Nothing else reaches Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot

import db
from core import config
from core.bm_cli.process_announce import PROCESS_CHECKIN_EVENT
from core.models.cli_policy import CLI_APPROVAL_KIND
from integrations.telegram import formatters
from integrations.telegram.auth import parse_allowed_user_ids
from integrations.telegram.process_checkin import format_checkin_card
from integrations.telegram.sessions import (
    list_sessions_for_agent,
    list_sessions_for_channel,
)

logger = logging.getLogger(__name__)


class TelegramEventBridge:
    """Subscribe to runtime events and forward them to Telegram users."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        # Approval cards already sent, so each goes out once per app run.
        self._sent_approval_ids: set[str] = set()

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
        """Forward an agent's DM line to the users with that DM open.

        A CLI approval notice sends its card instead. Every other line not
        authored by the agent (receipts, task and memory notices, the
        operator's own lines) stays in the app.
        """
        if data.get("notification_kind") == CLI_APPROVAL_KIND:
            await self._send_approval_card(data.get("cli_approval"))
            return
        if data.get("from_type") != "agent":
            return
        agent_id = data.get("agent_id")
        if not agent_id:
            return

        text = formatters.format_agent_reply(data.get("from_name", "Agent"), data.get("content", ""))
        for session in list_sessions_for_agent(agent_id):
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

    async def _on_channel_message(self, data: dict[str, Any]) -> None:
        """Forward an agent's thread line to the users with that thread open.

        A CLI approval notice sends its card instead. System lines, round
        markers included, and the operator's own lines stay in the app.
        """
        if data.get("notification_kind") == CLI_APPROVAL_KIND:
            await self._send_approval_card(data.get("cli_approval"))
            return
        if data.get("author_type") != "agent":
            return
        channel_id = data.get("channel_id")
        if not channel_id:
            return

        text = formatters.format_agent_reply(data.get("author_name", "Agent"), data.get("content", ""))
        for session in list_sessions_for_channel(channel_id):
            await self._send_safe(session.telegram_user_id, text, parse_mode="MarkdownV2")

    async def _on_activity(self, data: dict[str, Any]) -> None:
        """Send the process check-in card; every other activity event stays in the app."""
        if data.get("event", "") == PROCESS_CHECKIN_EVENT:
            await self._send_process_checkin(data)

    async def _send_process_checkin(self, data: dict[str, Any]) -> None:
        """Send the keep-or-stop card for one asked check-in to every allowlisted user.

        The runtime sink nests the activity's ``extra`` under ``data["extra"]``.
        The row is re-read: a card goes out only while the process still runs
        with its check-in asked, so a check-in answered (or a process ended)
        before this event was drained sends nothing.

        Raises:
            ValueError: The event carries no ``process_id``; the watchdog
                always sends one, so this is a bug.
        """
        extra = data.get("extra") or {}
        process_id = extra.get("process_id")
        if not process_id:
            raise ValueError(f"agent_process_checkin without a process_id: {data!r}")
        row = db.get_agent_process(process_id)
        if row is None or row.status != "running" or row.checkin_requested_at is None:
            return
        agent = db.get_agent(row.agent_id)
        text, keyboard = format_checkin_card(row, agent.name if agent is not None else None)
        for chat_id in self._card_recipients():
            await self._send_safe(chat_id, text, parse_mode="MarkdownV2", reply_markup=keyboard)

    async def _send_approval_card(self, card: dict[str, Any] | None) -> None:
        """Send one CLI approval's Approve/Reject/Ask card once to every allowlisted user.

        The emit path can broadcast the same approval's notice more than once,
        so the card is deduped by request id for this app run. The row is
        re-read: an approval answered before this event was drained sends
        nothing.

        Args:
            card: The notice's ``cli_approval`` card (``CliApprovalRequest.as_card``).
                ``None`` means the request row was gone when the notice was
                built, so there is nothing to approve and nothing is sent.

        Raises:
            ValueError: The card has no ``id``; ``as_card`` always sets one,
                so this is a bug.
        """
        if card is None:
            return
        request_id = card.get("id")
        if not request_id:
            raise ValueError(f"cli_approval card without an id: {card!r}")
        if request_id in self._sent_approval_ids:
            return
        request = db.get_cli_approval_request(request_id)
        if request is None or request.status != "pending":
            return
        self._sent_approval_ids.add(request_id)
        text, keyboard = formatters.format_approval_card(request, db.get_agent(request.agent_id))
        for chat_id in self._card_recipients():
            await self._send_safe(chat_id, text, parse_mode="MarkdownV2", reply_markup=keyboard)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _card_recipients() -> list[int]:
        """Every allowlisted Telegram user: the one recipient rule for action cards.

        Read at send time, like ``is_telegram_user_allowed``. A private chat's
        id is the user's id, so no session row is needed: a card reaches the
        operator whether their chat is open, closed, or they just moved
        floors. Only allowlisted users can press its buttons anyway.
        """
        return sorted(parse_allowed_user_ids(config.get("telegram_allowed_user_ids")))

    async def _send_safe(self, chat_id: int, text: str, **kwargs: Any) -> None:
        """Send a message; a failed send is logged at WARNING and the next recipient still gets theirs.

        A card that did not arrive (for example, an allowlisted user who never
        sent ``/start``, so Telegram refuses the bot) must show up in the log.
        """
        try:
            await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as exc:
            logger.warning("Failed to send Telegram message to %s: %s", chat_id, exc, exc_info=True)

    _HANDLERS: dict[str, Any] = {
        "chat_message": _on_chat_message,
        "channel_message": _on_channel_message,
        "activity": _on_activity,
    }
