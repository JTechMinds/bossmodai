"""BossMod AI — Telegram bot command handlers and message router.

All dependencies (RuntimeServices, ConnectionManager) are injected via
``Application.bot_data`` at creation time — handlers never import singletons.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.bm_cli.approvals import resolve_approval_by_unique_prefix, resume_cli_approval
from core.floors import AgentOnVacation, FloorSuspended
from core.messaging import route_human_dm, route_human_channel_message
from core.models.floor import Floor
from core.models.message import HUMAN_SENDER_ID
import db
from db.floors import get_floor, list_floors

from integrations.telegram import formatters
from integrations.telegram.auth import is_telegram_user_allowed
from integrations.telegram.join_list import (
    JoinListStatus,
    remember_channel_list,
    resolve_join_ordinal,
)
from integrations.telegram.sessions import (
    clear_session,
    current_floor_id,
    get_session,
    set_floor,
    touch_session,
    upsert_session,
)

logger = logging.getLogger(__name__)

_JOIN_CLOSED = {
    JoinListStatus.MISSING: (
        "Send /channels first to see this floor's threads. "
        "Then /join and a number from that list, like /join 1."
    ),
    JoinListStatus.STALE: (
        "The threads changed since you last looked. "
        "Send /channels again, then /join and a number from the new list."
    ),
    JoinListStatus.UNKNOWN: "That number isn't on the list. Send /channels to see the numbers.",
}


def create_application(
    token: str,
    *,
    services: Any,
    broadcast_manager: Any,
) -> Application:
    """Build and configure the Telegram bot application with injected deps.

    Every slash command comes from ``TELEGRAM_COMMANDS``, the same registry
    ``menu_commands`` reads, so a handler and its menu entry cannot drift.
    """
    app = Application.builder().token(token).build()
    app.bot_data["services"] = services
    app.bot_data["broadcast_manager"] = broadcast_manager

    for command in TELEGRAM_COMMANDS:
        app.add_handler(CommandHandler(command.name, command.handler))
    app.add_handler(CallbackQueryHandler(handle_approval_callback, pattern=r"^(approve|reject|ask):"))
    from integrations.telegram.process_checkin import CALLBACK_PATTERN, handle_process_checkin_callback  # needs _check_auth
    app.add_handler(CallbackQueryHandler(handle_process_checkin_callback, pattern=CALLBACK_PATTERN))
    from integrations.telegram import runtime_card  # needs _check_auth
    app.add_handler(CallbackQueryHandler(runtime_card.handle_runtime_restart_callback, pattern=runtime_card.CALLBACK_PATTERN))
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


def _resolve_agent_by_name(name: str, floor_id: str) -> dict[str, Any] | None:
    """Case-insensitive agent lookup on one floor. Returns the world-state dict or None."""
    agents = db.get_world_state()
    name_lower = name.strip().lower()
    for agent in agents:
        if str(agent.get("floor_id") or "") != floor_id:
            continue
        if agent.get("name", "").lower() == name_lower:
            return agent
    return None


def _current_floor(user_id: int) -> Floor:
    """The floor this Telegram user is on, read from their session row.

    Raises:
        LookupError: The stored floor does not exist. A floor delete moves
            its Telegram users to Lobby first (``db.floors.delete_floor_row``),
            so this is a bug and is not replaced by Lobby.
    """
    floor_id = current_floor_id(user_id)
    floor = get_floor(floor_id)
    if floor is None:
        raise LookupError(f"Telegram user {user_id} is on floor {floor_id!r}, which does not exist")
    return floor


def _active_floors() -> list[Floor]:
    """Active floors in the app switcher's order: Lobby first, then by name.

    A floor's number in ``/floors`` and ``/floor N`` is its 1-based position
    in this list, so it matches the number the app's switcher shows.
    """
    return [floor for floor in list_floors() if floor.suspended_at is None]


# ---------------------------------------------------------------------------
# /start  (alias: /help)
# ---------------------------------------------------------------------------

def _open_chat_label(user_id: int) -> str:
    """Name what this user has open: ``chatting with Ada``, ``in thread Books``, or ``no chat open``.

    A target row that has disappeared reads as no chat open; the stale
    target is handled when the user next types (``handle_plain_text``).
    """
    session = get_session(user_id)
    if session is not None and session.session_type == "dm" and session.target_agent_id:
        agent = db.get_agent(session.target_agent_id)
        if agent is not None:
            return f"chatting with {agent.name}"
    if session is not None and session.session_type == "group" and session.target_channel_id:
        channel = db.get_channel(session.target_channel_id)
        if channel is not None:
            return f"in thread {channel.name}"
    return "no chat open"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the user: their Telegram id, and for an allowlisted user the help.

    The help opens with where the user is (floor and open chat), so the
    reply changes as they move between floors and threads.
    """
    user_id = update.effective_user.id

    if not _check_auth(update):
        await update.message.reply_text(
            f"Your Telegram user ID: {user_id}\n\n"
            f"Add this ID to the Allowed User IDs field in\n"
            f"BossMod Settings > Telegram, then restart."
        )
        return

    help_text = formatters.format_help(
        floor_name=_current_floor(user_id).name,
        open_label=_open_chat_label(user_id),
    )
    await update.message.reply_text(
        f"Welcome to BossMod AI.\n"
        f"Your Telegram user ID: {user_id}\n\n"
        f"{help_text}"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias of /start. Prints the same command map."""
    await cmd_start(update, context)


# ---------------------------------------------------------------------------
# /floors and /floor N
# ---------------------------------------------------------------------------

async def cmd_floors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the active floors, numbered, with the user's floor marked.

    Suspended floors are listed apart and carry no number: ``/floor N``
    cannot move anyone onto one.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    active = _active_floors()
    suspended = [floor for floor in list_floors() if floor.suspended_at is not None]
    current = _current_floor(update.effective_user.id)
    text = formatters.format_floor_list(active, suspended, current.id)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def cmd_floor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's floor, or move them to floor N from ``/floors``.

    N resolves against the live active list at call time, not a snapshot
    like ``/join``: moving floors is harmless and reversible, and the reply
    names the floor, so a stale number is visible at once. Moving closes an
    open chat or thread (its target lives on the old floor); asking for the
    floor the user is already on changes nothing.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    user_id = update.effective_user.id
    args = context.args or []
    if not args:
        floor = _current_floor(user_id)
        await update.message.reply_text(
            f"You're on {floor.name}. Send /floors to see the floors, "
            "then /floor and a number, like /floor 2."
        )
        return
    # isdecimal, not isdigit: int() rejects digits such as superscripts.
    if len(args) != 1 or not str(args[0]).isdecimal():
        await update.message.reply_text("Add a floor number, like /floor 2. Send /floors to see the numbers.")
        return
    ordinal = int(args[0])
    active = _active_floors()
    if ordinal < 1 or ordinal > len(active):
        await update.message.reply_text(f"No floor {ordinal}. Send /floors to see the list.")
        return
    target = active[ordinal - 1]
    if target.id == current_floor_id(user_id):
        await update.message.reply_text(f"You're already on {target.name}.")
        return
    session = get_session(user_id)
    had_open_chat = session is not None and session.session_type != "idle"
    set_floor(user_id, target.id)
    reply = f"Now on {target.name}."
    if had_open_chat:
        reply += " Your open chat was closed."
    reply += "\nSend /agents to see who's here."
    await update.message.reply_text(reply)


# ---------------------------------------------------------------------------
# /agents
# ---------------------------------------------------------------------------

async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the agents on the user's floor."""
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    floor = _current_floor(update.effective_user.id)
    if floor.suspended_at is not None:
        await update.message.reply_text(_suspended_floor_reply(floor.name))
        return
    agents = [
        agent for agent in db.get_world_state()
        if str(agent.get("floor_id") or "") == floor.id
    ]
    text = formatters.format_agent_list(agents, floor_name=floor.name)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /chat <name> [name2 ...]
# ---------------------------------------------------------------------------

async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open a DM (one name) or a thread (several names) on the user's floor; no name closes it.

    Names resolve only against agents on the user's current floor, so
    ``/chat Ada`` never reaches an Ada on another floor.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args or []
    if not args:
        session = get_session(update.effective_user.id)
        if session and session.session_type != "idle":
            clear_session(update.effective_user.id)
            await update.message.reply_text("Chat closed.")
        else:
            await update.message.reply_text(
                "Add an agent's name, like /chat Ada. "
                "Add more names to open a thread with all of them. "
                "Send /agents to see who's here."
            )
        return

    user_id = update.effective_user.id
    floor = _current_floor(user_id)
    if floor.suspended_at is not None:
        await update.message.reply_text(_suspended_floor_reply(floor.name))
        return

    resolved: list[dict[str, Any]] = []
    for name in args:
        agent = _resolve_agent_by_name(name, floor.id)
        if agent is None:
            await update.message.reply_text(
                f"No agent named {name} on {floor.name}. "
                "Send /agents to see who's here, or /floors to change floors."
            )
            return
        resolved.append(agent)

    if len(resolved) == 1:
        agent = resolved[0]
        upsert_session(
            user_id,
            session_type="dm",
            floor_id=floor.id,
            target_agent_id=agent["id"],
            agent_names_key=agent["name"].lower(),
        )
        status = formatters.get_status_label(agent.get("status", ""))
        await update.message.reply_text(
            f"Now chatting with {agent['name']} ({agent.get('role', '')}) — {status}\n"
            f"Type anything to message them. Send /chat on its own to close."
        )
    else:
        await _open_thread(update, context, user_id, resolved, floor_id=floor.id)


def _suspended_floor_reply(floor_name: str) -> str:
    """The reply for a command run on a suspended floor; it names the way out."""
    return f"{floor_name} is suspended. Resume it in BossMod, or send /floors to switch."


def _agent_floor_suspended_reply(agent_name: str) -> str:
    """The reply for a DM to an agent whose floor is suspended."""
    return f"{agent_name}'s floor is suspended. Resume it in BossMod first."


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
    *,
    floor_id: str,
) -> None:
    """Attach this user to the active thread for this roster.

    Identity is channel membership, the same lookup the BossMod thread list
    uses. A second open rejoins that row. It does not mint a Telegram-only
    channel, and it does not write cli_auto_approve or clear Soft-block.

    Args:
        update: The Telegram update to reply to.
        context: The handler context (its ``bot_data`` carries the broadcast manager).
        user_id: The Telegram user opening the thread.
        agents: World-state rows of the roster, all read from ``floor_id``.
        floor_id: The user's current floor, stored on the session.
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
            # An agent can move floors between the roster read and the create.
            await update.message.reply_text(
                "Those agents are not on the same floor. A thread stays on one floor."
            )
            return
    names_key = ",".join(sorted(str(agent.get("name") or "").lower() for agent in agents))
    upsert_session(
        user_id,
        session_type="group",
        floor_id=floor_id,
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
    """Open the thread of every agent on the user's floor. This is a channel, not a meeting.

    Agents on any other floor are not included, so the roster cannot mix.
    It takes no arguments: ``/floor N`` changes the floor.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    if context.args:
        await update.message.reply_text(
            "Just send /thread on its own. It opens a thread with everyone on your floor. "
            "Send /floors to change floors."
        )
        return
    user_id = update.effective_user.id
    floor = _current_floor(user_id)
    if floor.suspended_at is not None:
        await update.message.reply_text(_suspended_floor_reply(floor.name))
        return
    agents = [
        agent for agent in db.get_world_state()
        if str(agent.get("floor_id") or "") == floor.id
    ]
    if not agents:
        await update.message.reply_text(f"No agents on {floor.name}.")
        return
    await _open_thread(update, context, user_id, agents, floor_id=floor.id)


