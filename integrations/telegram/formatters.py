"""BossMod AI — Telegram message formatters.

Pure functions that accept domain objects and return Telegram-safe
MarkdownV2 strings and InlineKeyboardMarkup objects.
"""

from __future__ import annotations

import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


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


def format_agent_list(agents: list[dict[str, Any]]) -> str:
    """Format a list of agents with name, role, and status."""
    if not agents:
        return escape_md("No agents found.")

    lines = ["*Agents*\n"]
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
        f"Pending approvals: {escape_md(str(pending_approvals))}",
    ]
    return "\n".join(lines)


def format_channels_list(
    channels: list[Any],
    members_map: dict[str, list[dict[str, Any]]],
) -> str:
    """Format active channels with member names."""
    if not channels:
        return escape_md("No active channels.")

    lines = ["*Channels*\n"]
    for ch in channels:
        name = escape_md(ch.name if hasattr(ch, "name") else str(ch))
        members = members_map.get(ch.id if hasattr(ch, "id") else "", [])
        member_names = ", ".join(escape_md(m.get("name", "?")) for m in members)
        lines.append(f"*{name}* \\- {member_names or escape_md('no members')}")

    return "\n".join(lines)


def format_approval_list(
    requests: list[Any],
    agents_map: dict[str, Any],
) -> str:
    """Format a list of pending approval requests."""
    if not requests:
        return escape_md("No pending approvals.")

    lines = ["*Pending Approvals*\n"]
    for req in requests:
        req_id = req.id if hasattr(req, "id") else str(req)
        short_id = escape_md(req_id[:8])
        command = escape_md(req.command if hasattr(req, "command") else "?")
        agent = agents_map.get(req.agent_id if hasattr(req, "agent_id") else "", None)
        agent_name = escape_md(agent.name if agent and hasattr(agent, "name") else "Unknown")
        lines.append(f"`{short_id}` *{agent_name}*: `{command}`")

    lines.append(f"\n{escape_md('Use /approve yes <id> or /approve no <id>')}")
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

    text_parts = [
        f"*Approval Request*\n",
        f"Agent: *{agent_name}*",
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


def format_notification(kind: str, data: dict[str, Any]) -> str | None:
    """Format a runtime event notification. Returns None if not worth sending."""
    event = data.get("event", kind)
    detail = data.get("detail", "")
    agent_name = data.get("agent_name", "")

    if not detail:
        return None

    prefix = f"*{escape_md(agent_name)}*: " if agent_name else ""
    return f"{prefix}{escape_md(detail)}"
