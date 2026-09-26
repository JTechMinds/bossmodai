"""Hard co-mingle isolation.

A floor is the only isolation axis. API versus self-hosted stays inside
a floor. Cross-floor wake, assign, seat, and list paths deny. A missing
floor denies too: nothing is treated as Lobby at the moment of a wake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.models.message import HUMAN_SENDER_ID
from db.floors import LOBBY_ID, LOBBY_NAME

logger = logging.getLogger(__name__)

CROSS_FLOOR_DENY = "Cross-floor access denied"
VACATION_DENY = "Agent is on vacation"

FloorOccupants = Literal["send_home", "delete"]


class FloorDenied(Exception):
    """A co-mingle was refused because the floors do not match."""

    def __init__(self, message: str = CROSS_FLOOR_DENY) -> None:
        super().__init__(message)


class AgentOnVacation(FloorDenied):
    """The agent is on vacation: off every floor and never woken."""

    def __init__(self, message: str = VACATION_DENY) -> None:
        super().__init__(message)


class FloorOccupantsChoiceRequired(Exception):
    """A floor with agents on it cannot be deleted until the operator says
    whether they go home (vacation) or are deleted with it."""

    def __init__(self, agent_count: int) -> None:
        self.agent_count = agent_count
        noun = "agent works" if agent_count == 1 else "agents work"
        super().__init__(
            f"{agent_count} {noun} on this floor. Choose whether to send them home or delete them."
        )


@dataclass
class FloorDeleteResult:
    """What a floor delete did, in the order it did it."""

    floor_id: str
    agents_sent_home: list[str] = field(default_factory=list)
    agents_deleted: list[str] = field(default_factory=list)
    threads_archived: list[str] = field(default_factory=list)
    # True when the floor's company folder moved to Company › Archived floors.
    folder_archived: bool = False


def home_floor_id(agent_id: str | None) -> str | None:
    """Return an agent's home floor, or None when it cannot be read."""
    token = (agent_id or "").strip()
    if not token or token == HUMAN_SENDER_ID:
        return None
    import db

    agent = db.get_agent(token)
    floor_id = str(getattr(agent, "floor_id", None) or "").strip() if agent is not None else ""
    return floor_id or None


def channel_floor_id(channel_id: str | None) -> str | None:
    """Return a thread's floor, or None when the thread has none."""
    token = (channel_id or "").strip()
    if not token:
        return None
    import db

    channel = db.get_channel(token)
    floor_id = str(getattr(channel, "floor_id", None) or "").strip() if channel is not None else ""
    return floor_id or None


def on_floor(agent_id: str | None, floor_id: str | None) -> bool:
    """True only when the agent has that exact home floor."""
    home = home_floor_id(agent_id)
    wanted = (floor_id or "").strip()
    return bool(home) and bool(wanted) and home == wanted


def peers_share_floor(left: str | None, right: str | None) -> bool:
    """True only when both agents have the same known home floor."""
    a = home_floor_id(left)
    b = home_floor_id(right)
    return bool(a) and a == b


def require_shared_home(agent_ids: list[str]) -> str:
    """Return the one floor every agent lives on.

    Mixed homes, a missing agent, or a missing floor deny. An empty list
    denies too: a thread with nobody has no floor to inherit.
    """
    unique = list(dict.fromkeys(agent_id for agent_id in agent_ids if agent_id))
    if not unique:
        raise FloorDenied("At least one agent on one floor is required")
    floors: list[str] = []
    for agent_id in unique:
        floor_id = home_floor_id(agent_id)
        if not floor_id:
            raise FloorDenied(CROSS_FLOOR_DENY)
        floors.append(floor_id)
    if len(set(floors)) != 1:
        raise FloorDenied(CROSS_FLOOR_DENY)
    return floors[0]


def filter_ids(agent_ids: list[str], *, floor_id: str | None) -> list[str]:
    """Keep ids that live on ``floor_id``. No floor keeps nobody."""
    wanted = (floor_id or "").strip()
    if not wanted:
        return []
    return [agent_id for agent_id in agent_ids if on_floor(agent_id, wanted)]