# ---------------------------------------------------------------------------
# /channels
# ---------------------------------------------------------------------------

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the user's floor's threads, numbered for ``/join N``.

    It takes no arguments: ``/floor N`` changes the floor.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    if context.args:
        await update.message.reply_text(
            "Just send /channels on its own. It lists your floor's threads. "
            "Send /floors to change floors."
        )
        return
    floor = _current_floor(update.effective_user.id)
    if floor.suspended_at is not None:
        await update.message.reply_text(_suspended_floor_reply(floor.name))
        return
    channels = [channel for channel in db.list_channels() if channel.floor_id == floor.id]
    remember_channel_list(
        update.effective_user.id,
        [channel.id for channel in channels],
        floor_id=floor.id,
    )
    members_map = db.list_channel_member_details_for([channel.id for channel in channels])
    text = formatters.format_channels_list(channels, members_map, floor_name=floor.name)
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /join N
# ---------------------------------------------------------------------------

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rejoin one numbered row from the last /channels list.

    The ordinal is valid only while that list's channel ids are unchanged
    and the list was shown for the user's current floor. A stale list (one
    from another floor included), a missing list, or an unknown row joins
    nothing.
    """
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args or []
    if len(args) != 1 or not str(args[0]).isdigit():
        await update.message.reply_text("Add a thread number, like /join 1. Send /channels to see the numbers.")
        return
    ordinal = int(args[0])
    floor = _current_floor(update.effective_user.id)
    channels = [channel for channel in db.list_channels() if channel.floor_id == floor.id]
    status, channel_id = resolve_join_ordinal(
        update.effective_user.id,
        ordinal,
        [channel.id for channel in channels],
        floor_id=floor.id,
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
        floor_id=floor.id,
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
        await update.message.reply_text(
            "Send /approve to see what's waiting. "
            "To approve one, send /approve yes and its code, like /approve yes a1b2. "
            "To reject, send /approve no a1b2, and add a reason after it if you like."
        )
        return

    decision = args[0].lower()
    prefix = args[1].lower()

    resolved = resolve_approval_by_unique_prefix(
        prefix,
        db.list_cli_approval_requests(status="pending"),
    )
    if resolved.status == "ambiguous":
        await update.message.reply_text(
            f"More than one approval starts with {prefix}. Type more of the code."
        )
        return
    if resolved.status != "unique" or resolved.request is None:
        await update.message.reply_text(
            f"No approval is waiting with the code {prefix}. Send /approve to see them."
        )
        return
    request = resolved.request

    if decision == "yes":
        approval = await _resume_telegram_approval(context, request.id, approved=True)
        if approval is None:
            await update.message.reply_text("This approval isn't waiting anymore.")
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
            await update.message.reply_text("This approval isn't waiting anymore.")
            return
        await update.message.reply_text(f"Rejected: {request.command}")
    else:
        await update.message.reply_text("Start with yes or no, like /approve yes a1b2.")


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
        await query.edit_message_text("This approval isn't waiting anymore.")
        return

    if action == "approve":
        approval = await _resume_telegram_approval(context, request_id, approved=True)
        if approval is None:
            await query.edit_message_text("This approval isn't waiting anymore.")
            return
        await query.edit_message_text(f"Approved: {request.command}")
    elif action == "reject":
        approval = await _resume_telegram_approval(context, request_id, approved=False)
        if approval is None:
            await query.edit_message_text("This approval isn't waiting anymore.")
            return
        await query.edit_message_text(f"Rejected: {request.command}")
    elif action == "ask":
        agent = db.get_agent(request.agent_id)
        if agent:
            if agent.floor_id is None:
                # On vacation: no floor to open the DM on, so nothing is written.
                await query.edit_message_text(f"{agent.name} is on vacation and cannot answer.")
                return
            agent_floor = get_floor(agent.floor_id)
            if agent_floor is None:
                raise LookupError(f"Agent {agent.id} is on floor {agent.floor_id!r}, which does not exist")
            user_id = update.effective_user.id
            # Ask moves the operator to the agent's floor; the reply says so.
            moved_floor = agent_floor.id != current_floor_id(user_id)
            upsert_session(
                user_id,
                session_type="dm",
                floor_id=agent_floor.id,
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
                    broadcast_manager=broadcast_manager,
                    services=services,
                )
            except AgentOnVacation:
                clear_session(user_id)
                await query.edit_message_text(f"{agent.name} is on vacation and cannot answer.")
                return
            except FloorSuspended:
                clear_session(user_id)
                await query.edit_message_text(_agent_floor_suspended_reply(agent.name))
                return
            where = f" on {agent_floor.name}" if moved_floor else ""
            await query.edit_message_text(
                f"Opened chat with {agent.name}{where} and asked about: {request.command}"
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
        floor = _current_floor(user_id)
        await update.message.reply_text(
            f"No chat open. Send /agents to see who's on {floor.name}, "
            "then /chat and a name, like /chat Ada."
        )
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    services = context.bot_data["services"]
    broadcast_manager = context.bot_data["broadcast_manager"]

    if session.session_type == "dm":
        if not session.target_agent_id:
            await update.message.reply_text(
                "That chat isn't available anymore. Send /chat and a name to start a new one, like /chat Ada."
            )
            clear_session(user_id)
            return
        try:
            await route_human_dm(
                agent_id=session.target_agent_id,
                content=text,
                broadcast_manager=broadcast_manager,
                services=services,
            )
        except AgentOnVacation:
            clear_session(user_id)
            await update.message.reply_text(
                "That agent is on vacation. Send /agents to see who's here."
            )
            return
        except FloorSuspended:
            clear_session(user_id)
            agent = db.get_agent(session.target_agent_id)
            # The refusal read the agent; one deleted since is "That agent",
            # the words the vacation reply uses.
            name = agent.name if agent is not None else "That agent"
            await update.message.reply_text(_agent_floor_suspended_reply(name))
            return
    elif session.session_type == "group":
        channel = db.get_channel(session.target_channel_id)
        if channel is None or channel.status != "active":
            await update.message.reply_text("That thread was closed. Send /channels to pick another.")
            clear_session(user_id)
            return
        try:
            await route_human_channel_message(
                channel_id=session.target_channel_id,
                channel_name=channel.name,
                content=text,
                broadcast_manager=broadcast_manager,
                services=services,
            )
        except FloorSuspended:
            clear_session(user_id)
            await update.message.reply_text("That thread's floor is suspended.")
            return

    touch_session(user_id)


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

class TelegramCommand(NamedTuple):
    """One slash command: its name, its handler, and its Telegram menu text.

    ``menu`` is ``None`` for a command that is registered but not shown in
    Telegram's command menu (``start``, which Telegram sends on first contact).
    """

    name: str
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
    menu: str | None


# The one source for handler names and menu text, in menu order. One thread
# verb: /thread opens the shared channel the BossMod list already shows. It
# does not create a spatial office meeting, and it does not touch CLI
# auto-approve, Soft-block, or Deny.
TELEGRAM_COMMANDS: tuple[TelegramCommand, ...] = (
    TelegramCommand("start", cmd_start, None),
    TelegramCommand("floors", cmd_floors, "List floors and see which one you're on"),
    TelegramCommand("floor", cmd_floor, "Go to a floor by number, like /floor 2"),
    TelegramCommand("agents", cmd_agents, "Agents on your floor"),
    TelegramCommand("chat", cmd_chat, "Chat with an agent, like /chat Ada"),
    TelegramCommand("thread", cmd_thread, "Thread with everyone on your floor"),
    TelegramCommand("channels", cmd_channels, "Your floor's threads, numbered"),
    TelegramCommand("join", cmd_join, "Open a thread by number, like /join 1"),
    TelegramCommand("status", cmd_status, "Summary across all floors"),
    TelegramCommand("approve", cmd_approve, "Approvals waiting for you"),
    TelegramCommand("help", cmd_help, "How to use this bot"),
)


def menu_commands() -> list[BotCommand]:
    """The command menu to register with Telegram (``set_my_commands``).

    Returns:
        One ``BotCommand`` per ``TELEGRAM_COMMANDS`` entry that has menu
        text, in registry order.
    """
    return [
        BotCommand(command.name, command.menu)
        for command in TELEGRAM_COMMANDS
        if command.menu is not None
    ]
