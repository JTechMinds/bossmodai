"""Floor list, create, rename and delete, and moving people, threads and projects between floors."""

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.websocket import manager
from core.bm_cli.floor_roots import floor_folder_exists, floor_root
from core.floor_moves import (
    MovePlanChanged,
    MoveRefused,
    ProjectExists,
    apply_move,
    list_projects,
    move_project,
    plan_move,
)
from core.floors import AgentOnVacation, FloorOccupantsChoiceRequired, delete_floor
from core.models.floor import Floor
from core.runtime import runtime_services
from core.tasking.transitions import IllegalTaskTransition
from db.floors import FloorNameTaken, create_floor, list_floors, rename_floor
import db

logger = logging.getLogger(__name__)

router = APIRouter()


def _floor_payloads() -> list[dict]:
    """Every floor as the UI reads it: the row plus ``has_folder``.

    ``has_folder`` says whether the floor's company folder is on disk, which
    is what the Delete layer uses to say its files move to Archived floors.
    One builder for the GET and the broadcast, so the two never disagree.
    """
    return [
        {**floor.model_dump(mode="json"), "has_folder": floor_folder_exists(floor.id)}
        for floor in list_floors()
    ]


async def _broadcast_floors() -> None:
    """Tell every open window the floor list changed, sending the whole list."""
    await manager.broadcast_floors_updated(_floor_payloads())


class FloorCreateBody(BaseModel):
    name: str


class FloorRenameBody(BaseModel):
    name: str


@router.get("/floors")
async def get_floors() -> list[dict]:
    """Return every co-mingle domain, each with ``has_folder``. Lobby is always present."""
    return _floor_payloads()