def keep_one_floor(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a System AI roster down to one floor.

    A single floor is unchanged. A tie between floors keeps nobody, so a
    mixed roster is not offered as a compromise.
    """
    floors: dict[str, str | None] = {}
    counts: dict[str, int] = {}
    for member in members:
        agent_id = str(member.get("id") or "").strip()
        floor_id = home_floor_id(agent_id) if agent_id else None
        floors[agent_id] = floor_id
        if floor_id:
            counts[floor_id] = counts.get(floor_id, 0) + 1
    if len(counts) == 1:
        anchor = next(iter(counts))
        return [member for member in members if floors.get(str(member.get("id") or "").strip()) == anchor]
    if not counts:
        return []
    top = max(counts.values())
    winners = [floor_id for floor_id, count in counts.items() if count == top]
    if len(winners) != 1:
        return []
    anchor = winners[0]
    return [member for member in members if floors.get(str(member.get("id") or "").strip()) == anchor]


def assert_on_channel(agent_id: str | None, channel_id: str | None) -> None:
    """Deny unless the agent lives on the thread's floor."""
    floor_id = channel_floor_id(channel_id)
    if not floor_id or not on_floor(agent_id, floor_id):
        raise FloorDenied(CROSS_FLOOR_DENY)


def assert_assignment(
    *,
    assignee_id: str | None,
    owner_id: str | None,
    channel_id: str | None = None,
    parent_channel_id: str | None = None,
) -> None:
    """Deny a board assign that would put work across floors.

    A channel-bound card stays on that channel's floor. Otherwise the
    assignee and the owner, when both are agents, must share a home.
    """
    bound = (channel_id or "").strip() or (parent_channel_id or "").strip()
    if bound:
        floor_id = channel_floor_id(bound)
        if not floor_id:
            raise FloorDenied(CROSS_FLOOR_DENY)
        for agent_id in (assignee_id, owner_id):
            token = (agent_id or "").strip()
            if not token or token == HUMAN_SENDER_ID:
                continue
            if not on_floor(token, floor_id):
                raise FloorDenied(CROSS_FLOOR_DENY)
        return
    agents = [
        token
        for token in ((assignee_id or "").strip(), (owner_id or "").strip())
        if token and token != HUMAN_SENDER_ID
    ]
    if not agents:
        return
    require_shared_home(agents)


def task_floor_id(task: Any) -> str | None:
    """The floor a card belongs to: its thread, else the assignee, else the owner."""
    channel_id = str(getattr(task, "notification_channel_id", None) or "").strip()
    if channel_id:
        return channel_floor_id(channel_id)
    assignee = str(getattr(task, "assigned_to", None) or "").strip()
    if assignee and assignee != HUMAN_SENDER_ID:
        return home_floor_id(assignee)
    owner = str(getattr(task, "owner_id", None) or "").strip()
    if owner and owner != HUMAN_SENDER_ID:
        return home_floor_id(owner)
    return None


def assignment_stays_on_floor(task: Any) -> bool:
    """False when waking this card would cross a floor. Missing floor denies."""
    assignee = str(getattr(task, "assigned_to", None) or "").strip()
    if not assignee or assignee == HUMAN_SENDER_ID:
        return False
    floor_id = task_floor_id(task)
    if not floor_id:
        return False
    return on_floor(assignee, floor_id)


def is_on_vacation(agent: Any) -> bool:
    """True when ``agent`` (an Agent or None) is on vacation. None is not."""
    return agent is not None and getattr(agent, "vacation_since", None) is not None


def agent_id_on_vacation(agent_id: str | None) -> bool:
    """True when the agent with this id exists and is on vacation.

    A missing agent is not "on vacation": that is a different failure, and
    the paths that read this already have their own not-found handling.
    """
    token = (agent_id or "").strip()
    if not token or token == HUMAN_SENDER_ID:
        return False
    import db

    return is_on_vacation(db.get_agent(token))


def send_home(agent_id: str) -> Any:
    """Put one agent on vacation: off every floor, out of every thread, never woken.

    Sets ``floor_id`` to NULL and stamps ``vacation_since`` together, which is
    the invariant every floor gate relies on (no floor denies). Every channel
    membership, active or archived, is released and queued triggers are
    dropped. Tasks stay where they are, the same policy a floor move
    (core/floor_moves.py) follows. Idempotent: an agent already on vacation is returned unchanged.

    Raises:
        LookupError: No agent has this id.
    """
    import db
    from db.connection import transaction

    agent = db.get_agent(agent_id)
    if agent is None:
        raise LookupError("Agent not found")
    if is_on_vacation(agent):
        return agent
    with transaction():
        updated = db.update_agent(
            agent.id,
            floor_id=None,
            vacation_since=datetime.now(timezone.utc),
        )
        if updated is None:
            raise LookupError("Agent not found")
        _release_all_memberships(updated.id)
        db.delete_queued_triggers(updated.id)
    return updated


def bring_back(agent_id: str, floor_id: str) -> Any:
    """End one agent's vacation onto ``floor_id``.

    Raises:
        LookupError: The agent or the floor does not exist.
        ValueError: The agent is not on vacation.
    """
    import db
    from db.floors import get_floor

    agent = db.get_agent(agent_id)
    if agent is None:
        raise LookupError("Agent not found")
    floor = get_floor((floor_id or "").strip())
    if floor is None:
        raise LookupError("Floor not found")
    if not is_on_vacation(agent):
        raise ValueError("Agent is not on vacation")
    updated = db.update_agent(agent.id, floor_id=floor.id, vacation_since=None)
    if updated is None:
        raise LookupError("Agent not found")
    return updated


async def delete_floor(
    floor_id: str,
    *,
    occupants: FloorOccupants | None,
    services: Any,
    on_before_seal: Any = None,
) -> FloorDeleteResult:
    """Delete one floor: its agents go home or are deleted, its threads archive.

    Order matters. Agents leave first (a live turn is cancelled through
    ``services.reset_agent_runtime`` before the agent row changes), so no
    turn can re-queue into a thread that is about to seal. Then every active
    thread on the floor is archived through the operator path with its open
    origin tasks cancelled. Then the floor's company folder, when it has
    one, moves to ``<company>/.archived-floors/<id>`` (never deleted) and
    artifact paths under it are rewritten. The floor row goes last.

    ``on_before_seal`` is passed through to ``archive_thread_as_operator`` so
    an API caller can paint each thread's closing lines live.

    Raises:
        ValueError: The floor is Lobby, or ``occupants`` is not a known choice.
        LookupError: No floor has this id.
        FloorOccupantsChoiceRequired: Agents live here and ``occupants`` is None.
    """
    import db
    from core.agent_repository import agent_repository
    from core.channel_archive import archive_thread_as_operator
    from db.floors import delete_floor_row, get_floor

    token = (floor_id or "").strip()
    if token == LOBBY_ID:
        raise ValueError("Lobby cannot be deleted")
    floor = get_floor(token)
    if floor is None:
        raise LookupError("Floor not found")
    if occupants is not None and occupants not in ("send_home", "delete"):
        raise ValueError(f"Unknown occupants choice: {occupants}")

    residents = [
        agent for agent in db.list_agents()
        if str(getattr(agent, "floor_id", None) or "") == floor.id
    ]
    if residents and occupants is None:
        raise FloorOccupantsChoiceRequired(len(residents))

    result = FloorDeleteResult(floor_id=floor.id)
    for agent in residents:
        await services.reset_agent_runtime(agent.id)
        if occupants == "send_home":
            send_home(agent.id)
            result.agents_sent_home.append(agent.id)
        else:
            try:
                agent_repository.delete(agent.id)
            except LookupError as exc:
                raise LookupError(f"Agent {agent.id} disappeared during the floor delete") from exc
            result.agents_deleted.append(agent.id)

    for channel in db.list_channels(status="active"):
        if str(getattr(channel, "floor_id", None) or "") != floor.id:
            continue
        await archive_thread_as_operator(
            channel.id,
            cancel_open_tasks=True,
            services=services,
            on_before_seal=on_before_seal,
        )
        result.threads_archived.append(channel.id)

    result.folder_archived = _archive_floor_folder(floor.id, floor.name)

    delete_floor_row(floor.id)
    logger.info(
        "Deleted floor %s: %d sent home, %d deleted, %d threads archived, folder archived: %s",
        floor.id,
        len(result.agents_sent_home),
        len(result.agents_deleted),
        len(result.threads_archived),
        result.folder_archived,
    )
    return result


def _archive_floor_folder(floor_id: str, floor_name: str) -> bool:
    """Move the floor's folder into the archive and repoint its artifacts.

    Returns False when the floor had no folder on disk. The move happens
    before the path rewrite; if the rewrite fails the error propagates with
    the folder already archived, which is the state on disk to report.
    """
    import db
    from core.bm_cli.floor_roots import archive_floor_folder

    moved = archive_floor_folder(floor_id, floor_name, archived_at=datetime.now(timezone.utc))
    if moved is None:
        return False
    old_path, new_path = moved
    rewritten = db.rewrite_artifact_path_prefix(str(old_path), str(new_path))
    logger.info("Floor %s: %d artifact path(s) now under %s", floor_id, rewritten, new_path)
    return True


def _release_all_memberships(agent_id: str) -> None:
    """Drop this agent from every thread, active or archived."""
    import db

    db.execute("DELETE FROM channel_members WHERE agent_id = $1", [agent_id])


def same_floor_agents(agent_id: str) -> list[Any]:
    """Agents who share ``agent_id``'s home. The agent themself is included."""
    import db

    floor_id = home_floor_id(agent_id)
    if not floor_id:
        return []
    return [agent for agent in db.list_agents() if str(getattr(agent, "floor_id", None) or "") == floor_id]


__all__ = [
    "CROSS_FLOOR_DENY",
    "LOBBY_ID",
    "LOBBY_NAME",
    "VACATION_DENY",
    "AgentOnVacation",
    "FloorDeleteResult",
    "FloorDenied",
    "FloorOccupants",
    "FloorOccupantsChoiceRequired",
    "agent_id_on_vacation",
    "assert_assignment",
    "assert_on_channel",
    "assignment_stays_on_floor",
    "bring_back",
    "channel_floor_id",
    "delete_floor",
    "filter_ids",
    "home_floor_id",
    "is_on_vacation",
    "keep_one_floor",
    "on_floor",
    "peers_share_floor",
    "require_shared_home",
    "same_floor_agents",
    "send_home",
    "task_floor_id",
]
