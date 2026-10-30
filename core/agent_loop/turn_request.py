"""BossMod AI — The message a turn is answering, for CLI commands that turn it into a record.

A ``backlog add`` or ``schedules add`` runs inside a turn, but a CLI command
is handed only the agent, its state and its cwd (``CliExecutionContext``),
never the trigger. So that such a record can keep the requester's own words
(``core/tasking/requests.py``), the turn opens a scope with
:func:`bind_turn_request` and the command asks this module.

It mirrors ``work_binding``'s scope: a ``ContextVar`` set for the whole
turn, which ``asyncio.to_thread`` (how CLI work leaves the event loop)
copies, so the scope follows the turn and never crosses turns. The scope
holds the live trigger dict, not a copy, and the messages are read when
asked: a channel's newest line, stamped onto the trigger after the scope
opened (``turn_context.stamp_channel_latest_line``), is included.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import db
from core.agent_loop.work_binding import bound_task_id
from core.models.task import TaskRequestMessage
from core.tasking.requests import request_messages_for_trigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TurnRequest:
    agent_id: str
    trigger: dict[str, Any]


_turn_request: ContextVar[_TurnRequest | None] = ContextVar("turn_request", default=None)


@contextmanager
def bind_turn_request(agent_id: str, trigger: dict[str, Any]) -> Iterator[None]:
    """Scope one turn's trigger for ``agent_id``, so its CLI commands can read the request.

    Nesting is allowed (the inner scope wins until it exits); the scope is
    reset with its token on exit, including when the body raises.

    Args:
        agent_id: The agent whose turn this is.
        trigger: The turn's live trigger dict. Held as is, so later stamps
            on it are seen.
    """
    token = _turn_request.set(_TurnRequest(agent_id=agent_id, trigger=trigger))
    try:
        yield
    finally:
        _turn_request.reset(token)


def turn_request_messages(agent_id: str) -> list[TaskRequestMessage]:
    """The message(s) the current turn of ``agent_id`` is answering.

    Read from the live trigger when called
    (``core/tasking/requests.py`` ``request_messages_for_trigger``).

    Args:
        agent_id: The agent asking, usually the CLI context's agent.

    Returns:
        The messages, oldest first. ``[]`` outside a turn scope, inside
        another agent's scope, or in a turn that answers no message (a work
        turn, a task wake): in each case there is no message being answered,
        so ``[]`` is the true answer, not a stand-in for a failure.
    """
    scope = _turn_request.get()
    if scope is None or scope.agent_id != agent_id:
        return []
    return request_messages_for_trigger(scope.trigger)


def turn_work_request_messages(agent_id: str) -> list[TaskRequestMessage]:
    """The message(s) the current turn answers, or else its bound task's own request.

    For records that are someone's standing ask whichever turn makes them:
    a schedule set up while working an accepted task ("every Monday send me
    X") is that task's request. The backlog does not use this: an item filed
    mid-task is the agent's own finding, and labelling the task's request
    as what was asked for it would mislead whoever picks it up.

    Args:
        agent_id: The agent asking, usually the CLI context's agent.

    Returns:
        :func:`turn_request_messages` when it has any; otherwise the
        request messages of the task the turn is bound to
        (``work_binding.bound_task_id``); otherwise ``[]``. A bound id that
        names no task is logged as a warning and gives ``[]``: the request
        is context the record can do without.
    """
    messages = turn_request_messages(agent_id)
    if messages:
        return messages
    task_id = bound_task_id(agent_id)
    if task_id is None:
        return []
    task = db.get_task(task_id)
    if task is None:
        logger.warning(
            "Agent %s is bound to task %s, which no longer exists; no request is saved",
            agent_id,
            task_id,
        )
        return []
    return list(task.request_messages)
