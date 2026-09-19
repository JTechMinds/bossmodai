"""Blocked origin one-liners: short why, @NextOwner wake, quieter auto GH.

Debra's locked order:
1. Origin line is ``Blocked — {why}. @NextOwner`` (host deny, no progress, …).
2. ``@NextOwner`` on that line wakes them (or one soft nudge). Tag is not hope.
3. Auto GH only when there is no next owner and no origin line.
4. Host-deny after Branch must still post the origin line; suppress_*_broadcast
   must not bury the why.
"""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.activity_scheduler import build_task_follow_up_trigger
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.next_owner import (
    HUMAN_MENTION_NAMES,
    extract_next_owner_mentions,
    mention_names_for_channel,
)
from core.agent_loop.task_origin_mirrors import short_reason
from core.models import Agent

HOST_DENY_WHY = "host deny"
NO_PROGRESS_WHY = "no progress"
SHELL_EXECUTOR_WHY = "Shell Executor off — needs enable"
NEST_GIT_WHY = "Nest git has no credentials"
NEST_GIT_BAD_CREDS_WHY = "GitHub didn’t accept that access token or SSH key"
HOST_DENY_KIND = "blocked_host_deny"
NO_PROGRESS_KIND = "blocked_no_progress"
SHELL_EXECUTOR_BLOCK_KIND = "blocked_shell_executor"
NEST_GIT_BLOCK_KIND = "blocked_nest_git"

_HOST_DENY_MARKERS = (
    "host writes stay blocked",
    "host-path access denied",
    "host path denied",
)
_SHELL_EXECUTOR_DENY_KIND = "shell_executor_deny"
_NEST_GIT_DENY_KIND = "nest_git_block"
_SHELL_EXECUTOR_MARKERS = (
    "shell executor is off",
    "shell executor off",
    "denied enable for validate-on-clone",
)
_NEST_GIT_MARKERS = (
    "nest git has no credentials",
    "host git is not visible to shell",
    "browser or desktop github login is not the agent's",
    "github didn’t accept that access token",
    "github didn't accept that access token",
)


def format_blocked_line(why: str, mention: str | None = None) -> str:
    """Return ``Blocked — {why}. @NextOwner`` or ``Blocked — {why}`` when untagged."""
    reason = short_reason(why) or (why or "").strip() or "blocked"
    if reason.lower().startswith("blocked —"):
        line = reason
    else:
        line = f"Blocked — {reason}"
    tag = (mention or "").strip()
    if tag and tag not in line:
        return f"{line}. {tag}"
    return line


def should_open_auto_github_issue(
    *,
    origin_line: str | None,
    next_owner: str | None,
) -> bool:
    """Return True only when there is no origin line and no named next owner.

    A named ``@Debra`` (or any origin line) is already the handoff. Opening a
    GitHub issue then is a #44-style duplicate.
    """
    if (origin_line or "").strip():
        return False
    if (next_owner or "").strip():
        return False
    return True


def is_host_deny_result(cli_result: Any) -> bool:
    """Return True when a CLI / host-access result is a host-path deny."""
    if getattr(cli_result, "ok", True):
        return False
    if getattr(cli_result, "consent_required", False):
        return False
    if getattr(cli_result, "approval_required", False):
        return False
    if str(getattr(cli_result, "kind", "") or "") == "host_deny":
        return True
    data = getattr(cli_result, "data", None) or {}
    blob = " ".join(
        [
            str(getattr(cli_result, "detail", "") or ""),
            str(data.get("error") or ""),
        ]
    ).lower()
    return any(marker in blob for marker in _HOST_DENY_MARKERS)


def _nest_git_block_why(cli_result: Any) -> str:
    """Prefer the bad-creds why when the CLI error names a rejected token."""
    data = getattr(cli_result, "data", None) or {}
    blob = " ".join(
        [
            str(getattr(cli_result, "detail", "") or ""),
            str(data.get("error") or ""),
        ]
    ).lower()
    if "didn’t accept" in blob or "didn't accept" in blob:
        return NEST_GIT_BAD_CREDS_WHY
    return NEST_GIT_WHY


