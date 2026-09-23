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

from core.bm_cli.approvals import resolve_approval_by_unique_prefix, resume_cli_approval
from core.floors import AgentOnVacation
from core.messaging import route_human_dm, route_human_channel_message
from core.models.message import HUMAN_SENDER_ID
import db

from integrations.telegram import formatters
from integrations.telegram.auth import is_telegram_user_allowed
from integrations.telegram.join_list import (
    JoinListStatus,
    remember_channel_list,
    remembered_floor_id,
    resolve_join_ordinal,
)
from integrations.telegram.sessions import (
    clear_session,
    get_session,
    touch_session,
    upsert_session,
)

logger = logging.getLogger(__name__)

# One thread verb. /thread opens the shared channel the BossMod list already
# shows. It does not create a spatial office meeting, and it does not touch
# CLI auto-approve, Soft-block, or Deny.
TELEGRAM_START_COMMAND_HELP = (
    "Commands:\n"
    "/help — command map\n"
    "/agents — list agents\n"
    "/chat <name> — chat with an agent\n"
    "/chat <name1> <name2> — thread with those agents\n"
    "/chat — close active session\n"
    "/thread — agents on one floor (Lobby, or /thread <floor>)\n"
    "/channels — one floor's threads, numbered\n"
    "/join <N> — rejoin list row N\n"
    "/status — quick summary\n"
    "/approve — pending approvals"
)