@router.post("/floors", status_code=201)
async def post_floor(body: FloorCreateBody) -> Floor:
    """Create a labeled floor and its company folder. An existing name returns that floor."""
    try:
        floor = create_floor(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    floor_root(floor.id)
    await _broadcast_floors()
    return floor


@router.patch("/floors/{floor_id}")
async def patch_floor(floor_id: str, body: FloorRenameBody) -> Floor:
    """Rename one floor. Lobby can be renamed; its id never changes.

    404 when the floor is missing, 409 when another floor already has the
    name (ignoring case), 400 when the name is blank or too long.
    """
    try:
        floor = rename_floor(floor_id, body.name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FloorNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await _broadcast_floors()
    return floor


@router.delete("/floors/{floor_id}")
async def remove_floor(floor_id: str, occupants: str | None = None):
    """Delete one floor. Its active threads are archived, its open work cancelled.

    ``occupants`` says what happens to the agents who live there:
    ``send_home`` (vacation) or ``delete``. It is required only when the
    floor has agents; without it that case answers 409
    ``{code: "occupants_choice_required", agent_count}``. Lobby and an
    unknown ``occupants`` value answer 400, a missing floor 404. The floor's
    company folder moves to Company › Archived floors (``folder_archived``).
    """
    # Imported here, not at module top: api.routes.agents is a sibling router
    # module, and these two helpers are the thread-archive broadcasts the
    # single-thread archive route already uses — one painter, not two.
    from api.routes.agents import _broadcast_archive_side_effects, _serialize_channel_summary

    doomed = {agent.id: agent.name for agent in db.list_agents() if agent.floor_id == floor_id}
    try:
        result = await delete_floor(
            floor_id,
            occupants=occupants,  # validated in core; an unknown value is a ValueError
            services=runtime_services,
            on_before_seal=_broadcast_archive_side_effects,
        )
    except FloorOccupantsChoiceRequired as exc:
        return JSONResponse(
            {
                "code": "occupants_choice_required",
                "agent_count": exc.agent_count,
                "message": str(exc),
            },
            status_code=409,
        )
    except IllegalTaskTransition as exc:
        # Same answer the single-thread archive route gives for a cancel that
        # hit a task mid-transition.
        raise HTTPException(
            409,
            f"Illegal task status transition: {exc.from_status} → {exc.to_status}",
        ) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    for channel_id in result.threads_archived:
        channel = db.get_channel(channel_id)
        if channel is None:
            # Archived a moment ago and gone now: something else deleted it.
            # Nothing to paint, but that is worth knowing.
            logger.warning("Archived thread %s vanished before its broadcast", channel_id)
            continue
        summary = _serialize_channel_summary(
            channel,
            members=db.list_channel_member_details(channel.id),
            latest_message=db.get_latest_channel_message(channel.id),
        )
        await manager.broadcast_channel_updated(summary)
    await manager.broadcast_world_state()
    await _broadcast_floors()
    for agent_id in result.agents_deleted:
        name = doomed.get(agent_id, agent_id)
        await manager.broadcast_activity(
            event="agent_deleted",
            detail=f'Agent "{name}" deleted',
            agent_name=name,
        )
    return asdict(result)


# ─── Moving people, threads and projects (core/floor_moves.py) ───


class MoveBody(BaseModel):
    """What to move to the floor in the path. Threads bring their members."""

    agent_ids: list[str] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)
    exclude_companion_ids: list[str] = Field(default_factory=list)


class MoveApplyBody(MoveBody):
    """A move, with the fingerprint of the plan the operator confirmed."""

    fingerprint: str


class ProjectMoveBody(BaseModel):
    """One project folder to bring onto the floor in the path."""

    project: str
    from_floor_id: str


def _plan_changed(exc: MovePlanChanged) -> JSONResponse:
    return JSONResponse({"code": "plan_changed", "message": str(exc)}, status_code=409)


@router.post("/floors/{floor_id}/move-plan")
async def post_move_plan(floor_id: str, body: MoveBody) -> dict:
    """What moving these people and threads here would do. Writes nothing.

    404 when the floor, an agent or a thread is missing; 409 when a chosen
    agent is on vacation; 400 when nothing is chosen, a thread is archived,
    something is already on this floor, or an excluded id is not a companion.
    """
    try:
        plan = plan_move(
            floor_id,
            agent_ids=body.agent_ids,
            channel_ids=body.channel_ids,
            exclude_companion_ids=body.exclude_companion_ids,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AgentOnVacation as exc:
        raise HTTPException(409, str(exc)) from exc
    except MoveRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return asdict(plan)


@router.post("/floors/{floor_id}/move")
async def post_move(floor_id: str, body: MoveApplyBody):
    """Apply a confirmed move plan.

    Answers the plan-move errors, plus 409 ``{code: "plan_changed"}`` when
    the plan is no longer what the operator confirmed. Broadcasts the world,
    every moved and every left thread, and the floor list.
    """
    # Imported here for the reason remove_floor gives: the channel painter is
    # the agents router's, one painter, not two.
    from api.routes.agents import _serialize_channel_summary

    try:
        result = await apply_move(
            floor_id,
            agent_ids=body.agent_ids,
            channel_ids=body.channel_ids,
            exclude_companion_ids=body.exclude_companion_ids,
            fingerprint=body.fingerprint,
            services=runtime_services,
        )
    except MovePlanChanged as exc:
        return _plan_changed(exc)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AgentOnVacation as exc:
        raise HTTPException(409, str(exc)) from exc
    except MoveRefused as exc:
        raise HTTPException(400, str(exc)) from exc

    await manager.broadcast_world_state()
    # Left threads lost members, so their rosters on every rail changed too.
    for channel_id in [*result.moved_threads, *result.left_threads]:
        channel = db.get_channel(channel_id)
        if channel is None:
            logger.warning("Thread %s vanished before its move broadcast", channel_id)
            continue
        await manager.broadcast_channel_updated(_serialize_channel_summary(
            channel,
            members=db.list_channel_member_details(channel.id),
            latest_message=db.get_latest_channel_message(channel.id),
        ))
    await _broadcast_floors()
    return asdict(result)


@router.get("/floors/{floor_id}/projects")
async def get_floor_projects(floor_id: str) -> list[dict]:
    """The floor's projects (its folder's top-level directories), ``[{name, modified_at}]``."""
    try:
        return list_projects(floor_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/floors/{floor_id}/projects/move")
async def post_project_move(floor_id: str, body: ProjectMoveBody) -> dict:
    """Bring one project folder from another floor onto this one.

    404 when a floor or the project is missing, 409 when this floor already
    has a project by that name, 400 when the floors are the same or the name
    is not a folder name. A failed rename answers 500 with the OS's reason;
    nothing was moved.
    """
    try:
        result = move_project(body.project, body.from_floor_id, floor_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        logger.error("Project move %r -> floor %s failed: %s", body.project, floor_id, exc)
        raise HTTPException(500, f"The project folder could not be moved: {exc}") from exc
    return asdict(result)
