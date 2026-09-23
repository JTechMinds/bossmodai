"""Floor list and create. Moving an agent lives on the agent routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.models.floor import Floor
from db.floors import create_floor, list_floors

router = APIRouter()


class FloorCreateBody(BaseModel):
    name: str


@router.get("/floors")
async def get_floors() -> list[Floor]:
    """Return every co-mingle domain. Lobby is always present."""
    return list_floors()


@router.post("/floors", status_code=201)
async def post_floor(body: FloorCreateBody) -> Floor:
    """Create a labeled floor. An existing name returns that floor."""
    try:
        return create_floor(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