def is_nest_git_block_result(cli_result: Any) -> bool:
    """Return True when a CLI result is a nest git auth fail-closed block."""
    if getattr(cli_result, "ok", True):
        return False
    if getattr(cli_result, "consent_required", False):
        return False
    if getattr(cli_result, "approval_required", False):
        return False
    if str(getattr(cli_result, "kind", "") or "") == _NEST_GIT_DENY_KIND:
        return True
    data = getattr(cli_result, "data", None) or {}
    blob = " ".join(
        [
            str(getattr(cli_result, "detail", "") or ""),
            str(data.get("error") or ""),
        ]
    ).lower()
    return any(marker in blob for marker in _NEST_GIT_MARKERS)


def is_shell_executor_deny_result(cli_result: Any) -> bool:
    """Return True when a CLI result is a Shell Executor deny (not a wait)."""
    if getattr(cli_result, "ok", True):
        return False
    if getattr(cli_result, "consent_required", False):
        return False
    if getattr(cli_result, "approval_required", False):
        return False
    if str(getattr(cli_result, "kind", "") or "") == _SHELL_EXECUTOR_DENY_KIND:
        return True
    data = getattr(cli_result, "data", None) or {}
    blob = " ".join(
        [
            str(getattr(cli_result, "detail", "") or ""),
            str(data.get("error") or ""),
        ]
    ).lower()
    return any(marker in blob for marker in _SHELL_EXECUTOR_MARKERS)


def surface_cli_gate_block(
    result: dict[str, Any],
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
    cli_result: Any,
) -> None:
    """Post Blocked — {why} when a CLI result is a named gate deny."""
    if is_host_deny_result(cli_result):
        surface_blocked_origin(
            result,
            agent=agent,
            trigger=trigger,
            why=HOST_DENY_WHY,
            kind=HOST_DENY_KIND,
        )
        return
    if is_shell_executor_deny_result(cli_result):
        surface_blocked_origin(
            result,
            agent=agent,
            trigger=trigger,
            why=SHELL_EXECUTOR_WHY,
            kind=SHELL_EXECUTOR_BLOCK_KIND,
        )
        return
    if is_nest_git_block_result(cli_result):
        surface_blocked_origin(
            result,
            agent=agent,
            trigger=trigger,
            why=_nest_git_block_why(cli_result),
            kind=NEST_GIT_BLOCK_KIND,
        )


def finish_blocked_origin(
    result: dict[str, Any],
    *,
    agent: Agent,
    content: str,
    mention: str | None,
) -> None:
    """Wake the tagged next owner. Persist reads ``auto_github_issue``."""
    from core.agent_loop import activity_runtime

    posted = bool(result.get("origin_status_messages"))
    origin_line = content if posted else None
    wakes = wake_mentioned_next_owner(agent=agent, content=content, posted=result)
    if wakes:
        result.setdefault("trigger_requests", []).extend(wakes)
    result["auto_github_issue"] = should_open_auto_github_issue(
        origin_line=origin_line,
        next_owner=mention,
    )
    result["auto_github"] = {
        "title": content,
        "body": content,
        "task_id": activity_runtime.get_active_task_id(agent.id),
        "origin_line": origin_line,
        "next_owner": mention,
    }


def surface_blocked_origin(
    result: dict[str, Any],
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
    why: str,
    kind: str,
) -> str:
    """Persist ``Blocked — {why}. @NextOwner``, wake them, and gate auto GH."""
    from core.agent_loop.soft_blocks import next_owner_mention
    from core.agent_loop.task_origin_mirrors import (
        attach_operator_status_line,
        named_origin_line,
        persist_unbound_status_line,
    )
    from core.agent_loop import activity_runtime

    mention = next_owner_mention(agent, trigger=trigger)
    content = format_blocked_line(why, mention)
    task_id = activity_runtime.get_active_task_id(getattr(agent, "id", None) or "")
    task = db.get_task(task_id) if task_id else None
    if task is not None:
        attach_operator_status_line(
            result,
            task=task,
            agent=agent,
            kind=kind,
            reason=content,
            target_name=mention,
        )
    else:
        posted = persist_unbound_status_line(
            agent=agent,
            content=named_origin_line(agent, content),
            kind=kind,
            channel_id=_channel_id(trigger),
        )
        extras = result.setdefault("origin_status_messages", [])
        if posted.get("channel_message"):
            extras.append(posted["channel_message"])
            result.setdefault("channel_message", posted["channel_message"])
        if posted.get("chat_message"):
            extras.append(posted["chat_message"])
            result.setdefault("chat_message", posted["chat_message"])
    finish_blocked_origin(result, agent=agent, content=content, mention=mention)
    return content


