"""Hard co-mingle isolation.

A floor is the only isolation axis. API versus self-hosted stays inside
a floor. Cross-floor wake, assign, seat, and list paths deny. A missing
floor denies too: nothing is treated as Lobby at the moment of a wake.
"""

from __future__ import annotations

from typing import Any

from core.models.message import HUMAN_SENDER_ID
from db.floors import LOBBY_ID, LOBBY_NAME

CROSS_FLOOR_DENY = "Cross-floor access denied"


class FloorDenied(Exception):
    """A co-mingle was refused because the floors do not match."""

    def __init__(self, message: str = CROSS_FLOOR_DENY) -> None:
        super().__init__(message)


class FloorMoveNeedsConfirm(FloorDenied):
    """The home floor can move, but open work on the old floor must be confirmed."""

    def __init__(self, open_task_count: int) -> None:
        self.open_task_count = open_task_count
        noun = "task" if open_task_count == 1 else "tasks"
        super().__init__(
            f"This agent has {open_task_count} open {noun} on the current floor. "
            "Confirm to move the home floor."
        )


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


def open_work_ids(agent_id: str) -> list[str]:
    """Open cards assigned to or owned by this agent. Order is not significant."""
    import db
    from core.tasking.resolution import OPEN_TASK_STATUSES

    found: list[str] = []
    seen: set[str] = set()
    for status in OPEN_TASK_STATUSES:
        rows = list(db.list_tasks(assigned_to=agent_id, status=status))
        rows.extend(db.list_tasks(owner_id=agent_id, status=status))
        for task in rows:
            if task.id in seen:
                continue
            seen.add(task.id)
            found.append(task.id)
    return found


def move_home_floor(agent_id: str, floor_id: str, *, confirm_open_work: bool) -> Any:
    """Move one agent's home floor. Open work on the old floor must be confirmed.

    Membership on any other floor's threads is released so those rosters
    stay single-floor. Tasks, soft-block, and sticky rows are not wiped.
    """
    import db

    agent = db.get_agent(agent_id)
    if agent is None:
        raise LookupError("Agent not found")
    from db.floors import get_floor

    target = (floor_id or "").strip()
    floor = get_floor(target)
    if floor is None:
        raise LookupError("Floor not found")
    current = str(getattr(agent, "floor_id", None) or "").strip()
    if current == floor.id:
        return agent
    open_ids = open_work_ids(agent.id)
    if open_ids and not confirm_open_work:
        raise FloorMoveNeedsConfirm(len(open_ids))
    updated = db.update_agent(agent.id, floor_id=floor.id)
    if updated is None:
        raise LookupError("Agent not found")
    _release_other_floor_memberships(updated.id, floor.id)
    return updated


def _release_other_floor_memberships(agent_id: str, floor_id: str) -> None:
    """Drop this agent from threads that are not their new home floor."""
    import db

    for status in ("active", "archived"):
        for channel in db.list_channels(status=status):
            if str(getattr(channel, "floor_id", None) or "") == floor_id:
                continue
            db.execute(
                "DELETE FROM channel_members WHERE channel_id = $1 AND agent_id = $2",
                [channel.id, agent_id],
            )


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
    "FloorDenied",
    "FloorMoveNeedsConfirm",
    "assert_assignment",
    "assert_on_channel",
    "assignment_stays_on_floor",
    "channel_floor_id",
    "filter_ids",
    "home_floor_id",
    "keep_one_floor",
    "move_home_floor",
    "on_floor",
    "open_work_ids",
    "peers_share_floor",
    "require_shared_home",
    "same_floor_agents",
    "task_floor_id",
]
