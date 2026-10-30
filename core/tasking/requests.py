"""BossMod AI — Task request rules: the requester's own words, kept with the work.

A task's request is the message(s) that asked for the work, word for word,
with their authors (``TaskRequestMessage``). The system saves it; the agent
never writes it. When a turn turns a message into a record (accepting or
deferring a task, filing a backlog item, setting up a schedule), the
message(s) that turn showed as the current request are appended to the
record's request.

How it differs from a task's other inputs:

- ``description`` is written once, by whoever creates the task: for work
  that starts in chat, the accepting agent's brief, which says what the
  message does not (earlier context, the exact paths and limits agreed) and
  does not copy the request. Afterwards only the boss edits it; an agent's
  ``desc`` on a later accept by task id is noted on the task thread as its
  working brief instead.
- ``references`` are documents to read before starting.
- deliverables (the work contract) are output the assignee must produce.

The list is append only. A scope change adds the new message after the
first, and a message already saved (same author type, agent and content)
is not saved again, so a retried decision changes nothing. A child task
with no request of its own is shown its parent's, one level up, labelled as
the request behind the parent: the child's own description is its part.

This module holds the rules and the one formatter every agent-facing view of
a request uses (the prompt's Current Task block, a teammate's
``task_assigned`` trigger, ``task <id>``). It has no transport code and
imports nothing from the agent loop.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

import db
from core.boss import boss_label
from core.models.task import Task, TaskRequestMessage

logger = logging.getLogger(__name__)

_OWN_HEADING = (
    "request (what was asked, word for word, oldest first; where the description leaves something out, "
    "go by this and any document it names):"
)
_FORWARDED_HEADING = (
    'request behind the parent task "{parent_title}" (word for word, oldest first; '
    "your part is this task's description):"
)

_CHANNEL_TRIGGER_TYPES = frozenset({"channel_message", "channel_response"})
_SESSION_TRIGGER_TYPES = frozenset({"session_message", "session_response"})


def request_messages_for_trigger(trigger: dict[str, Any]) -> list[TaskRequestMessage]:
    """The message(s) a turn on ``trigger`` showed as the current request.

    The same message(s) ``prompts/runtime_block_trigger_event.md`` shows the
    agent, by trigger type:

    - ``human_chat``: ``content``, by the boss.
    - ``peer_message``: ``content``, by the agent ``from_agent`` /
      ``from_name``.
    - ``channel_message`` / ``channel_response``: the opener (``content``,
      by ``author_type`` / ``from_agent`` / ``from_name``), then, when the
      thread has a newer line, that line (``latest_content``, by
      ``latest_author_type`` / ``latest_from_agent`` / ``latest_from_name``).
    - ``session_message`` / ``session_response``: ``content``, by
      ``author_type`` / ``from_agent`` / ``from_name``.
    - ``task_update``: ``content``, by the agent ``from_agent`` when set,
      otherwise by the boss.
    - anything else (``task_assigned``, ``task_follow_up``,
      ``activity_resumed``, …): nothing; such a turn answers no message.

    A human author is named with the current boss label. A message whose
    content is blank is left out. A message whose author cannot be recorded
    (an agent author without an id or a name, or an author type outside
    human, agent and system) is left out too, with a warning naming the
    trigger type, which message it was and why: the request is context the
    record can do without, so a malformed trigger never fails the accept,
    defer or filing that saves it. The other messages are still returned.
    Pure apart from reading the boss label and that warning.

    Args:
        trigger: The turn's live trigger (payload keys are top-level).

    Returns:
        The messages in the order the trigger shows them; ``[]`` when the
        turn answers no message.
    """
    trigger_type = str(trigger.get("type") or "")

    def message(which: str, author_type: Any, prefix: str = "") -> TaskRequestMessage | None:
        return _message(
            author_type, trigger.get(f"{prefix}from_agent"), trigger.get(f"{prefix}from_name"),
            trigger.get(f"{prefix}content"), trigger_type=trigger_type, which=which,
        )

    if trigger_type == "human_chat":
        candidates = [message("content", "human")]
    elif trigger_type == "peer_message":
        candidates = [message("content", "agent")]
    elif trigger_type in _CHANNEL_TRIGGER_TYPES:
        candidates = [
            message("opener", trigger.get("author_type")),
            message("latest", trigger.get("latest_author_type"), prefix="latest_"),
        ]
    elif trigger_type in _SESSION_TRIGGER_TYPES:
        candidates = [message("content", trigger.get("author_type"))]
    elif trigger_type == "task_update":
        # The context_builder._conversation_speaker rule for this trigger.
        candidates = [message("content", "agent" if trigger.get("from_agent") else "human")]
    else:
        return []
    return [item for item in candidates if item is not None]


def _message(
    author_type: Any, agent_id: Any, name: Any, content: Any, *, trigger_type: str, which: str,
) -> TaskRequestMessage | None:
    """One request message, or ``None`` when ``content`` is blank or its author cannot be recorded.

    A human is named with the boss label and carries no agent id; any other
    author keeps the trigger's id (agents only) and name. A message the model
    refuses is logged as a warning naming ``trigger_type`` and ``which``
    message (``content``, ``opener`` or ``latest``) and left out.
    """
    text = str(content or "")
    if not text.strip():
        return None
    try:
        if author_type == "human":
            return TaskRequestMessage(author_type="human", author_name=boss_label(), content=text)
        return TaskRequestMessage(
            author_type=author_type,
            author_agent_id=(str(agent_id or "").strip() or None) if author_type == "agent" else None,
            author_name=str(name or ""),
            content=text,
        )
    except ValidationError as exc:
        logger.warning(
            "A %s trigger's %s message has no author that can be recorded, so it is left out of the request: %s",
            trigger_type,
            which,
            exc,
        )
        return None


def _message_key(message: TaskRequestMessage) -> tuple[str, str | None, str]:
    """What makes two saved messages the same message."""
    return message.author_type, message.author_agent_id, message.content


def merge_request_messages(
    existing: Sequence[TaskRequestMessage], new: Sequence[TaskRequestMessage],
) -> list[TaskRequestMessage]:
    """Append each message of ``new`` that is not already in ``existing``.

    Messages are matched by ``(author_type, author_agent_id, content)``; the
    name is an audit snapshot and does not make a message new. Pure.

    Args:
        existing: The messages already saved, oldest first.
        new: The messages to add, in order.

    Returns:
        ``existing`` followed by the messages of ``new`` it did not hold yet,
        in order (a repeat inside ``new`` is added once).
    """
    merged = list(existing)
    seen = {_message_key(message) for message in merged}
    for message in new:
        key = _message_key(message)
        if key in seen:
            continue
        seen.add(key)
        merged.append(message)
    return merged


def append_task_request(task_id: str, messages: Sequence[TaskRequestMessage]) -> Task:
    """Save ``messages`` as part of a task's request, after the ones it holds.

    The read and the write run in one transaction, so the append always
    lands on the list it read; nothing is written when every message is
    already saved. Must not be called inside another ``db.transaction()``:
    ``BEGIN IMMEDIATE`` does not nest.

    Args:
        task_id: The task whose request grows.
        messages: The messages to add, oldest first (usually
            :func:`request_messages_for_trigger`'s answer).

    Returns:
        The task as stored after the append; unchanged when ``messages`` is
        empty or adds nothing.

    Raises:
        ValueError: The task no longer exists.
    """
    if not messages:
        return _require_task(task_id)
    with db.transaction():
        task = _require_task(task_id)
        merged = merge_request_messages(task.request_messages, messages)
        if len(merged) == len(task.request_messages):
            return task
        updated = db.update_task(task_id, request_messages=merged)
    if updated is None:
        raise ValueError(f"Task {task_id} no longer exists")
    return updated


def request_payload_for_task(task: Task) -> dict[str, Any] | None:
    """The request a task's assignee works from: its own, or its parent's.

    A child task with no request of its own (a delegated part, a plan child)
    is shown the request behind its parent, labelled as such. One level
    only: a grandparent's request is never forwarded.

    Args:
        task: The task being shown.

    Returns:
        ``{"parent_title": str | None, "messages": [dumped
        TaskRequestMessage, ...]}``: the task's own messages with
        ``parent_title`` ``None``, else its parent's messages with the
        parent's title. ``None`` when neither has a request, or when the
        parent row is missing; a missing parent is logged as a warning,
        since the request is context the task can do without.
    """
    if task.request_messages:
        return {"parent_title": None, "messages": _dump(task.request_messages)}
    if not task.parent_task_id:
        return None
    parent = db.get_task(task.parent_task_id)
    if parent is None:
        logger.warning(
            "Task %s names parent task %s, which no longer exists; its request is not shown",
            task.id,
            task.parent_task_id,
        )
        return None
    if not parent.request_messages:
        return None
    return {"parent_title": parent.title, "messages": _dump(parent.request_messages)}


def format_request_for_context(payload: dict[str, Any] | None) -> list[str]:
    """Prompt lines for a request payload: a heading, then one line per message.

    The only request formatter: the prompt's Current Task block, the
    ``task_assigned`` trigger and ``task <id>`` all use it, so every view
    reads the same lines::

        request (what was asked, word for word, oldest first; …):
        [Boss]: Fix the three failures in /projects/app/preflight.log

    A forwarded request is headed ``request behind the parent task
    "<title>" (…)`` instead. A human author is named with the current boss
    label, so a rename shows at once; an agent or system author with the
    saved name. Content is unchanged, newlines included.

    Args:
        payload: :func:`request_payload_for_task`'s answer.

    Returns:
        The lines; ``[]`` for ``None`` (no request to show).

    Raises:
        ValueError: The payload holds no message.
        pydantic.ValidationError: A dumped message is malformed.
    """
    if payload is None:
        return []
    raw_messages = payload["messages"]
    if not raw_messages:
        raise ValueError("A request payload holds at least one message")
    messages = [
        item if isinstance(item, TaskRequestMessage) else TaskRequestMessage.model_validate(item)
        for item in raw_messages
    ]
    parent_title = payload.get("parent_title")
    lines = [_OWN_HEADING if parent_title is None else _FORWARDED_HEADING.format(parent_title=parent_title)]
    for message in messages:
        name = boss_label() if message.author_type == "human" else message.author_name
        lines.append(f"[{name}]: {message.content}")
    return lines


def _dump(messages: Sequence[TaskRequestMessage]) -> list[dict[str, Any]]:
    """JSON-ready dumps, the shape the turn's current-task dict and the API carry."""
    return [message.model_dump(mode="json") for message in messages]


def _require_task(task_id: str) -> Task:
    """The task, or a ``ValueError`` saying it no longer exists."""
    task = db.get_task(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} no longer exists")
    return task
