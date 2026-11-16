"""BossMod AI — suspend a floor (a clean stop) and resume it (a fresh start).

A suspended floor is out of service: ``floors.suspended_at`` is set, so
every wake gate refuses its residents (``core.floors.wake_refusal``), the
loops that work before any wake skip it, and it leaves the roster and the
floor switcher. Suspend is a clean stop, not a freeze: the residents stand
down (``core.stand_down.stand_down``: work in progress ends, open tasks are
cancelled, desktops and browsers shut down). Resume only clears the flag:
nothing that was running picks up again. Homes, desks, threads,
memberships, files and schedules are kept throughout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.models.floor import Floor
from core.stand_down import StandDownResult, StandDownWhy, open_task_ids_on_floor, stand_down
from db.floors import LOBBY_ID

logger = logging.getLogger(__name__)

FLOOR_SUSPENDED = StandDownWhy(label="Floor suspended", phrase="the floor was suspended")


class FloorSuspendConflict(Exception):
    """The floor is already suspended (on suspend), or is not suspended (on resume)."""


@dataclass
class FloorSuspendResult:
    """What one suspend did: the floor, and its residents' stand-down."""

    floor_id: str
    stand_down: StandDownResult


async def suspend_floor(floor_id: str, *, services: Any) -> FloorSuspendResult:
    """Take one floor out of service: close every resident's gate, then stand them down.

    The order is the contract:

    1. Validate (Lobby, missing, already suspended).
    2. List the floor's open tasks (``open_task_ids_on_floor``) while the
       residents still resolve to it.
    3. Set ``suspended_at``: every resident's gate closes at once.
    4. The residents are the agents whose home is this floor; a vacationer
       has no floor and is never one.
    5. ``stand_down`` the residents, with the floor's open tasks as extras.
    6. Close every active response round of the floor's threads. The
       precondition of ``db.close_active_channel_rounds`` holds: step 5
       removed every queued and claimed wake of every member, and members
       are residents by floor isolation.
    7. Ask the worker to reload its schedule timetable, which drops the
       floor's schedules (``db.list_schedules_to_run``).

    Args:
        floor_id: The floor to suspend.
        services: The runtime services (``core.runtime.runtime_services``).

    Returns:
        The floor id and what the stand-down did.

    Raises:
        ValueError: The floor is Lobby, the operator's fallback floor, which
            must always exist active.
        LookupError: No floor has this id.
        FloorSuspendConflict: The floor is already suspended.
        Exception: Whatever ``stand_down`` raises, unchanged. The floor stays
            suspended (the safe state); the operator recovers with Resume
            then Suspend.
    """
    import db
    from core.scheduling.service import request_reload
    from db.floors import get_floor, set_floor_suspended

    token = (floor_id or "").strip()
    if token == LOBBY_ID:
        raise ValueError("Lobby cannot be suspended")
    floor = get_floor(token)
    if floor is None:
        raise LookupError("Floor not found")
    if floor.suspended_at is not None:
        raise FloorSuspendConflict(f"{floor.name} is already suspended")

    floor_tasks = open_task_ids_on_floor(floor.id)
    set_floor_suspended(floor.id, datetime.now(timezone.utc))
    residents = [agent.id for agent in db.list_agents() if agent.floor_id == floor.id]
    stood_down = await stand_down(
        residents,
        why=FLOOR_SUSPENDED,
        extra_task_ids=floor_tasks,
        services=services,
    )
    rounds_closed = 0
    for channel in db.list_channels(status="active"):
        if channel.floor_id == floor.id:
            rounds_closed += db.close_active_channel_rounds(channel.id)
    request_reload()
    logger.info(
        "Suspended floor %s (%s): %d resident(s) stood down, %d thread round(s) closed",
        floor.id,
        floor.name,
        len(residents),
        rounds_closed,
    )
    return FloorSuspendResult(floor_id=floor.id, stand_down=stood_down)


def resume_floor(floor_id: str) -> Floor:
    """Put a suspended floor back in service: a fresh start.

    Clears ``suspended_at`` and asks the worker to reload its schedule
    timetable, so the floor's schedules run again from now on. Nothing is
    woken: what was running ended at suspend, and starting again is the
    operator's decision.

    Args:
        floor_id: The floor to resume.

    Returns:
        The floor as stored after the resume.

    Raises:
        LookupError: No floor has this id.
        FloorSuspendConflict: The floor is not suspended.
    """
    from core.scheduling.service import request_reload
    from db.floors import get_floor, set_floor_suspended

    floor = get_floor((floor_id or "").strip())
    if floor is None:
        raise LookupError("Floor not found")
    if floor.suspended_at is None:
        raise FloorSuspendConflict(f"{floor.name} is not suspended")
    resumed = set_floor_suspended(floor.id, None)
    request_reload()
    logger.info("Resumed floor %s (%s)", resumed.id, resumed.name)
    return resumed


__all__ = [
    "FLOOR_SUSPENDED",
    "FloorSuspendConflict",
    "FloorSuspendResult",
    "resume_floor",
    "suspend_floor",
]
