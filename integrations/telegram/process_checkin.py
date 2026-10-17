"""BossMod AI — Telegram's background-process check-in card and its buttons.

When the watchdog asks whether a background process should keep running
(``agent_process_checkin``), the bridge sends this card to every active
session. **Keep running** re-arms the check-in; **Stop** stops the process as
the operator. Either answer is announced the way the desk's own buttons
announce it, so the desk and Needs repaint. An unanswered card does nothing.

Kept out of bot.py, which holds the command handlers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import db
from core.bm_cli import background_processes
from core.bm_cli.process_announce import announce_processes_changed
from core.models.agent_process import AgentProcess
from integrations.telegram.bot import _check_auth
from integrations.telegram.formatters import escape_md

KEEP_ACTION = "pkeep"
STOP_ACTION = "pstop"
CALLBACK_PATTERN = rf"^({KEEP_ACTION}|{STOP_ACTION}):"
# The same words the desk's Stop records, so the desk reads one reason.
OPERATOR_STOP_REASON = "Stopped by you"
GONE_TEXT = "This process is no longer running."


def format_checkin_card(row: AgentProcess, agent_name: str | None) -> tuple[str, InlineKeyboardMarkup]:
    """The check-in card: who is still running what, with Keep running and Stop.

    Args:
        row: The running process whose check-in was asked.
        agent_name: Its agent's name; ``None`` when the agent is gone.

    Returns:
        MarkdownV2 text (the title, then the command in code) and the
        two-button keyboard (``pkeep:<id>`` / ``pstop:<id>``).
    """
    title = f"{agent_name or 'An agent'}'s background process is still running"
    text = f"*{escape_md(title)}*\n`{escape_md(row.command)}`"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Keep running", callback_data=f"{KEEP_ACTION}:{row.id}"),
            InlineKeyboardButton("Stop", callback_data=f"{STOP_ACTION}:{row.id}"),
        ]
    ])
    return text, keyboard


async def handle_process_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer a check-in card's button: keep the process running, or stop it.

    Only an allowlisted user can answer (the approval buttons' gate). The
    keep or stop runs off the event loop (a stop reaps, which can take a
    few seconds), then the change is announced through the app's
    ``broadcast_manager`` and the card is edited to say what happened, or
    that the process is no longer running.

    Raises:
        ValueError: The button's data is not ``pkeep:<id>`` or ``pstop:<id>``
            (the handler's pattern admits nothing else).
    """
    if not _check_auth(update):
        return
    query = update.callback_query
    await query.answer()
    action, process_id = query.data.split(":", 1)
    if action == KEEP_ACTION:
        row = await asyncio.to_thread(background_processes.keep_running, process_id)
        done, detail = "Kept running", "Kept a background process running"
    elif action == STOP_ACTION:
        row = await asyncio.to_thread(
            background_processes.stop, process_id, by="operator", reason=OPERATOR_STOP_REASON,
        )
        done, detail = "Stopped", "Stopped a background process"
    else:
        raise ValueError(f"Not a process check-in answer: {query.data!r}")
    if row is None:
        await query.edit_message_text(GONE_TEXT)
        return
    agent = db.get_agent(row.agent_id)
    await announce_processes_changed(
        _broadcast_manager(context),
        agent_id=row.agent_id,
        agent_name=agent.name if agent is not None else None,
        detail=f"{detail}: {row.command}",
        process_id=row.id,
    )
    await query.edit_message_text(f"{done}: {row.command}")


def _broadcast_manager(context: ContextTypes.DEFAULT_TYPE) -> Any:
    """The app's websocket manager, injected at bot creation (bot.py ``create_application``)."""
    return context.bot_data["broadcast_manager"]
