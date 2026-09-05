"""BossMod AI — Telegram bot command handlers and message router.

All dependencies (RuntimeServices, ConnectionManager) are injected via
``Application.bot_data`` at creation time — handlers never import singletons.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.messaging import route_human_dm, route_human_channel_message
import db

from integrations.telegram import formatters
from integrations.telegram.auth import is_telegram_user_allowed
from integrations.telegram.sessions import (
    clear_session,
    find_channel_for_names_key,
    get_session,
    touch_session,
    upsert_session,
)

logger = logging.getLogger(__name__)


def create_application(
    token: str,
    *,
    services: Any,
    broadcast_manager: Any,
) -> Application:
    """Build and configure the Telegram bot application with injected deps."""
    app = Application.builder().token(token).build()
    app.bot_data["services"] = services
    app.bot_data["broadcast_manager"] = broadcast_manager

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("meeting", cmd_meeting))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CallbackQueryHandler(handle_approval_callback, pattern=r"^(approve|reject|ask):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plain_text))

    return app


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def _check_auth(update: Update) -> bool:
    """Return True if the user is on the Telegram allowlist.

    Empty allowlist is deny-all (fail-closed). Approvals use this same gate.
    """
    user = update.effective_user
    user_id = user.id if user is not None else None
    return is_telegram_user_allowed(user_id)


def _resolve_agent_by_name(name: str) -> dict[str, Any] | None:
    """Case-insensitive agent lookup. Returns the world-state dict or None."""
    agents = db.get_world_state()
    name_lower = name.strip().lower()
    for agent in agents:
        if agent.get("name", "").lower() == name_lower:
            return agent
    return None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not _check_auth(update):
        await update.message.reply_text(
            f"Your Telegram user ID: {user_id}\n\n"
            f"Add this ID to the Allowed User IDs field in\n"
            f"BossMod Settings > Telegram, then restart."
        )
        return

    await update.message.reply_text(
        f"Welcome to BossMod AI.\n"
        f"Your Telegram user ID: {user_id}\n\n"
        f"Commands:\n"
        f"/agents — list agents\n"
        f"/chat <name> — chat with an agent\n"
        f"/chat <name1> <name2> — group chat\n"
        f"/chat — close active session\n"
        f"/meeting — all-agent group chat\n"
        f"/channels — list active channels\n"
        f"/status — quick summary\n"
        f"/approve — pending approvals"
    )


# ---------------------------------------------------------------------------
# /agents
# ---------------------------------------------------------------------------

async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    agents = db.get_world_state()
    text = formatters.format_agent_list(agents)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /chat <name> [name2 ...]
# ---------------------------------------------------------------------------

async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args or []
    if not args:
        session = get_session(update.effective_user.id)
        if session and session.session_type != "idle":
            clear_session(update.effective_user.id)
            await update.message.reply_text("Session closed.")
        else:
            await update.message.reply_text("Usage: /chat <agent_name> [agent_name2 ...]")
        return

    resolved: list[dict[str, Any]] = []
    for name in args:
        agent = _resolve_agent_by_name(name)
        if agent is None:
            await update.message.reply_text(f"Agent '{name}' not found.")
            return
        resolved.append(agent)

    user_id = update.effective_user.id

    if len(resolved) == 1:
        agent = resolved[0]
        upsert_session(
            user_id,
            session_type="dm",
            target_agent_id=agent["id"],
            agent_names_key=agent["name"].lower(),
        )
        status = formatters.get_status_label(agent.get("status", ""))
        await update.message.reply_text(
            f"Now chatting with {agent['name']} ({agent.get('role', '')}) — {status}\n"
            f"Type anything to message them. /chat to close."
        )
    else:
        await _open_group_session(update, user_id, resolved)


async def _open_group_session(
    update: Update,
    user_id: int,
    agents: list[dict[str, Any]],
) -> None:
    """Find or create a channel for the agent group and set the session."""
    names_key = ",".join(sorted(a["name"].lower() for a in agents))
    channel_id = find_channel_for_names_key(names_key)

    if channel_id is not None:
        channel = db.get_channel(channel_id)
        if channel is None or channel.status != "active":
            channel_id = None

    if channel_id is None:
        agent_names = " + ".join(a["name"] for a in agents)
        channel = db.create_channel(
            name=f"Telegram: {agent_names}",
            member_agent_ids=[a["id"] for a in agents],
            created_by="telegram",
        )
        channel_id = channel.id

    upsert_session(
        user_id,
        session_type="group",
        target_channel_id=channel_id,
        agent_names_key=names_key,
    )
    member_names = ", ".join(a["name"] for a in agents)
    await update.message.reply_text(f"Group chat: {member_names}\nType anything to message them.")


# ---------------------------------------------------------------------------
# /meeting
# ---------------------------------------------------------------------------

async def cmd_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    agents = db.get_world_state()
    if not agents:
        await update.message.reply_text("No agents available.")
        return
    await _open_group_session(update, update.effective_user.id, agents)


# ---------------------------------------------------------------------------
# /channels
# ---------------------------------------------------------------------------

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    channels = db.list_channels()
    members_map = {ch.id: db.list_channel_member_details(ch.id) for ch in channels}
    text = formatters.format_channels_list(channels, members_map)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    agents = db.get_world_state()
    active_tasks = db.list_tasks(status="active")
    blocked_tasks = db.list_tasks(status="blocked")
    pending = db.count_pending_cli_approval_requests()
    text = formatters.format_status_summary(agents, active_tasks, blocked_tasks, pending)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /approve [yes|no <id_prefix> [note]]
# ---------------------------------------------------------------------------

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args or []

    if not args:
        requests = db.list_cli_approval_requests(status="pending")
        agents_map: dict[str, Any] = {}
        for req in requests:
            if req.agent_id not in agents_map:
                agents_map[req.agent_id] = db.get_agent(req.agent_id)
        text = formatters.format_approval_list(requests, agents_map)
        await update.message.reply_text(text, parse_mode="MarkdownV2")
        return

    if len(args) < 2:
        await update.message.reply_text("Usage: /approve yes <id> or /approve no <id> [note]")
        return

    decision = args[0].lower()
    prefix = args[1].lower()

    request = _resolve_approval_by_prefix(prefix)
    if request is None:
        await update.message.reply_text(f"No pending approval matching '{prefix}'.")
        return

    if decision == "yes":
        db.approve_cli_approval_request(request.id, decision_by="telegram")
        await update.message.reply_text(f"Approved: {request.command}")
    elif decision == "no":
        note = " ".join(args[2:]) if len(args) > 2 else None
        db.reject_cli_approval_request(request.id, decision_by="telegram", decision_note=note)
        await update.message.reply_text(f"Rejected: {request.command}")
    else:
        await update.message.reply_text("Use 'yes' or 'no': /approve yes <id> or /approve no <id>")


def _resolve_approval_by_prefix(prefix: str) -> Any | None:
    """Find a pending approval request whose ID starts with the given prefix."""
    requests = db.list_cli_approval_requests(status="pending")
    prefix_lower = prefix.lower()
    for req in requests:
        if req.id.lower().startswith(prefix_lower):
            return req
    return None


# ---------------------------------------------------------------------------
# Inline button callback (approve / reject / ask)
# ---------------------------------------------------------------------------

async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    query = update.callback_query
    await query.answer()

    action, request_id = query.data.split(":", 1)
    request = db.get_cli_approval_request(request_id)

    if request is None or request.status != "pending":
        await query.edit_message_text("This approval is no longer pending.")
        return

    if action == "approve":
        db.approve_cli_approval_request(request_id, decision_by="telegram")
        await query.edit_message_text(f"Approved: {request.command}")
    elif action == "reject":
        db.reject_cli_approval_request(request_id, decision_by="telegram")
        await query.edit_message_text(f"Rejected: {request.command}")
    elif action == "ask":
        agent = db.get_agent(request.agent_id)
        if agent:
            user_id = update.effective_user.id
            upsert_session(
                user_id,
                session_type="dm",
                target_agent_id=agent.id,
                agent_names_key=agent.name.lower(),
            )
            question = f"Can you explain why you need to run: {request.command}"
            services = context.bot_data["services"]
            broadcast_manager = context.bot_data["broadcast_manager"]
            await route_human_dm(
                agent_id=agent.id,
                content=question,
                from_name="Telegram User",
                broadcast_manager=broadcast_manager,
                services=services,
            )
            await query.edit_message_text(
                f"Opened chat with {agent.name} and asked about: {request.command}"
            )
        else:
            await query.edit_message_text("Agent not found.")


# ---------------------------------------------------------------------------
# Plain text router
# ---------------------------------------------------------------------------

async def handle_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    user_id = update.effective_user.id
    session = get_session(user_id)

    if session is None or session.session_type == "idle":
        await update.message.reply_text("No active session. Use /chat <agent> to start.")
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    services = context.bot_data["services"]
    broadcast_manager = context.bot_data["broadcast_manager"]

    if session.session_type == "dm":
        if not session.target_agent_id:
            await update.message.reply_text("Session invalid. Use /chat <agent> to start a new one.")
            clear_session(user_id)
            return
        await route_human_dm(
            agent_id=session.target_agent_id,
            content=text,
            from_name="Telegram User",
            broadcast_manager=broadcast_manager,
            services=services,
        )
    elif session.session_type == "group":
        channel = db.get_channel(session.target_channel_id)
        if channel is None or channel.status != "active":
            await update.message.reply_text("Channel no longer active. Use /chat to start a new session.")
            clear_session(user_id)
            return
        await route_human_channel_message(
            channel_id=session.target_channel_id,
            channel_name=channel.name,
            content=text,
            from_name="Telegram User",
            broadcast_manager=broadcast_manager,
            services=services,
        )

    touch_session(user_id)
