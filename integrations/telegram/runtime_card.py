"""BossMod AI — Telegram's "the AI runtime has stopped" card and its Restart now button.

When automatic restarts of the runtime worker give up (``runtime_worker_down``),
the bridge sends this card once per outage to every allowlisted Telegram
user. **Restart now** does what the Needs card's button does
(``RuntimeServices.restart_worker``), and the card is edited to say whether
the runtime runs again.

Kept out of bot.py, which holds the command handlers.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.runtime.supervisor import OUTAGE_TITLE
from integrations.telegram.bot import _check_auth
from integrations.telegram.formatters import escape_md

logger = logging.getLogger(__name__)

RESTART_ACTION = "rtrestart"
CALLBACK_PATTERN = rf"^{RESTART_ACTION}$"
RUNNING_TEXT = "Running again."
# A failed start is handed back to automatic restarts (restart_worker).
STILL_STOPPED_TEXT = "Still stopped. BossMod will try again on its own."


def format_runtime_down_card(detail: str) -> tuple[str, InlineKeyboardMarkup]:
    """The outage card: the Needs card's title and two sentences, with Restart now.

    Args:
        detail: The ``runtime_worker_down`` event's detail
            (``WorkerOutage.describe``), the same words the Needs card shows.

    Returns:
        MarkdownV2 text (the bold title, then the detail) and the one-button
        keyboard (``rtrestart``).
    """
    text = f"*{escape_md(OUTAGE_TITLE)}*\n{escape_md(detail)}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Restart now", callback_data=RESTART_ACTION)]])
    return text, keyboard


async def handle_runtime_restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer the outage card's Restart now: start the runtime again, then say whether it runs.

    Only an allowlisted user can press it (the approval buttons' gate). The
    restart goes through the ``RuntimeServices`` injected at bot creation
    (``bot_data["services"]``). A start that fails is logged at ERROR with
    its traceback, and the card says so in plain words; automatic restarts
    take over again.
    """
    if not _check_auth(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        await context.bot_data["services"].restart_worker()
    except Exception:
        logger.exception("Restart now from Telegram did not start the runtime worker")
        await query.edit_message_text(STILL_STOPPED_TEXT)
        return
    await query.edit_message_text(RUNNING_TEXT)
