"""BossMod AI — the boss's note when an agent changes its own memory.

A memory is shown to the agent on every turn, so the boss must never have to
discover a change later: every agent add, replace and remove posts one system
line in the boss's DM with that agent, whatever thread the turn ran in. Add
and replace link the line to the memory's number, so the chat can open the
desk's Memory layer on that row; a remove shows the text it dropped, since
there is nothing left to open. Kept apart from core/agent_loop/standing_prefs.py
so the memory store stays free of the chat layer, the same split as
core/scheduling/notices.py.
"""

from __future__ import annotations

from typing import Any, Literal

from core.agent_loop.notifications import ChatNotification, persist_chat_notification
from core.agent_loop.standing_prefs import Memory
from core.models import Agent

MemoryChange = Literal["add", "replace", "remove"]


def memory_change_line(agent: Agent, change: MemoryChange, memory: Memory) -> str:
    """Return the note's sentence for one memory change.

    Args:
        agent: The agent that changed its memory.
        change: What it did.
        memory: The memory as stored after the change (as it was, for ``remove``).

    Returns:
        ``"<Agent> saved a memory"``, ``"<Agent> updated a memory"``, or
        ``"<Agent> removed a memory: “<text>”"``.

    Raises:
        ValueError: ``change`` is not one of the three changes.
    """
    if change == "add":
        return f"{agent.name} saved a memory"
    if change == "replace":
        return f"{agent.name} updated a memory"
    if change == "remove":
        return f"{agent.name} removed a memory: “{memory.text}”"
    raise ValueError(f"unknown memory change {change!r}")


def note_memory_change(agent: Agent, change: MemoryChange, memory: Memory) -> dict[str, Any]:
    """Persist the boss's DM note for one change an agent made to its memory.

    Persisted through ``persist_chat_notification`` directly, not
    ``persist_origin_system_note``: that helper hides a line identical to a
    recent one, and two saves in a row both read "saved a memory", so each
    change must get its own line. The note is never shown to the agent
    (``prompt_visibility=False``): the memory itself is already in its prompt
    every turn.

    It is not broadcast here: callers may run off the event loop (the
    ``memory`` command; the decision turn's ``remember`` save runs it through
    ``off_request_loop``), so the caller hands the persisted line back for
    the turn to broadcast (``data["origin_chrome"]`` on a CLI result, or the
    turn result's ``origin_status_messages``).

    Args:
        agent: The agent that changed its memory.
        change: What it did.
        memory: The memory as stored after the change (as it was, for ``remove``).

    Returns:
        ``{"chat_message": {...}}``, the persisted line's broadcast payload.
        Its ``memory_id`` is the memory's number for add and replace, and
        ``None`` for remove.

    Raises:
        ValueError: ``change`` is not one of the three changes.
    """
    line = memory_change_line(agent, change, memory)
    posted = persist_chat_notification(
        agent,
        ChatNotification(
            kind="memory",
            content=line,
            source_channel="chat",
            policy="all",
            prompt_visibility=False,
            memory_id=memory.id if change != "remove" else None,
        ),
    )
    return {"chat_message": posted}
