"""Agent schedules: recurring work that becomes a task on each occurrence.

Thin: the rules live in core/scheduling/service.py (every call here acts
as the operator, ``service.OPERATOR``, who may change any schedule and its
``agent_can_change`` lock) and the models validate the payloads, so this
maps their errors to HTTP. Every mutation then asks the runtime worker to
sync its timetable (``service.request_reload``, a de-duplicated runtime
command) and tells the UI through a ``schedule_changed`` activity carrying
the agent id, so the agent's desk repaints.

Run now (``POST /schedules/{id}/run``) creates the run's task here, in the
app process, as ``POST /api/tasks`` does, and wakes the worker through
``runtime_services.enqueue_trigger``; it changes nothing timing-related, so
it queues no reload. The preview (``POST /schedules/preview``) is pure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

import db
from api.websocket import manager
from core.agent_loop.task_origin_mirrors import broadcast_origin_line
from core.models.schedule import (
    AgentSchedule,
    ScheduleCreate,
    SchedulePreview,
    SchedulePreviewRequest,
    ScheduleRunResult,
    ScheduleUpdate,
    ScheduleView,
)
from core.runtime import runtime_services
from core.scheduling import runner, service
from core.scheduling.recurrence import describe, upcoming

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _announce(schedule: AgentSchedule, detail: str) -> None:
    """Ask the worker to sync its timetable, then tell the UI the agent's schedules changed."""
    service.request_reload()
    agent = db.get_agent(schedule.agent_id)
    await manager.broadcast_activity(
        event="schedule_changed",
        detail=detail,
        agent_name=agent.name if agent is not None else None,
        extra={"agent_id": schedule.agent_id, "schedule_id": schedule.id},
    )


@router.get("/agents/{agent_id}/schedules")
async def list_agent_schedules(agent_id: str) -> list[ScheduleView]:
    """List one agent's schedules, oldest first, each with its next run.

    Raises:
        HTTPException: 404 when the agent does not exist.
    """
    if db.get_agent(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    now = _now()
    return [service.to_view(item, now=now) for item in db.list_schedules_for_agent(agent_id)]


@router.post("/agents/{agent_id}/schedules", status_code=201)
async def create_agent_schedule(agent_id: str, body: ScheduleCreate) -> ScheduleView:
    """Create a schedule for an agent.

    Raises:
        HTTPException: 404 when the agent does not exist; FastAPI answers
            422 for an invalid body or rule.
    """
    try:
        schedule = service.create_schedule(agent_id, body, actor=service.OPERATOR)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _announce(schedule, f'Schedule "{schedule.title}" created')
    return service.to_view(schedule, now=_now())


# Declared before every /schedules/{schedule_id} route. None of those takes
# POST on a one-segment path today, so it could not shadow this anyway; the
# order keeps that true if one is added.
@router.post("/schedules/preview")
async def preview_schedule(body: SchedulePreviewRequest) -> SchedulePreview:
    """The draft rule's summary and next ``count`` runs from now. No side effects.

    Raises:
        HTTPException: FastAPI answers 422 for an invalid rule or a
            ``count`` outside 1..20.
    """
    return SchedulePreview(
        summary=describe(body.recurrence),
        next_runs=upcoming(body.recurrence, after=_now(), count=body.count),
    )


@router.post("/schedules/{schedule_id}/run", status_code=201)
async def run_schedule_now(schedule_id: str) -> ScheduleRunResult:
    """Run the saved schedule once, now, enabled or not ("Run now").

    Creates the task through the scheduled path (core/scheduling/runner.py
    ``run_now``), wakes the agent, pushes the DM "Created" line live, and
    announces ``task_created`` and ``schedule_ran``.

    Raises:
        HTTPException: 404 when the schedule does not exist; 409 with
            ``{"reason", "detail", "task_id"}`` when the agent is on
            vacation or the last run is still open; 500 with the recorded
            detail when creating the task failed.
    """
    try:
        run = await asyncio.to_thread(runner.run_now, schedule_id, now=_now())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except runner.ScheduleRunRefused as exc:
        raise HTTPException(409, {"reason": exc.reason, "detail": exc.message, "task_id": exc.task_id}) from exc
    except runner.ScheduleRunFailed as exc:
        raise HTTPException(500, exc.detail) from exc
    if run.task is None:
        raise HTTPException(500, "Run now returned no task")
    if run.trigger is not None:
        await runtime_services.enqueue_trigger(**run.trigger)
    await broadcast_origin_line(manager, run.origin_line)
    agent = db.get_agent(run.agent_id)
    agent_name = agent.name if agent is not None else None
    await manager.broadcast_activity(
        event="task_created", detail=f'Scheduled task "{run.task.title}" created', agent_name=agent_name,
    )
    await manager.broadcast_activity(
        event="schedule_ran",
        detail=f'Schedule "{run.title}" ran now',
        agent_name=agent_name,
        extra={"agent_id": run.agent_id, "schedule_id": run.schedule_id, "outcome": "fired"},
    )
    schedule = db.get_schedule(run.schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    return ScheduleRunResult(schedule=service.to_view(schedule, now=_now()), task=run.task)


@router.patch("/schedules/{schedule_id}")
async def update_agent_schedule(schedule_id: str, body: ScheduleUpdate) -> ScheduleView:
    """Change the fields present in the body, ``agent_can_change`` included.

    Raises:
        HTTPException: 404 when the schedule does not exist; FastAPI answers
            422 for an invalid body.
    """
    try:
        schedule = service.update_schedule(schedule_id, body, actor=service.OPERATOR)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _announce(schedule, f'Schedule "{schedule.title}" updated')
    return service.to_view(schedule, now=_now())


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_agent_schedule(schedule_id: str) -> Response:
    """Delete a schedule; the tasks its runs created stay on the board, detached.

    Raises:
        HTTPException: 404 when the schedule does not exist.
    """
    try:
        schedule = service.delete_schedule(schedule_id, actor=service.OPERATOR)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _announce(schedule, f'Schedule "{schedule.title}" deleted')
    return Response(status_code=204)
