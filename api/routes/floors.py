"""Floor list, create, rename, and delete. Moving an agent lives on the agent routes."""

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.websocket import manager
from core.floors import FloorOccupantsChoiceRequired, delete_floor
from core.models.floor import Floor
from core.runtime import runtime_services
from core.tasking.transitions import IllegalTaskTransition
from db.floors import FloorNameTaken, create_floor, list_floors, rename_floor
import db

logger = logging.getLogger(__name__)

router = APIRouter()


async def _broadcast_floors() -> None:
    """Tell every open window the floor list changed, sending the whole list."""
    await manager.broadcast_floors_updated(
        [floor.model_dump(mode="json") for floor in list_floors()]
    )


class FloorCreateBody(BaseModel):
    name: str


class FloorRenameBody(BaseModel):
    name: str


@router.get("/floors")
async def get_floors() -> list[Floor]:
    """Return every co-mingle domain. Lobby is always present."""
    return list_floors()


@router.post("/floors", status_code=201)
async def post_floor(body: FloorCreateBody) -> Floor:
    """Create a labeled floor. An existing name returns that floor."""
    try:
        floor = create_floor(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    unknown ``occupants`` value answer 400, a missing floor 404.
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
