"""BossMod AI — schedule operations for both actors: the operator (API) and an agent (CLI).

Create, edit, switch on/off and delete a schedule, with one set of rules
whoever asks, so the rules hold however a change arrives:

- an agent manages only its own schedules; another agent's reads as not
  found, so its existence is not leaked;
- an agent may change a schedule only while ``agent_can_change`` is on, and
  only the operator sets that flag. A locked schedule answers with the
  ``internal_schedule_locked`` prompt, rendered with the operator's name;
- ``created_by`` records who set it up. Agent-created schedules start
  unlocked; operator-created ones start locked unless the operator says
  otherwise.

``request_reload`` tells the runtime worker to sync its timetable through a
runtime command row, so it works from the app (API, CLI simulator) and from
the worker (agent CLI) alike. ``to_view`` builds the API view, clock-free:
the caller passes ``now``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

import db
from core.default_prompts import render_default_prompt
from core.models.message import HUMAN_SENDER_ID
from core.models.schedule import AgentSchedule, ScheduleCreate, ScheduleView
from core.scheduling.recurrence import describe, next_occurrence

RELOAD_COMMAND = "reload_schedules"
_LOCKED_PROMPT_KEY = "internal_schedule_locked"
_LOCKED_PROMPT_PATHS = {"operator_name"}


@dataclass(frozen=True, slots=True)
class ScheduleActor:
    """Who is changing a schedule.

    Attributes:
        kind: ``operator`` (the API) or ``agent`` (its ``schedules`` command).
        agent_id: The agent's id for ``agent``; ``None`` for the operator.
    """

    kind: Literal["operator", "agent"]
    agent_id: str | None

    def __post_init__(self) -> None:
        if (self.kind == "agent") != bool(self.agent_id):
            raise ValueError("an agent actor needs its agent_id, and the operator has none")


OPERATOR = ScheduleActor("operator", None)


class ScheduleLocked(Exception):
    """An agent tried to change a schedule whose ``agent_can_change`` is off.

    Attributes:
        message: The rendered ``internal_schedule_locked`` sentence, for the agent.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ScheduleNotFound(ValueError):
    """No such schedule, or (for an agent) not one of its own."""


def render_locked_message() -> str:
    """The sentence a locked schedule answers an agent with, naming the operator.

    Rendered from the file-backed ``internal_schedule_locked`` prompt with
    ``operator_name`` from ``core.tasking.operator_actions.OPERATOR_NAME``,
    the one seam a configurable operator name would replace.

    Raises:
        core.llm.template_engine.TemplateError: The prompt is malformed.
    """
    from core.tasking.operator_actions import OPERATOR_NAME

    return render_default_prompt(
        _LOCKED_PROMPT_KEY, {"operator_name": OPERATOR_NAME}, allowed_paths=_LOCKED_PROMPT_PATHS,
    )


def create_schedule(agent_id: str, body: ScheduleCreate, *, actor: ScheduleActor) -> AgentSchedule:
    """Store a new schedule for an existing agent.

    ``created_by`` comes from the actor. An agent's schedule starts with
    ``agent_can_change`` on; the operator's starts with ``body.agent_can_change``
    when given, else off (important schedules are protected unless opted in).

    Args:
        agent_id: The agent whose recurring work this is.
        body: The validated create payload.
        actor: Who is creating it; an agent may only create for itself.

    Returns:
        The stored schedule.

    Raises:
        ValueError: ``Agent not found``; an agent creating for another
            agent; or an agent setting ``agent_can_change`` (operator-only).
    """
    if db.get_agent(agent_id) is None:
        raise ValueError("Agent not found")
    if actor.kind == "agent":
        if actor.agent_id != agent_id:
            raise ValueError("An agent can only schedule work for itself")
        if "agent_can_change" in body.model_fields_set:
            raise ValueError("Only the operator can set agent_can_change")
        created_by, agent_can_change = agent_id, True
    else:
        created_by, agent_can_change = HUMAN_SENDER_ID, bool(body.agent_can_change)
    return db.create_schedule(
        agent_id=agent_id,
        title=body.title,
        instructions=body.instructions,
        recurrence=body.recurrence,
        notification_policy=body.notification_policy,
        enabled=body.enabled,
        created_by=created_by,
        agent_can_change=agent_can_change,
    )


