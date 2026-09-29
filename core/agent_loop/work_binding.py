"""BossMod AI — The live work a turn is bound to.

One question, one answer: "what live work may this turn act on?" Every
turn-facing site (the turn loop, action handlers, the CLI bridge, blocked
lines, progress reporters) asks this module. State-maintenance code (status
derivation, dispatch eligibility, watchdogs) keeps asking
``activity_runtime`` directly, because it must see the truth.

A turn opens a scope with :func:`bind_turn`. Inside a *detached* scope (a
trigger whose policy is ``detached_from_work``, or a resume stamped
``detached_origin``) the scoped agent reads as bound to nothing, so the paused
task's transcript and state are never touched. Everywhere else — attached
turns, API code, watchdogs, another agent's lookups — the binding is the real
``activity_runtime`` answer.

The scope is a ``ContextVar``: each trigger runs in its own asyncio task, and
``asyncio.to_thread`` (how ``core.loop_breathing`` hops CLI work off the loop)
copies the context, so the scope follows the turn and never crosses turns.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.policies import get_trigger_policy
from core.models import Activity

# Resume-payload key: the turn that opened this approval/consent was detached.
DETACHED_ORIGIN_KEY = "detached_origin"


@dataclass(frozen=True, slots=True)
class WorkBinding:
    """The live work a turn may act on.

    Attributes:
        activity: The agent's active activity of any kind.
        work_activity: The active activity when it is a ``work`` activity.
        task_id: The task bound to ``work_activity``.
    """

    activity: Activity | None
    work_activity: Activity | None
    task_id: str | None


_UNBOUND = WorkBinding(activity=None, work_activity=None, task_id=None)


@dataclass(frozen=True, slots=True)
class _TurnBinding:
    agent_id: str
    detached: bool


_turn_binding: ContextVar[_TurnBinding | None] = ContextVar("work_binding_turn", default=None)


def is_detached(trigger: dict[str, Any]) -> bool:
    """Return whether ``trigger`` runs detached from the agent's live work.

    The only place detachment is decided. The trigger policy registry
    declares it per type (``TriggerPolicy.detached_from_work``); a resume
    (CLI approval / consent) carries it forward as ``detached_origin: True``.

    Args:
        trigger: The live trigger dict of a turn (the dispatcher merges the
            queued payload onto it, so payload keys are top-level).

    Returns:
        ``True`` when the type's policy is detached or the trigger is stamped
        ``detached_origin`` with the literal ``True``.
    """
    if get_trigger_policy(str(trigger.get("type") or "")).detached_from_work:
        return True
    return trigger.get(DETACHED_ORIGIN_KEY) is True


@contextmanager
def bind_turn(agent_id: str, trigger: dict[str, Any]) -> Iterator[None]:
    """Scope one turn's work binding for ``agent_id``.

    Nesting is allowed (the inner scope wins until it exits); the scope is
    reset with its token on exit, including when the body raises.

    Args:
        agent_id: The agent whose turn this is.
        trigger: The turn's live trigger; :func:`is_detached` decides the mode.
    """
    token = _turn_binding.set(_TurnBinding(agent_id=agent_id, detached=is_detached(trigger)))
    try:
        yield
    finally:
        _turn_binding.reset(token)


def _unbound_for(agent_id: str) -> bool:
    """True inside a detached turn scope for this agent."""
    scope = _turn_binding.get()
    return scope is not None and scope.detached and scope.agent_id == agent_id


def current_turn_detached(agent_id: str) -> bool:
    """Return whether a detached turn scope for ``agent_id`` is active.

    Approval and consent creation sites persist this on the request row, so
    the operator's later resume turn runs detached too (``detached_origin``).
    Outside any turn scope, or in another agent's scope, it is ``False``.
    """
    return _unbound_for(agent_id)


def bound_work(agent_id: str) -> WorkBinding:
    """Return the live work the current turn may act on for ``agent_id``.

    Inside a detached scope for this agent the binding is empty. Otherwise
    (attached turn, another agent, or no scope at all) it is the real
    ``activity_runtime`` lookups.
    """
    if _unbound_for(agent_id):
        return _UNBOUND
    return WorkBinding(
        activity=activity_runtime.get_active_activity(agent_id),
        work_activity=activity_runtime.get_active_work_activity(agent_id),
        task_id=activity_runtime.get_active_task_id(agent_id),
    )


# The accessors return one field of ``bound_work`` each, with only that
# field's lookup (sites ask for one thing, often several times per command).


def bound_task_id(agent_id: str) -> str | None:
    """Return ``bound_work(agent_id).task_id``."""
    return None if _unbound_for(agent_id) else activity_runtime.get_active_task_id(agent_id)


def bound_work_activity(agent_id: str) -> Activity | None:
    """Return ``bound_work(agent_id).work_activity``."""
    return None if _unbound_for(agent_id) else activity_runtime.get_active_work_activity(agent_id)


def bound_activity(agent_id: str) -> Activity | None:
    """Return ``bound_work(agent_id).activity``."""
    return None if _unbound_for(agent_id) else activity_runtime.get_active_activity(agent_id)
