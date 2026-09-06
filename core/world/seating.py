"""Seat desk-assigned agents when the live body has drifted.

Directory ``location`` is the live ``(x, y)`` via ``get_room_at``. Default
spawn sits in Hallway. Create/patch can assign a desk without re-seating
older agents, so desk and body diverge until something heals them.
"""

from __future__ import annotations

from core.world.tilemap import RoomType, get_room_at
import db


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
    """Seat hallway-stranded agents that already have a desk. Return how many moved."""
    from core.agent_loop import activity_runtime

    healed = 0
    for agent in db.list_agents():
        if agent.desk_x is None or agent.desk_y is None:
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
