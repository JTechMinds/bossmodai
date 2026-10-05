"""Desk assignment per floor, and seating the body at the desk.

One desk is one chair per floor: every floor draws its own copy of the map
(core/world/tilemap.py), so two agents may hold the same chair on different
floors but never on the same one. Every write that changes an agent's desk or
floor enforces that here — ``choose_desk`` validates an API pick,
``reconcile_floor_desks`` repairs a floor after a move or a return from
vacation — and seats the body in the same call.

``heal_desk_seats`` is the boot-time repair of bodies stranded by a worker
stop (``WorldSimulation.start``), not a per-read check: directory ``location``
is the live ``(x, y)`` via ``get_room_at``, and a body left in the Hallway when
the worker stopped stays there until something seats it.
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from core.models import Agent
from core.world.tilemap import RoomType, first_free_chair, get_room_at, is_chair
import db

logger = logging.getLogger(__name__)


class DeskNotAChair(ValueError):
    """A requested desk is not the chair of any desk on the office map."""

    def __init__(self, xy: tuple[int, int]) -> None:
        self.xy = xy
        super().__init__(f"({xy[0]}, {xy[1]}) is not a desk on the office map")


class DeskTaken(ValueError):
    """A requested desk already belongs to another agent on the same floor."""

    def __init__(self, xy: tuple[int, int], occupant_name: str) -> None:
        self.xy = xy
        self.occupant_name = occupant_name
        super().__init__(
            f"The desk at ({xy[0]}, {xy[1]}) is already taken by {occupant_name} on this floor"
        )


def _desk_of(agent: Agent) -> tuple[int, int] | None:
    """The agent's chair, or None when either coordinate is unset."""
    if agent.desk_x is None or agent.desk_y is None:
        return None
    return (agent.desk_x, agent.desk_y)


def floor_desk_occupancy(
    floor_id: str,
    *,
    exclude_agent_id: str | None = None,
) -> dict[tuple[int, int], Agent]:
    """Map each claimed chair on one floor to the agent holding it.

    Only agents whose ``floor_id`` is ``floor_id`` count; agents on vacation
    (``floor_id`` NULL) never hold a chair. When two agents share a chair the
    earlier hire (``db.list_agents`` order) is the one reported.

    Args:
        floor_id: The floor to read.
        exclude_agent_id: An agent left out, so an edit does not collide
            with its own desk.

    Returns:
        Chair tile -> occupant.
    """
    occupancy: dict[tuple[int, int], Agent] = {}
    for agent in db.list_agents():
        if agent.floor_id != floor_id or agent.id == exclude_agent_id:
            continue
        desk = _desk_of(agent)
        if desk is not None:
            occupancy.setdefault(desk, agent)
    return occupancy


def choose_desk(
    floor_id: str,
    requested: tuple[int, int] | None,
    *,
    exclude_agent_id: str | None = None,
) -> tuple[int, int] | None:
    """Pick the desk an API create or patch writes.

    An explicit pick is validated, never silently swapped: the form already
    marks taken desks, so a taken pick is a conflict the operator must see.

    Args:
        floor_id: The floor the agent works on.
        requested: The chair asked for, or None to take the first free one.
        exclude_agent_id: The agent being edited, whose own desk is not a
            conflict.

    Returns:
        ``requested`` when it is valid; otherwise the first free chair on the
        floor, or None when the floor is full.

    Raises:
        DeskNotAChair: ``requested`` is not a chair on the map.
        DeskTaken: ``requested`` belongs to another agent on this floor.
    """
    occupancy = floor_desk_occupancy(floor_id, exclude_agent_id=exclude_agent_id)
    if requested is None:
        return first_free_chair(occupancy.keys())
    if not is_chair(requested):
        raise DeskNotAChair(requested)
    occupant = occupancy.get(requested)
    if occupant is not None:
        raise DeskTaken(requested, occupant.name)
    return requested


def reconcile_floor_desks(floor_id: str, *, newcomers: Collection[str] = ()) -> list[str]:
    """Give every agent on a floor its own chair; residents keep theirs.

    The "two agents on one desk -> next free desk" rule. Residents (agents on
    the floor not in ``newcomers``) are walked first, in creation order, then
    ``newcomers`` in creation order, so an agent arriving by a move or a return
    from vacation yields a contested chair to whoever already sits there,
    however early it was hired. An agent keeps its desk when that desk is a
    chair not yet claimed in this pass; otherwise (or when it has no desk) it
    gets the first free chair, or None when the floor is full. Changed desks
    are persisted and the body is seated in the same call.

    Args:
        floor_id: The floor to repair.
        newcomers: Ids of agents that just arrived on the floor.

    Returns:
        The ids of the agents whose desk changed.
    """
    arriving = set(newcomers)
    on_floor = [agent for agent in db.list_agents() if agent.floor_id == floor_id]
    ordered = [a for a in on_floor if a.id not in arriving] + [a for a in on_floor if a.id in arriving]
    claimed: set[tuple[int, int]] = set()
    changed: list[str] = []
    for agent in ordered:
        current = _desk_of(agent)
        if current is not None and is_chair(current) and current not in claimed:
            desk = current
        else:
            desk = first_free_chair(claimed)
        if desk is not None:
            claimed.add(desk)
        else:
            logger.warning(
                "Floor %s has no free desk: %s (%s) is left without one",
                floor_id, agent.name, agent.id,
            )
        new_x, new_y = desk if desk is not None else (None, None)
        if (new_x, new_y) == (agent.desk_x, agent.desk_y):
            continue
        db.update_agent(agent.id, desk_x=new_x, desk_y=new_y)
        logger.info(
            "Desk reassigned on floor %s: %s (%s) %s -> %s",
            floor_id, agent.name, agent.id, (agent.desk_x, agent.desk_y), desk,
        )
        if desk is not None:
            place_agent_at_desk(agent.id, new_x, new_y)
        changed.append(agent.id)
    return changed