_JOIN_CLOSED = {
    JoinListStatus.MISSING: "No thread list yet. Send /channels, then /join N.",
    JoinListStatus.STALE: "That list is stale. Send /channels again, then /join N.",
    JoinListStatus.UNKNOWN: "That row is not on the list. Send /channels, then /join N.",
}


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
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("thread", cmd_thread))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("join", cmd_join))
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
# /start  (alias: /help)
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
        f"{TELEGRAM_START_COMMAND_HELP}"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias of /start. Prints the same command map."""
    await cmd_start(update, context)


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
        await _open_thread(update, context, user_id, resolved)


def _floor_from_args(args: list[str]):
    """Lobby when no name is given. None when the name is not a floor."""
    from db.floors import LOBBY_ID, ensure_lobby, get_floor, get_floor_by_name

    ensure_lobby()
    label = " ".join(args).strip()
    if not label:
        return get_floor(LOBBY_ID)
    return get_floor_by_name(label)


def _thread_display_name(agents: list[dict[str, Any]]) -> str:
    """Roster label used when BossMod creates a thread with no custom name."""
    member_names = [str(agent.get("name") or "") for agent in agents]
    if len(member_names) <= 3:
        return ", ".join(member_names)
    return f"{', '.join(member_names[:3])} +{len(member_names) - 3}"


async def _publish_new_thread(context: ContextTypes.DEFAULT_TYPE, channel: Any) -> None:
    """Broadcast the new thread on the same channel_updated surface the GUI lists.

    The rail refetches ``GET /api/channels`` from that event. The payload is
    the summary that route already returns, so a Telegram-born thread is not
    a second identity. A failed publish leaves the row in place for the next
    list load. Soft-block and CLI policy are not part of this payload.
    """
    bot_data = getattr(context, "bot_data", None)
    if not isinstance(bot_data, dict):
        return
    broadcast_manager = bot_data.get("broadcast_manager")
    if broadcast_manager is None:
        return
    from api.routes.agents import _serialize_channel_summary

    members = db.list_channel_member_details(channel.id)
    latest = db.get_latest_channel_message(channel.id)
    summary = _serialize_channel_summary(channel, members=members, latest_message=latest)
    try:
        await broadcast_manager.broadcast_channel_updated(summary)
    except Exception:
        logger.warning("Could not publish new thread %s to BossMod", channel.id, exc_info=True)


async def _open_thread(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    agents: list[dict[str, Any]],
) -> None:
    """Attach this user to the active thread for this roster.

    Identity is channel membership, the same lookup the BossMod thread list
    uses. A second open rejoins that row. It does not mint a Telegram-only
    channel, and it does not write cli_auto_approve or clear Soft-block.
    """
    member_ids = [agent["id"] for agent in agents]
    channel = db.find_active_channel_for_members(member_ids)
    created = channel is None
    if channel is None:
        from core.floors import FloorDenied

        try:
            channel = db.create_channel(
                name=_thread_display_name(agents),
                member_agent_ids=member_ids,
                created_by=HUMAN_SENDER_ID,
            )
        except FloorDenied:
            await update.message.reply_text(
                "Those agents are not on the same floor. A thread stays on one floor."
            )
            return
    names_key = ",".join(sorted(str(agent.get("name") or "").lower() for agent in agents))
    upsert_session(
        user_id,
        session_type="group",
        target_channel_id=channel.id,
        agent_names_key=names_key,
    )
    if created:
        await _publish_new_thread(context, channel)
    await update.message.reply_text(f"Thread: {channel.name}\nType anything to message them.")


# ---------------------------------------------------------------------------
# /thread
# ---------------------------------------------------------------------------

async def cmd_thread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the thread of agents on one floor. This is a channel, not a meeting.

    With no floor name the floor is Lobby. Agents on any other floor are
    not included, so the roster cannot mix.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    floor = _floor_from_args(context.args or [])
    if floor is None:
        await update.message.reply_text("No floor with that name.")
        return
    agents = [
        agent for agent in db.get_world_state()
        if str(agent.get("floor_id") or "") == floor.id
    ]
    if not agents:
        await update.message.reply_text(f"No agents on {floor.name}.")
        return
    await _open_thread(update, context, update.effective_user.id, agents)


# ---------------------------------------------------------------------------
# /channels
# ---------------------------------------------------------------------------

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    floor = _floor_from_args(context.args or [])
    if floor is None:
        await update.message.reply_text("No floor with that name.")
        return
    channels = [channel for channel in db.list_channels() if channel.floor_id == floor.id]
    remember_channel_list(
        update.effective_user.id,
        [channel.id for channel in channels],
        floor_id=floor.id,
    )
    members_map = {ch.id: db.list_channel_member_details(ch.id) for ch in channels}
    text = formatters.format_channels_list(channels, members_map, floor_name=floor.name)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /join N
# ---------------------------------------------------------------------------

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rejoin one numbered row from the last /channels list.

    The ordinal is valid only while that list's channel ids are unchanged.
    A stale list, a missing list, or an unknown row joins nothing.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args or []
    if len(args) != 1 or not str(args[0]).isdigit():
        await update.message.reply_text("Usage: /join N — N is a row from /channels.")
        return
    ordinal = int(args[0])
    floor_id = remembered_floor_id(update.effective_user.id)
    if not floor_id:
        await update.message.reply_text(_JOIN_CLOSED[JoinListStatus.MISSING])
        return
    channels = [channel for channel in db.list_channels() if channel.floor_id == floor_id]
    status, channel_id = resolve_join_ordinal(
        update.effective_user.id,
        ordinal,
        [channel.id for channel in channels],
        floor_id=floor_id,
    )
    if status is not JoinListStatus.OK or not channel_id:
        await update.message.reply_text(_JOIN_CLOSED.get(status, _JOIN_CLOSED[JoinListStatus.STALE]))
        return
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        await update.message.reply_text(_JOIN_CLOSED[JoinListStatus.STALE])
        return
    members = db.list_channel_member_details(channel.id)
    names_key = ",".join(
        sorted(str(member.get("name") or "").lower() for member in members if member.get("name"))
    )
    upsert_session(
        update.effective_user.id,
        session_type="group",
        target_channel_id=channel.id,
        agent_names_key=names_key or None,
    )
    await update.message.reply_text(f"Thread: {channel.name}\nType anything to message them.")


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

    resolved = resolve_approval_by_unique_prefix(
        prefix,
        db.list_cli_approval_requests(status="pending"),
    )
    if resolved.status == "ambiguous":
        await update.message.reply_text(
            f"Ambiguous approval prefix '{prefix}'. "
            "Use more characters so it matches exactly one pending request."
        )
        return
    if resolved.status != "unique" or resolved.request is None:
        await update.message.reply_text(f"No pending approval matching '{prefix}'.")
        return
    request = resolved.request

    if decision == "yes":
        approval = await _resume_telegram_approval(context, request.id, approved=True)
        if approval is None:
            await update.message.reply_text("This approval is no longer pending.")
            return
        await update.message.reply_text(f"Approved: {request.command}")
    elif decision == "no":
        note = " ".join(args[2:]) if len(args) > 2 else None
        approval = await _resume_telegram_approval(
            context,
            request.id,
            approved=False,
            note=note,
        )
        if approval is None:
            await update.message.reply_text("This approval is no longer pending.")
            return
        await update.message.reply_text(f"Rejected: {request.command}")
    else:
        await update.message.reply_text("Use 'yes' or 'no': /approve yes <id> or /approve no <id>")


async def _resume_telegram_approval(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: str,
    *,
    approved: bool,
    note: str | None = None,
) -> Any | None:
    """Persist a Telegram approve/reject and enqueue the agent resume trigger."""
    services = context.bot_data["services"]
    return await resume_cli_approval(
        request_id,
        approved=approved,
        note=note,
        decision_by="telegram",
        services=services,
    )


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
        approval = await _resume_telegram_approval(context, request_id, approved=True)
        if approval is None:
            await query.edit_message_text("This approval is no longer pending.")
            return
        await query.edit_message_text(f"Approved: {request.command}")
    elif action == "reject":
        approval = await _resume_telegram_approval(context, request_id, approved=False)
        if approval is None:
            await query.edit_message_text("This approval is no longer pending.")
            return
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
            try:
                await route_human_dm(
                    agent_id=agent.id,
                    content=question,
                    from_name="Telegram User",
                    broadcast_manager=broadcast_manager,
                    services=services,
                )
            except AgentOnVacation:
                clear_session(user_id)
                await query.edit_message_text(f"{agent.name} is on vacation and cannot answer.")
                return
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
        try:
            await route_human_dm(
                agent_id=session.target_agent_id,
                content=text,
                from_name="Telegram User",
                broadcast_manager=broadcast_manager,
                services=services,
            )
        except AgentOnVacation:
            clear_session(user_id)
            await update.message.reply_text(
                "That agent is on vacation. Use /chat <agent> to talk to someone else."
            )
            return
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