def wake_mentioned_next_owner(
    *,
    agent: Agent,
    content: str,
    posted: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return one wake (or none) for the tagged next owner. Tag is not hope."""
    channel_message = posted.get("channel_message")
    if isinstance(channel_message, dict) and channel_message.get("channel_id"):
        return _wake_channel_owner(agent=agent, content=content, channel_message=channel_message)
    return _wake_chat_owner(agent=agent, content=content, posted=posted)


def _wake_channel_owner(
    *,
    agent: Agent,
    content: str,
    channel_message: dict[str, Any],
) -> list[dict[str, Any]]:
    channel_id = str(channel_message.get("channel_id") or "").strip()
    message_id = str(channel_message.get("message_id") or "").strip()
    if not channel_id or not message_id:
        return []
    target = _mentioned_member_id(content, channel_id=channel_id, author_name=agent.name)
    if not target:
        return []
    exclude = {
        str(member.get("id") or "").strip()
        for member in db.list_channel_member_details(channel_id)
        if str(member.get("id") or "").strip() and str(member.get("id") or "").strip() != target
    }
    exclude.add(agent.id)
    return start_channel_peer_round(
        channel_id=channel_id,
        message_id=message_id,
        content=content,
        from_name=agent.name,
        author_type="system",
        exclude_agent_ids=exclude,
        from_agent=agent.id,
    )


def _wake_chat_owner(
    *,
    agent: Agent,
    content: str,
    posted: dict[str, Any],
) -> list[dict[str, Any]]:
    from core.agent_loop import activity_runtime

    target = _mentioned_agent(content, author_name=agent.name)
    if target is None or target.id == agent.id:
        return []
    task_id = activity_runtime.get_active_task_id(agent.id)
    task = db.get_task(task_id) if task_id else None
    if task is not None:
        return [
            build_task_follow_up_trigger(
                task,
                recipient_agent_id=target.id,
                from_agent=agent.id,
                from_name=agent.name,
                content=content,
                attention_kind="blocker",
                source_channel="work",
            )
        ]
    chat = posted.get("chat_message") if isinstance(posted.get("chat_message"), dict) else {}
    return [
        {
            "agent_id": target.id,
            "trigger_type": "peer_message",
            "source_channel": "peer",
            "payload": {
                "content": content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "work",
                "source_message_id": chat.get("message_id"),
            },
        }
    ]


def _mentioned_member_id(content: str, *, channel_id: str, author_name: str) -> str | None:
    names = mention_names_for_channel(channel_id)
    target_name = _first_next_owner_name(content, names, author_name=author_name)
    if not target_name:
        return None
    needle = target_name.strip().lower()
    for member in db.list_channel_member_details(channel_id):
        if (str(member.get("name") or "").strip().lower() == needle) and member.get("id"):
            return str(member["id"])
    return None


def _mentioned_agent(content: str, *, author_name: str) -> Agent | None:
    names = [row.name for row in db.list_agents() if getattr(row, "name", None)]
    names.extend(HUMAN_MENTION_NAMES)
    target_name = _first_next_owner_name(content, names, author_name=author_name)
    if not target_name:
        return None
    needle = target_name.strip().lower()
    for row in db.list_agents():
        if (row.name or "").strip().lower() == needle:
            return row
    return None


def _first_next_owner_name(
    content: str,
    member_names: list[str],
    *,
    author_name: str,
) -> str | None:
    author = (author_name or "").strip().lower()
    for mention in extract_next_owner_mentions(content, member_names=member_names):
        if mention == "everyone":
            continue
        if mention in HUMAN_MENTION_NAMES:
            continue
        if author and mention.lower() == author:
            continue
        return mention
    return None


def _channel_id(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict):
        return None
    raw = trigger.get("channel_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