def reconcile_all_desks() -> int:
    """Run ``reconcile_floor_desks`` on every floor that has agents.

    Agents on vacation (``floor_id`` NULL) are skipped; their desk is kept and
    reconciled when they come back (core/floors.py ``bring_back``).

    Returns:
        How many agents' desks changed, over all floors.
    """
    floors: list[str] = []
    for agent in db.list_agents():
        if agent.floor_id is not None and agent.floor_id not in floors:
            floors.append(agent.floor_id)
    return sum(len(reconcile_floor_desks(floor_id)) for floor_id in floors)


def occupied_tiles_on_floor(floor_id: str, *, exclude_agent_id: str) -> set[tuple[int, int]]:
    """Tiles other agents on one floor stand on or are walking to.

    Bodies are the live ``(x, y)`` from ``db.get_world_state`` (which already
    leaves vacationers out); walks in progress add their destination, so two
    agents setting off for the same room at once do not pick the same tile.

    Args:
        floor_id: The floor whose bodies count. Each floor draws its own
            office, so agents elsewhere never occupy a tile here.
        exclude_agent_id: The agent asking, whose own tile is not a conflict.

    Returns:
        The occupied ``(x, y)`` tiles.
    """
    from core.agent_loop import activity_runtime

    others = {
        row["id"]: row
        for row in db.get_world_state()
        if row["id"] != exclude_agent_id and row.get("floor_id") == floor_id
    }
    occupied = {
        (int(row["x"]), int(row["y"]))
        for row in others.values()
        if row.get("x") is not None and row.get("y") is not None
    }
    for movement in activity_runtime.list_active_movements():
        if movement.agent_id not in others:
            continue
        metadata = movement.metadata or {}
        dest_x, dest_y = metadata.get("destination_x"), metadata.get("destination_y")
        if dest_x is not None and dest_y is not None:
            occupied.add((int(dest_x), int(dest_y)))
    return occupied


def live_position_needs_desk_heal(
    desk_x: int | None,
    desk_y: int | None,
    x: int,
    y: int,
    *,
    moving: bool,
) -> bool:
    """Return True when a desk is assigned but the body is still in the hallway."""
    if desk_x is None or desk_y is None:
        return False
    if (x, y) == (desk_x, desk_y):
        return False
    if moving:
        return False
    room = get_room_at(x, y)
    if room is None:
        return True
    return room.get("room_type") == RoomType.HALLWAY


def place_agent_at_desk(agent_id: str, desk_x: int | None, desk_y: int | None) -> None:
    """Seat the live body at an assigned chair so office presence matches the desk."""
    if desk_x is None or desk_y is None:
        return
    from core.agent_loop import activity_runtime
    from core.world.simulation import simulation

    simulation.clear_agent_path(agent_id)
    state = db.get_agent_state(agent_id)
    if state is None or (state.x, state.y) != (desk_x, desk_y):
        db.update_agent_state(agent_id, x=desk_x, y=desk_y)
    active = activity_runtime.get_active_activity(agent_id)
    if active and active.kind == "movement":
        activity_runtime.resolve_arrival(agent_id)


def heal_desk_seats() -> int:
    """Seat hallway-stranded agents that already have a desk.

    Boot-time repair, called once from ``WorldSimulation.start``: a body left
    standing when the worker stopped is put back at its chair. Not a per-read
    check — every desk write seats the body in the same call.

    Returns:
        How many agents were moved.
    """
    from core.agent_loop import activity_runtime

    healed = 0
    for agent in db.list_agents():
        if agent.desk_x is None or agent.desk_y is None:
            continue
        # On vacation: off every floor and out of the office, so there is no
        # body to seat. The desk is kept for when they come back.
        if agent.vacation_since is not None:
            continue
        state = db.get_agent_state(agent.id)
        if state is None:
            continue
        active = activity_runtime.get_active_activity(agent.id)
        moving = state.status == "in_transit" or (
            active is not None and active.kind == "movement"
        )
        if not live_position_needs_desk_heal(
            agent.desk_x,
            agent.desk_y,
            state.x,
            state.y,
            moving=moving,
        ):
            continue
        place_agent_at_desk(agent.id, agent.desk_x, agent.desk_y)
        healed += 1
    return healed
