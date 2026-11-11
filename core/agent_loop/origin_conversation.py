"""BossMod AI — The conversation before a task: saved when a turn takes the task on, shown while it is worked.

When a turn that answers a DM or thread message (``human_chat``,
``peer_message``, ``channel_message``, ``channel_response``) accepts or
defers a task, the system saves the conversation that turn was shown, so the
work starts from what was discussed instead of from the request alone. It is
a task input of its own kind (``TaskOriginConversation``): the request is the
message that asked, word for word; the description is the agent's brief;
references are documents to read; this is everything said before the
request, as far as the "Conversation before the task" share allows.

What is saved: the turn's conversation window
(``conversation_notes.load_thread_window``: notes plus verbatim rows, read
with the same arguments the turn's history used), without the trigger's own
message and without any row equal to a saved request message, fitted newest
first. A later accept from a conversation replaces the snapshot; a capture
that yields nothing keeps the stored one. Meetings are not covered.

The window rule (:func:`fit_newest`): walk the newest message first and stop
at the first one that does not fit. The notes are the oldest entry, so they
are kept only when every verbatim row fit; a gap between the notes and the
oldest row shown can never happen. The rule runs at capture, with the budget
then, and again at render, with the budget now, so lowering the share later
takes effect at once.

Forwarding: a child task with no conversation of its own is shown its
parent's, labelled as such, one level only (as the request is,
``core/tasking/requests.py``).

Every work turn of the task shows it as one history entry before the task
thread (``prompt_history``), and the task detail shows the same view
(``GET /api/tasks/{id}/origin-conversation``). It is history, not part of the
``{{task}}`` block: the system prompt counts toward the measured
instructions reserve, and a conversation there would shrink every agent's
usable window.

This module owns these rules. It never imports ``prompt_history``, which
imports it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

import db
from core.agent_loop.conversation_notes import (
    NOTES_FROM_AGENT,
    ThreadScope,
    entry_tokens,
    load_thread_window,
    trigger_conversation_scope,
    window_entries,
)
from core.boss import boss_label
from core.context_budget import read_context_budget
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskMessage, TaskOriginConversation
from core.tasking.requests import request_messages_for_trigger

logger = logging.getLogger(__name__)

ORIGIN_ID_PREFIX = "task-origin:"
ORIGIN_FROM_NAME = "Before this task"

_OWN_HEADING = (
    "The conversation in {where} that this task came from, as it stood when the task was taken on "
    "(oldest first). The request itself is under Current Task."
)
_FORWARDED_HEADING = (
    'The conversation in {where} that the parent task "{parent_title}" came from, as it stood when that '
    "task was taken on (oldest first). Your part is this task's description."
)


def _messages_from_window(entries: list[dict[str, Any]], *, trigger_type: str) -> list[TaskMessage]:
    """Map prompt-history rows to saved messages, oldest first.

    The boss's rows (``HUMAN_SENDER_ID``, in DMs and threads alike) are
    human, named with the boss label; ``NOTES_FROM_AGENT`` rows (the notes
    entry, a thread's system lines) are system, under their ``from_name``;
    anything else is that agent. A row the model refuses (blank content or
    name) is optional context: it is left out with a warning naming the
    trigger type and the row.
    """
    messages: list[TaskMessage] = []
    for entry in entries:
        sender = entry.get("from_agent")
        try:
            if sender == HUMAN_SENDER_ID:
                message = TaskMessage(author_type="human", author_name=boss_label(), content=entry.get("content"))
            elif sender == NOTES_FROM_AGENT:
                message = TaskMessage(
                    author_type="system", author_name=entry.get("from_name"), content=entry.get("content"),
                )
            else:
                message = TaskMessage(
                    author_type="agent", author_agent_id=sender, author_name=entry.get("from_name"),
                    content=entry.get("content"),
                )
        except ValidationError as exc:
            logger.warning(
                "A %s turn's conversation row %s cannot be saved with the task, so it is left out: %s",
                trigger_type, entry.get("id"), exc,
            )
            continue
        messages.append(message)
    return messages


def _line(message: TaskMessage) -> str:
    """The one rendered form of a saved message, ``[Name]: content``, for counting and display.

    A human author is named with the current boss label, as
    ``conversation_notes._entry_text`` names the boss's rows.
    """
    name = boss_label() if message.author_type == "human" else message.author_name
    return f"[{name}]: {message.content}"


def _message_key(message: TaskMessage) -> tuple[str, str | None, str]:
    """What makes a window row the same message as a saved request message (content is stored stripped)."""
    return message.author_type, message.author_agent_id, message.content


def fit_newest(messages: Sequence[TaskMessage], *, budget_tokens: int) -> list[TaskMessage]:
    """Keep the newest messages that fit ``budget_tokens``, oldest first.

    Walks the newest message first, adding each one's ``[Name]: content``
    tokens, and stops at the first message that would pass the budget, so
    it never keeps a gap: an older message is kept only when every newer one
    was. The notes, listed first, are therefore kept only when every newer
    row fit. Unlike the history windows, the newest message is not kept when
    it alone is over the budget. Pure apart from token counting.

    Args:
        messages: The messages, oldest first.
        budget_tokens: The most tokens the kept lines may take.

    Returns:
        The kept messages, oldest first; empty when the newest alone does
        not fit.

    Raises:
        Exception: Whatever the tokenizer raises; never mapped to 0.
    """
    kept: list[TaskMessage] = []
    used = 0
    for message in reversed(messages):
        size = entry_tokens(_line(message))
        if used + size > budget_tokens:
            break
        used += size
        kept.append(message)
    kept.reverse()
    return kept


def _source_names(scope: ThreadScope, agent: Agent, trigger: dict[str, Any], task_id: str) -> list[str] | None:
    """The label names for a capture, or ``None`` (with a warning) when one cannot be named.

    ``human_dm`` -> the agent; ``agent_dm`` -> the agent, then the trigger's
    ``from_name``; ``channel`` -> the thread's name. A blank name or a
    missing thread row means nothing is saved: the conversation is context
    the task can do without.
    """
    if scope.kind == "human_dm":
        names = [agent.name]
    elif scope.kind == "agent_dm":
        names = [agent.name, str(trigger.get("from_name") or "")]
    else:
        channel = db.get_channel(scope.key)
        if channel is None:
            logger.warning(
                "No conversation before task %s was saved: thread %s no longer exists", task_id, scope.key,
            )
            return None
        names = [channel.name]
    if any(not name.strip() for name in names):
        logger.warning(
            "No conversation before task %s was saved: the %s conversation has a blank name in %r",
            task_id, scope.kind, names,
        )
        return None
    return names


def save_turn_origin(agent: Agent, trigger: dict[str, Any], task_id: str) -> None:
    """Save the conversation this turn was shown as the conversation before ``task_id``.

    Called by ``decision_task_bind`` on the task an accept or defer lands on
    (created, bound by title, or bound by ``data.task.id``), after its
    request is saved, outside any transaction. A turn that answers no
    conversation (a task wake, a resume, a meeting) saves nothing.

    The window is the one the turn's history read: the same scope
    (:func:`conversation_notes.trigger_conversation_scope`), the agent's
    ``earliest_ts_allowed`` for a DM (threads ignore it, as their history
    does), the trigger's own message excluded. Rows equal to a request
    message (``(author_type, author_agent_id, content)``) are dropped, which
    leaves out a thread's ``latest_*`` line; then :func:`fit_newest` fits
    the rest to the current "Conversation before the task" budget. A stored
    snapshot is replaced only when this capture saves something.

    Nothing is written, and the reason is logged, when no message remains
    (INFO; a stored snapshot is kept), when the newest message alone is over
    the budget (WARNING naming its tokens and the budget), or when the
    conversation cannot be named (WARNING, :func:`_source_names`).

    Args:
        agent: The agent whose turn took the task on.
        trigger: The turn's live trigger.
        task_id: The task the accept or defer landed on.

    Raises:
        core.config.ConfigError: A conversation-memory or Context Window
            setting is missing or invalid.
        Exception: Whatever the database or the tokenizer raises; nothing is
            caught broadly.
    """
    scope = trigger_conversation_scope(agent.id, trigger)
    if scope is None:
        return
    trigger_type = str(trigger.get("type") or "")
    policy = db.ensure_agent_prompt_history_policy(agent.id)
    window = load_thread_window(
        scope,
        earliest_ts=policy.earliest_ts_allowed if scope.kind != "channel" else None,
        exclude_message_id=str(trigger.get("source_message_id") or "").strip() or None,
        perspective_agent_id=agent.id,
    )
    asked = {_message_key(message) for message in request_messages_for_trigger(trigger)}
    rows = [
        message
        for message in _messages_from_window(window_entries(window), trigger_type=trigger_type)
        if _message_key(message) not in asked
    ]
    if not rows:
        logger.info(
            "No conversation before task %s was saved: the %s turn's conversation holds nothing but the request",
            task_id, trigger_type,
        )
        return
    budget = read_context_budget().origin
    fitted = fit_newest(rows, budget_tokens=budget)
    if not fitted:
        logger.warning(
            "No conversation before task %s was saved: its newest message is %d tokens, over the %d-token "
            "Conversation before the task budget",
            task_id, entry_tokens(_line(rows[-1])), budget,
        )
        return
    names = _source_names(scope, agent, trigger, task_id)
    if names is None:
        return
    db.set_task_origin_conversation(TaskOriginConversation(
        task_id=task_id,
        source_kind=scope.kind,
        source_names=names,
        messages=fitted,
        captured_at=datetime.now(timezone.utc),
    ))


def _where(origin: TaskOriginConversation) -> str:
    """The conversation's place, in the words the heading and the task detail use."""
    names = origin.source_names
    if origin.source_kind == "human_dm":
        return f"the DM between {boss_label()} and {names[0]}"
    if origin.source_kind == "agent_dm":
        return f"the DM between {names[0]} and {names[1]}"
    return f'the thread "{names[0]}"'


def _resolve(task: Task, *, budget_tokens: int) -> tuple[TaskOriginConversation, list[TaskMessage], str | None] | None:
    """The snapshot a task shows, its messages fitted to ``budget_tokens``, and the parent's title when forwarded."""
    origin = db.get_task_origin_conversation(task.id)
    parent_title: str | None = None
    if origin is None:
        if not task.parent_task_id:
            return None
        parent = db.get_task(task.parent_task_id)
        if parent is None:
            logger.warning(
                "Task %s names parent task %s, which no longer exists; the conversation before it is not shown",
                task.id, task.parent_task_id,
            )
            return None
        origin = db.get_task_origin_conversation(parent.id)
        if origin is None:
            return None
        parent_title = parent.title
    messages = fit_newest(origin.messages, budget_tokens=budget_tokens)
    if not messages:
        logger.info(
            "The conversation before task %s is not shown: its newest message is over the %d-token "
            "Conversation before the task budget",
            task.id, budget_tokens,
        )
        return None
    return origin, messages, parent_title


def origin_view_for_task(task: Task, *, budget_tokens: int) -> dict[str, Any] | None:
    """The conversation before a task, as its work turns see it: the one view the prompt and the API render.

    The task's own snapshot, else its parent's (one level; a grandparent's
    is never forwarded), re-fitted with :func:`fit_newest` to
    ``budget_tokens``, so a lowered share takes effect on stored snapshots.

    Args:
        task: The task being shown.
        budget_tokens: The current "Conversation before the task" budget
            (``ContextBudget.origin``).

    Returns:
        ``{"task_id", "parent_title", "where", "messages"}``: ``task_id`` is
        the task whose conversation it is (the parent's when forwarded),
        ``parent_title`` is ``None`` for the task's own and the parent's
        title when forwarded, ``where`` names the conversation ("the DM
        between Boss and Jim", 'the thread "Compression Engine"'), and
        ``messages`` are ``TaskMessage`` dumps, oldest first. ``None`` when
        neither the task nor its parent has one, when the parent row is
        missing (logged as a warning), or when nothing fits the budget
        (logged at INFO).

    Raises:
        pydantic.ValidationError: A stored snapshot is malformed.
        Exception: Whatever the database or the tokenizer raises.
    """
    resolved = _resolve(task, budget_tokens=budget_tokens)
    if resolved is None:
        return None
    origin, messages, parent_title = resolved
    return {
        "task_id": origin.task_id,
        "parent_title": parent_title,
        "where": _where(origin),
        "messages": [message.model_dump(mode="json") for message in messages],
    }


def origin_history_entry(task_id: str, *, budget_tokens: int) -> dict[str, Any] | None:
    """The conversation before a task as one prompt-history entry for its work turns.

    It renders as ``[Before this task]: <heading>`` followed by one
    ``[Name]: content`` line per message, oldest first, a human named with
    the current boss label. The heading says where the conversation was and,
    when it is the parent's, that the task's own part is its description.
    ``budget_tokens`` covers the lines; the one-line heading rides in the
    room the shares leave free, as the trigger message does.

    Args:
        task_id: The task the work turn is bound to.
        budget_tokens: The current "Conversation before the task" budget
            (``ContextBudget.origin``).

    Returns:
        ``{"id": "task-origin:<task_id>", "from_agent": "__system__",
        "from_name": "Before this task", "content": ...}``, or ``None`` when
        there is nothing to show (:func:`origin_view_for_task`) or the task
        no longer exists (logged as a warning).

    Raises:
        pydantic.ValidationError: A stored snapshot is malformed.
        Exception: Whatever the database or the tokenizer raises.
    """
    task = db.get_task(task_id)
    if task is None:
        logger.warning("Task %s no longer exists; no conversation before it is shown to its work turn", task_id)
        return None
    resolved = _resolve(task, budget_tokens=budget_tokens)
    if resolved is None:
        return None
    origin, messages, parent_title = resolved
    where = _where(origin)
    heading = (
        _OWN_HEADING.format(where=where)
        if parent_title is None
        else _FORWARDED_HEADING.format(where=where, parent_title=parent_title)
    )
    return {
        "id": ORIGIN_ID_PREFIX + task_id,
        "from_agent": NOTES_FROM_AGENT,
        "from_name": ORIGIN_FROM_NAME,
        "content": heading + "\n" + "\n".join(_line(message) for message in messages),
    }
