"""BossMod AI — Telegram message formatters.

Pure functions that accept domain objects and return Telegram-safe
MarkdownV2 strings and InlineKeyboardMarkup objects.
"""

from __future__ import annotations

import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.bm_cli.approvals import display_approval_prefix
from core.models.floor import Floor


_MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

STATUS_LABELS = {
    "idle": "Idle",
    "work_active": "Working",
    "social_active": "Socializing",
    "in_transit": "Moving",
}


def get_status_label(status: str) -> str:
    """Return a human-friendly label for an agent status."""
    return STATUS_LABELS.get(status, "Unknown")


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return _MD_ESCAPE_RE.sub(r"\\\1", str(text))


def format_agent_list(agents: list[dict[str, Any]], *, floor_name: str) -> str:
    """Format one floor's agents with name, role, and status (MarkdownV2).

    Args:
        agents: World-state rows, already filtered to the floor.
        floor_name: The floor's label, shown on the header.

    Returns:
        A ``*Agents · Floor*`` header and one row per agent, or a single
        "No agents on Floor." line when the list is empty.
    """
    if not agents:
        return escape_md(f"No agents on {floor_name}.")

    lines = [f"*Agents · {escape_md(floor_name)}*\n"]
    for agent in agents:
        name = escape_md(agent.get("name", "Unknown"))
        role = escape_md(agent.get("role", ""))
        status = get_status_label(agent.get("status", ""))
        status_esc = escape_md(status)
        role_part = f" \\- {role}" if role else ""
        lines.append(f"*{name}*{role_part} \\[{status_esc}\\]")

    return "\n".join(lines)


def format_status_summary(
    agents: list[dict[str, Any]],
    active_tasks: list[Any],
    blocked_tasks: list[Any],
    pending_approvals: int,
) -> str:
    """Format a quick status dashboard."""
    total = len(agents)
    working = sum(1 for a in agents if a.get("status") == "work_active")
    idle = sum(1 for a in agents if a.get("status") == "idle")

    lines = [
        "*Status*\n",
        f"Agents: {escape_md(str(total))} total, {escape_md(str(working))} working, {escape_md(str(idle))} idle",
        f"Active tasks: {escape_md(str(len(active_tasks)))}",
        f"Blocked tasks: {escape_md(str(len(blocked_tasks)))}",
        f"Approvals waiting: {escape_md(str(pending_approvals))}",
    ]
    return "\n".join(lines)


def format_channels_list(
    channels: list[Any],
    members_map: dict[str, list[dict[str, Any]]],
    *,
    floor_name: str = "",
) -> str:
    """Format one floor's threads with 1-based row numbers.

    The number is the only join handle. It matches this floor's list order
    and nothing else. The floor name is on the header and on every row so
    an ordinal cannot be read as a thread from another floor.
    """
    label = floor_name.strip() or "this floor"
    if not channels:
        return escape_md(f"No threads on {label} yet. Send /thread to open one with everyone here.")

    lines = [f"*Threads · {escape_md(label)}*\n"]
    floor_tag = escape_md(f"[{label}]")
    for index, ch in enumerate(channels, start=1):
        name = escape_md(ch.name if hasattr(ch, "name") else str(ch))
        members = members_map.get(ch.id if hasattr(ch, "id") else "", [])
        member_names = ", ".join(escape_md(m.get("name", "?")) for m in members)
        ordinal = escape_md(f"{index}.")
        lines.append(
            f"{ordinal} *{name}* {floor_tag} \\- {member_names or escape_md('no members')}"
        )

    lines.append("")
    lines.append(escape_md("Open one with /join and its number, like /join 1."))
    return "\n".join(lines)


def format_approval_list(
    requests: list[Any],
    agents_map: dict[str, Any],
) -> str:
    """Format the approvals waiting for an answer, each with its short code (MarkdownV2).

    Each row starts with the shortest code that tells it apart from the
    others (``display_approval_prefix``), the code ``/approve yes|no`` takes.

    Args:
        requests: Pending approval requests, in display order.
        agents_map: Agent id → agent, for the name on each row.

    Returns:
        The ``*Approvals waiting for you*`` header, one row per request, and
        a footer whose ``/approve`` example uses the first row's code, so the
        example works as typed. With no requests, a single "No approvals are
        waiting for you." line.
    """
    if not requests:
        return escape_md("No approvals are waiting for you.")

    sibling_ids = [req.id if hasattr(req, "id") else str(req) for req in requests]
    lines = ["*Approvals waiting for you*\n"]
    for req in requests:
        req_id = req.id if hasattr(req, "id") else str(req)
        short_id = escape_md(display_approval_prefix(req_id, sibling_ids))
        command = escape_md(req.command if hasattr(req, "command") else "?")
        agent = agents_map.get(req.agent_id if hasattr(req, "agent_id") else "", None)
        agent_name = escape_md(agent.name if agent and hasattr(agent, "name") else "Unknown")
        lines.append(f"`{short_id}` *{agent_name}*: `{command}`")

    # The same value the first row shows, so the example can be typed as is.
    code = display_approval_prefix(sibling_ids[0], sibling_ids)
    footer = (
        "To answer one, use the code at the start of its line: "
        f"/approve yes {code} to approve, /approve no {code} to reject."
    )
    lines.append(f"\n{escape_md(footer)}")
    return "\n".join(lines)


