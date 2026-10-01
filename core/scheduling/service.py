"""BossMod AI — app-side schedule operations, for the API.

Create, edit and delete a schedule, and build the view the API returns.
Clock-free: the caller passes ``now``, so "Next run" is testable and is the
same pure ``next_occurrence`` the runtime worker fires from. Telling the
worker to reload is the caller's (api/routes/schedules.py), not this
module's: this layer never reaches the runtime.
"""

from __future__ import annotations

from datetime import datetime

import db
from core.models.schedule import AgentSchedule, ScheduleCreate, ScheduleUpdate, ScheduleView
from core.scheduling.recurrence import describe, next_occurrence


def create_schedule(agent_id: str, body: ScheduleCreate) -> AgentSchedule:
    """Store a new schedule for an existing agent.

    Args:
        agent_id: The agent whose recurring work this is.
        body: The validated create payload.

    Returns:
        The stored schedule.

    Raises:
        ValueError: ``Agent not found``.
    """
    if db.get_agent(agent_id) is None:
        raise ValueError("Agent not found")
    return db.create_schedule(
        agent_id=agent_id,
        title=body.title,
        instructions=body.instructions,
        recurrence=body.recurrence,
        notification_policy=body.notification_policy,
        enabled=body.enabled,
    )


def update_schedule(schedule_id: str, body: ScheduleUpdate) -> AgentSchedule:
    """Apply the fields present in ``body`` (``model_fields_set``) and nothing else.

    Args:
        schedule_id: The schedule to edit.
        body: The validated edit payload; it always carries at least one field.

    Returns:
        The stored schedule.

    Raises:
        ValueError: ``Schedule not found``.
    """
    fields = {name: getattr(body, name) for name in body.model_fields_set}
    updated = db.update_schedule(schedule_id, **fields)
    if updated is None:
        raise ValueError("Schedule not found")
    return updated


def delete_schedule(schedule_id: str) -> None:
    """Delete a schedule; the tasks its runs created stay, detached.

    Raises:
        ValueError: ``Schedule not found``.
    """
    if not db.delete_schedule(schedule_id):
        raise ValueError("Schedule not found")


def to_view(schedule: AgentSchedule, *, now: datetime) -> ScheduleView:
    """Build the API view: the stored row plus what the UI shows.

    Args:
        schedule: A stored schedule.
        now: The aware request time "Next run" is computed after.

    Returns:
        The view with ``summary`` (``describe``), ``next_run_at`` (``None``
        while disabled) and ``last_task_status`` (``None`` when there is no
        last task or it no longer exists).

    Raises:
        ValueError: ``now`` is naive.
    """
    last_task = db.get_task(schedule.last_task_id) if schedule.last_task_id else None
    return ScheduleView(
        **schedule.model_dump(),
        summary=describe(schedule.recurrence),
        next_run_at=next_occurrence(schedule.recurrence, after=now) if schedule.enabled else None,
        last_task_status=last_task.status if last_task is not None else None,
    )
