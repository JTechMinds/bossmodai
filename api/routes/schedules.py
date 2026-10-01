"""Agent schedules: recurring work that becomes a task on each occurrence.

Thin: the rules live in core/scheduling/service.py and the models validate
the payloads, so this maps their errors to HTTP. Every mutation then asks
the runtime worker to reload its timetable (a de-duplicated runtime
command) and tells the UI through a ``schedule_changed`` activity carrying
the agent id, so the agent's desk repaints.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

import db
from api.websocket import manager
from core.models.schedule import AgentSchedule, ScheduleCreate, ScheduleUpdate, ScheduleView
from core.runtime import runtime_services
from core.scheduling import service

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _announce(schedule: AgentSchedule, detail: str) -> None:
    """Reload the worker's timetable, then tell the UI the agent's schedules changed."""
    await runtime_services.reload_schedules()
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
        schedule = service.create_schedule(agent_id, body)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _announce(schedule, f'Schedule "{schedule.title}" created')
    return service.to_view(schedule, now=_now())


@router.patch("/schedules/{schedule_id}")
async def update_agent_schedule(schedule_id: str, body: ScheduleUpdate) -> ScheduleView:
    """Change the fields present in the body.

    Raises:
        HTTPException: 404 when the schedule does not exist; FastAPI answers
            422 for an invalid body.
    """
    try:
        schedule = service.update_schedule(schedule_id, body)
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
    schedule = db.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    try:
        service.delete_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _announce(schedule, f'Schedule "{schedule.title}" deleted')
    return Response(status_code=204)