def format_approval_card(
    request: Any,
    agent: Any,
) -> tuple[str, InlineKeyboardMarkup]:
    """Format an approval request with inline action buttons."""
    agent_name = escape_md(agent.name if agent and hasattr(agent, "name") else "Unknown")
    command = escape_md(request.command if hasattr(request, "command") else "?")
    content = escape_md(request.content if hasattr(request, "content") else "")
    req_id = request.id if hasattr(request, "id") else ""
    short_id = escape_md(display_approval_prefix(req_id, [req_id]))

    text_parts = [
        f"*Approval needed*\n",
        f"Agent: *{agent_name}*",
        f"Code: `{short_id}`",
        f"Command: `{command}`",
    ]
    if content:
        text_parts.append(f"Content: {content}")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve:{req_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject:{req_id}"),
            InlineKeyboardButton("Ask Agent", callback_data=f"ask:{req_id}"),
        ]
    ])
    return "\n".join(text_parts), keyboard


def format_agent_reply(agent_name: str, content: str) -> str:
    """Format an agent's response for display in Telegram."""
    name = escape_md(agent_name)
    body = escape_md(content)
    return f"*\\[{name}\\]* {body}"


def format_floor_list(active: list[Floor], suspended: list[Floor], current_floor_id: str) -> str:
    """Format the floor list for ``/floors`` (MarkdownV2).

    Active floors are numbered from 1 in the order given, the numbers
    ``/floor`` takes. Suspended floors carry no number and are listed
    apart, since nobody can move onto one.

    Args:
        active: Active floors in the app switcher's order (Lobby first).
        suspended: Suspended floors, in the same order.
        current_floor_id: The user's floor, marked ``← you're here``; when it
            is suspended a line says so instead.

    Returns:
        The ``*Floors*`` header, the numbered rows, the suspended line(s)
        when any floor is suspended, and a footer. The footer's example,
        ``like /floor K``, uses the number of the first active floor that is
        not the user's, so it always moves them. When there is none (one
        active floor, and the user is on it) the footer is "This is the only
        floor."
    """
    lines = ["*Floors*\n"]
    for index, floor in enumerate(active, start=1):
        marker = " ← you're here" if floor.id == current_floor_id else ""
        lines.append(escape_md(f"{index}. {floor.name}{marker}"))
    if suspended:
        lines.append("")
        names = ", ".join(floor.name for floor in suspended)
        lines.append(escape_md(f"Suspended (resume in BossMod): {names}"))
        for floor in suspended:
            if floor.id == current_floor_id:
                lines.append(escape_md(f"You're on {floor.name}, which is suspended."))
    lines.append("")
    # An example naming the user's own floor would answer "You're already on".
    example = next(
        (index for index, floor in enumerate(active, start=1) if floor.id != current_floor_id),
        None,
    )
    if example is None:
        lines.append(escape_md("This is the only floor."))
    else:
        lines.append(escape_md(f"Go to a floor with /floor and its number, like /floor {example}."))
    return "\n".join(lines)


def format_help(*, floor_name: str, open_label: str) -> str:
    """The ``/help`` guide, grouped by task, opening with where the user is.

    Plain text: it is sent with no parse mode, so nothing is escaped.

    Args:
        floor_name: The user's current floor.
        open_label: What is open: ``chatting with Ada``, ``in thread Books``
            or ``no chat open``.

    Returns:
        The guide text.
    """
    return (
        f"You're on {floor_name} · {open_label}\n"
        "\n"
        "Move between floors\n"
        "/floors — see the floors, numbered\n"
        "/floor 2 — go to floor 2 (use a number from /floors; this closes your open chat)\n"
        "\n"
        "Talk to agents on this floor\n"
        "/agents — see who's here\n"
        "/chat Ada — chat with Ada (use any name from /agents)\n"
        "/chat Ada Bob — open a thread with Ada and Bob\n"
        "/thread — open a thread with everyone on this floor\n"
        "Then just type. Your messages go to the chat or thread you have open.\n"
        "\n"
        "Switch threads\n"
        "/channels — see this floor's threads, numbered\n"
        "/join 1 — open thread 1 from that list\n"
        "\n"
        "Close\n"
        "/chat — on its own, closes your open chat or thread\n"
        "\n"
        "All floors\n"
        "/status — quick summary\n"
        "/approve — approvals waiting for you\n"
        "/help — this guide"
    )