def _changeable(schedule_id: str, actor: ScheduleActor) -> AgentSchedule:
    """The schedule ``actor`` may change.

    Raises:
        ScheduleNotFound: No such schedule, or an agent's request for one that is not its own.
        ScheduleLocked: An agent's request while ``agent_can_change`` is off.
    """
    schedule = db.get_schedule(schedule_id)
    if schedule is None or (actor.kind == "agent" and schedule.agent_id != actor.agent_id):
        raise ScheduleNotFound("Schedule not found")
    if actor.kind == "agent" and not schedule.agent_can_change:
        raise ScheduleLocked(render_locked_message())
    return schedule


def update_schedule(schedule_id: str, body: BaseModel, *, actor: ScheduleActor) -> AgentSchedule:
    """Apply the fields present in ``body`` (``model_fields_set``) and nothing else.

    Args:
        schedule_id: The schedule to edit.
        body: A validated ``ScheduleUpdate`` (operator) or
            ``AgentScheduleUpdate`` (agent); it always carries a field.
        actor: Who is editing.

    Returns:
        The stored schedule.

    Raises:
        ScheduleNotFound: No such schedule (or, for an agent, not its own).
        ScheduleLocked: An agent editing a locked schedule.
        ValueError: An agent body carrying ``agent_can_change`` (operator-only).
    """
    _changeable(schedule_id, actor)
    fields = {name: getattr(body, name) for name in body.model_fields_set}
    if actor.kind == "agent" and "agent_can_change" in fields:
        raise ValueError("Only the operator can set agent_can_change")
    updated = db.update_schedule(schedule_id, **fields)
    if updated is None:
        raise ScheduleNotFound("Schedule not found")
    return updated


def set_enabled(schedule_id: str, enabled: bool, *, actor: ScheduleActor) -> AgentSchedule:
    """Switch a schedule on or off, under the same rules as an edit.

    Raises:
        ScheduleNotFound: No such schedule (or, for an agent, not its own).
        ScheduleLocked: An agent switching a locked schedule.
    """
    _changeable(schedule_id, actor)
    updated = db.update_schedule(schedule_id, enabled=enabled)
    if updated is None:
        raise ScheduleNotFound("Schedule not found")
    return updated


def delete_schedule(schedule_id: str, *, actor: ScheduleActor) -> AgentSchedule:
    """Delete a schedule; the tasks its runs created stay, detached.

    Returns:
        The schedule as it was, for the caller's announcement.

    Raises:
        ScheduleNotFound: No such schedule (or, for an agent, not its own).
        ScheduleLocked: An agent removing a locked schedule.
    """
    schedule = _changeable(schedule_id, actor)
    if not db.delete_schedule(schedule_id):
        raise ScheduleNotFound("Schedule not found")
    return schedule


def request_reload() -> None:
    """Ask the runtime worker to sync its schedule timetable with the database.

    Writes a ``reload_schedules`` runtime command, whatever process calls
    it, and rings the worker's doorbell when called in the app; the worker's
    command loop applies it on that ring or at its fallback poll. It is
    de-duplicated: while one is still open, another request adds nothing
    (a burst of edits is one sync). It only syncs; telling the UI about a
    change is the caller's job (the API route's broadcast, or an agent
    command's declared ``activity``). A row written while no worker runs is
    harmless: a starting worker clears open commands and loads every
    schedule fresh.
    """
    if not db.has_open_runtime_command([RELOAD_COMMAND]):
        db.create_runtime_command(RELOAD_COMMAND)
        # Local: a module-level import is circular (core.runtime.services's own
        # imports reach this module through the CLI runtime).
        from core.runtime.services import notify_runtime_command_queued

        notify_runtime_command_queued()


def to_view(schedule: AgentSchedule, *, now: datetime) -> ScheduleView:
    """Build the API view: the stored row plus what the UI shows.

    Args:
        schedule: A stored schedule.
        now: The aware request time "Next run" is computed after.

    Returns:
        The view with ``summary`` (``describe`` in 12-hour time: the view
        is operator-facing), ``next_run_at`` (``None`` while disabled),
        ``last_task_status`` (``None`` when there is no
        last task or it no longer exists) and ``created_by_name`` (the
        agent's name when an agent set it up; ``None`` for the operator or
        an agent since deleted).

    Raises:
        ValueError: ``now`` is naive.
    """
    last_task = db.get_task(schedule.last_task_id) if schedule.last_task_id else None
    creator = db.get_agent(schedule.created_by) if schedule.created_by != HUMAN_SENDER_ID else None
    return ScheduleView(
        **schedule.model_dump(),
        summary=describe(schedule.recurrence, clock="12h"),
        next_run_at=next_occurrence(schedule.recurrence, after=now) if schedule.enabled else None,
        last_task_status=last_task.status if last_task is not None else None,
        created_by_name=creator.name if creator is not None else None,
    )
