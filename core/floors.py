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
# Only the two small dataclasses: core/stand_down.py keeps its own heavy
# dependencies (turn claims, tasking, meetings, seating) inside its functions,
# so this import does not make this module an import hub.
from core.stand_down import StandDownResult, StandDownWhy
from db.floors import LOBBY_ID, LOBBY_NAME

logger = logging.getLogger(__name__)

CROSS_FLOOR_DENY = "Cross-floor access denied"
VACATION_DENY = "Agent is on vacation"
FLOOR_SUSPENDED_DENY = "This floor is suspended"
# Why a floor change stopped an agent's background processes: their
# /projects root is about to point at another floor's folder, or none.
FLOOR_MOVE_STOP_REASON = "Moved floors"
# Why a floor delete's "Send them home" stood its residents down.
FLOOR_DELETED = StandDownWhy(label="Floor deleted", phrase="the floor was deleted")

FloorOccupants = Literal["send_home", "delete"]


class FloorDenied(Exception):
    """A co-mingle was refused because the floors do not match."""

    def __init__(self, message: str = CROSS_FLOOR_DENY) -> None:
        super().__init__(message)


class AgentOnVacation(FloorDenied):
    """The agent is on vacation: off every floor and never woken."""

    def __init__(self, message: str = VACATION_DENY) -> None:
        super().__init__(message)


class FloorSuspended(FloorDenied):
    """The floor is suspended: out of service, its agents never woken.

    A ``FloorDenied``, so every existing ``except FloorDenied`` mapping
    (thread create, task create and assign → 403) covers it.
    """

    def __init__(self, message: str = FLOOR_SUSPENDED_DENY) -> None:
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
    # Agents sent home whose running background processes were stopped,
    # so the API can repaint just those desks.
    process_stopped_agent_ids: list[str] = field(default_factory=list)
    # Open tasks a send-home cancelled (theirs and the floor's); empty for
    # the "delete" branch.
    tasks_cancelled: list[str] = field(default_factory=list)


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


def shared_floor_id(left: str | None, right: str | None) -> str | None:
    """Return the home floor two agents share right now, else None.

    Both agents are read fresh from the DB, so a caller that checks the floor
    and then stamps it on a row uses one read for both and the stamp can never
    name a floor the check did not confirm.

    Args:
        left: One agent id. The human sender id or an empty value has no floor.
        right: The other agent id, with the same rules.

    Returns:
        The floor id both agents live on, or None when either agent is
        missing, has no home floor (on vacation), is the human, or the two
        live on different floors.
    """
    a = home_floor_id(left)
    b = home_floor_id(right)
    return a if a and a == b else None


def peers_share_floor(left: str | None, right: str | None) -> bool:
    """True only when both agents have the same known home floor."""
    return shared_floor_id(left, right) is not None


def require_shared_home(agent_ids: list[str]) -> str:
    """Return the one floor every agent lives on.

    Mixed homes, a missing agent, or a missing floor deny. An empty list
    denies too: a thread with nobody has no floor to inherit.

    Raises:
        FloorDenied: Any of the above.
        FloorSuspended: The one shared floor is suspended.
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
    if is_floor_suspended(floors[0]):
        raise FloorSuspended()
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

    Raises:
        FloorDenied: The assignment crosses floors or a floor is missing.
        FloorSuspended: The bound thread's floor, or the shared home, is
            suspended.
    """
    bound = (channel_id or "").strip() or (parent_channel_id or "").strip()
    if bound:
        floor_id = channel_floor_id(bound)
        if not floor_id:
            raise FloorDenied(CROSS_FLOOR_DENY)
        if is_floor_suspended(floor_id):
            raise FloorSuspended()
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
    """The floor a card belongs to: its thread, else the assignee, else the owner,
    else the agent who reported it.

    The requester comes last so an unassigned, unowned backlog item an agent
    filed sits on the reporter's floor. An operator-reported card (the human
    requester) with no agent on it has no floor.
    """
    channel_id = str(getattr(task, "notification_channel_id", None) or "").strip()
    if channel_id:
        return channel_floor_id(channel_id)
    for column in ("assigned_to", "owner_id", "requester_id"):
        agent_id = str(getattr(task, column, None) or "").strip()
        if agent_id and agent_id != HUMAN_SENDER_ID:
            return home_floor_id(agent_id)
    return None


def assert_task_stays_on_floor(task: Any, assignee_id: str | None) -> None:
    """Deny assigning ``task`` to an agent who is not on the task's floor.

    The server-side guard behind the Tasks edit roster, so a reassign can
    never move work across floors. A task with no floor checks nothing (the
    operator's own floor-less backlog), and neither does an unassign or the
    human sender.

    Raises:
        FloorDenied: The task has a floor and ``assignee_id`` is an agent
            not living on it.
        FloorSuspended: The task's floor is suspended (a stale window
            assigning work there).
    """
    token = (assignee_id or "").strip()
    if not token or token == HUMAN_SENDER_ID:
        return
    floor_id = task_floor_id(task)
    if floor_id and is_floor_suspended(floor_id):
        raise FloorSuspended()
    if floor_id and not on_floor(token, floor_id):
        raise FloorDenied(CROSS_FLOOR_DENY)


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


def is_floor_suspended(floor_id: str | None) -> bool:
    """True when ``floor_id`` names a floor that is suspended.

    A blank or missing floor is not "suspended": that is a different
    failure, owned by the gates that already deny a missing floor.
    """
    token = (floor_id or "").strip()
    if not token:
        return False
    from db.floors import get_floor

    floor = get_floor(token)
    return floor is not None and floor.suspended_at is not None


def wake_refusal(agent: Any) -> str | None:
    """Why ``agent`` (an Agent or None) must not be woken, or None when it may be.

    The one rule every wake gate asks: on vacation (``VACATION_DENY``), or
    living on a suspended floor (``FLOOR_SUSPENDED_DENY``). ``None`` agent
    returns None: not-found is handled by each caller.
    """
    if agent is None:
        return None
    if is_on_vacation(agent):
        return VACATION_DENY
    if is_floor_suspended(getattr(agent, "floor_id", None)):
        return FLOOR_SUSPENDED_DENY
    return None


def agent_wake_refusal(agent_id: str | None) -> str | None:
    """``wake_refusal`` for an agent id, read fresh from the database.

    The human sender id, a blank id or a missing agent return None.
    """
    token = (agent_id or "").strip()
    if not token or token == HUMAN_SENDER_ID:
        return None
    import db

    return wake_refusal(db.get_agent(token))


# Each wake refusal as the operator-ingress error that carries it.
_REFUSAL_ERRORS: dict[str, type[FloorDenied]] = {
    VACATION_DENY: AgentOnVacation,
    FLOOR_SUSPENDED_DENY: FloorSuspended,
}


def assert_agent_reachable(agent_id: str | None) -> None:
    """Deny an operator message to an agent that would never answer it.

    The operator-ingress form of ``agent_wake_refusal``: raise before
    anything is written, so the message never looks delivered.

    Raises:
        AgentOnVacation: The agent is on vacation.
        FloorSuspended: The agent's floor is suspended.
    """
    refusal = agent_wake_refusal(agent_id)
    if refusal is not None:
        raise _REFUSAL_ERRORS[refusal]()


async def send_home(
    agent_ids: list[str],
    *,
    why: StandDownWhy,
    extra_task_ids: list[str],
    services: Any,
) -> StandDownResult:
    """Put agents on vacation: off every floor, out of every thread, their work ended.

    Close the gate, then stand down. In one transaction, every agent not
    already on vacation gets ``floor_id`` NULL and ``vacation_since`` stamped
    together (the invariant every floor gate relies on: no floor denies) and
    loses every channel membership, active or archived. Every gate is closed
    before any stop starts, so no teammate's turn can re-wake an agent that
    was already stopped. Then ``core.stand_down.stand_down`` ends their work:
    live turns, queued triggers, background processes, their open assigned
    tasks (plus ``extra_task_ids``), hosted meetings, pending approvals and
    consents, activities, and what extensions run for them. Idempotent: an
    agent already on vacation keeps its row, and the stand-down finds nothing
    left to stop. A per-agent vacation calls this with one id.

    Args:
        agent_ids: The agents to send home.
        why: Why they stood down (e.g. ``FLOOR_DELETED``); every
            operator-facing line is derived from it.
        extra_task_ids: More open tasks to cancel with theirs, e.g. a deleted
            floor's backlog (``core.stand_down.open_task_ids_on_floor``,
            computed before they left the floor).
        services: The runtime services (``core.runtime.runtime_services``).

    Returns:
        What the stand-down did.

    Raises:
        LookupError: An id names no agent; raised before anything is written.
        Exception: Whatever ``stand_down`` raises, unchanged; the gates stay
            closed, which is the safe state, and a retry finishes the job.
    """
    import db
    from core.stand_down import stand_down
    from db.connection import transaction

    unique = list(dict.fromkeys(agent_id for agent_id in agent_ids if agent_id))
    agents = []
    for agent_id in unique:
        agent = db.get_agent(agent_id)
        if agent is None:
            raise LookupError(f"Agent not found: {agent_id}")
        agents.append(agent)
    now = datetime.now(timezone.utc)
    with transaction():
        for agent in agents:
            if is_on_vacation(agent):
                continue
            if db.update_agent(agent.id, floor_id=None, vacation_since=now) is None:
                raise LookupError(f"Agent not found: {agent.id}")
            _release_all_memberships(agent.id)
    return await stand_down(unique, why=why, extra_task_ids=extra_task_ids, services=services)


def bring_back(agent_id: str, floor_id: str) -> Any:
    """End one agent's vacation onto ``floor_id``.

    The agent keeps its desk when that chair is free on ``floor_id`` and
    otherwise takes the next free one, never bumping a resident (core/world/seating.py
    ``reconcile_floor_desks``); the returned agent carries the reconciled desk.
    Any background process still running is stopped before ``/projects``
    becomes the floor's folder.

    Raises:
        LookupError: The agent or the floor does not exist.
        ValueError: The floor is suspended (the agent would land on a floor
            that is off the list, never woken), or the agent is not on
            vacation.
    """
    import db
    from core.bm_cli.background_processes import stop_for_agent
    from core.world.seating import reconcile_floor_desks
    from db.floors import get_floor

    agent = db.get_agent(agent_id)
    if agent is None:
        raise LookupError("Agent not found")
    floor = get_floor((floor_id or "").strip())
    if floor is None:
        raise LookupError("Floor not found")
    if floor.suspended_at is not None:
        raise ValueError(f"{floor.name} is suspended. Resume it first.")
    if not is_on_vacation(agent):
        raise ValueError("Agent is not on vacation")
    stop_for_agent(agent.id, reason=FLOOR_MOVE_STOP_REASON)
    if db.update_agent(agent.id, floor_id=floor.id, vacation_since=None) is None:
        raise LookupError("Agent not found")
    reconcile_floor_desks(floor.id, newcomers=[agent.id])
    returned = db.get_agent(agent.id)
    if returned is None:
        raise LookupError("Agent not found")
    return returned


async def delete_floor(
    floor_id: str,
    *,
    occupants: FloorOccupants | None,
    services: Any,
    on_before_seal: Any = None,
) -> FloorDeleteResult:
    """Delete one floor: its agents go home or are deleted, its threads archive.

    Order matters. Agents leave first, so no turn can re-queue into a thread
    that is about to seal. For send-home, the floor's open tasks are listed
    while the residents still live here (``open_task_ids_on_floor``), then
    one ``send_home`` closes every resident's gate and stands them all down
    (live turns cancelled and their claims discarded, background processes
    stopped, their open assigned tasks and the floor's tasks cancelled with
    ``Cancelled: the floor was deleted``, hosted meetings ended, approvals
    and consents closed, extension resources released); the agents whose
    processes stopped are listed in ``process_stopped_agent_ids`` and the
    cancelled tasks in ``tasks_cancelled``. The stand-down's lines go to
    ``on_before_seal`` before any thread seals. For delete, each live turn
    is cancelled through ``services.reset_agent_runtime`` (deleting the
    agent deletes its triggers) and the agent is deleted. Then every active
    thread on the floor is archived through the operator path with its open
    origin tasks cancelled (after a send-home nothing is left open, so no
    second cancel line is posted). Then the floor's company folder, when it
    has one, moves to ``<company>/.archived-floors/<id>`` (never deleted) and
    artifact paths under it are rewritten. The floor row goes last.

    ``on_before_seal`` is called with the send-home's posted lines and is
    passed through to ``archive_thread_as_operator``, so an API caller can
    paint every closing line live.

    Raises:
        ValueError: The floor is Lobby, or ``occupants`` is not a known choice.
        LookupError: No floor has this id.
        FloorOccupantsChoiceRequired: Agents live here and ``occupants`` is None.
    """
    import db
    from core.agent_repository import agent_repository
    from core.channel_archive import archive_thread_as_operator
    from core.stand_down import open_task_ids_on_floor
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
    if occupants == "send_home":
        # Before anyone leaves: a resident's backlog item resolves to this
        # floor only while its reporter still lives here.
        floor_tasks = open_task_ids_on_floor(floor.id)
        sent = await send_home(
            [agent.id for agent in residents],
            why=FLOOR_DELETED,
            extra_task_ids=floor_tasks,
            services=services,
        )
        result.agents_sent_home = sent.agent_ids
        result.process_stopped_agent_ids = sent.process_stopped_agent_ids
        result.tasks_cancelled = sent.tasks_cancelled
        if on_before_seal is not None:
            await on_before_seal(sent.posted_lines)
    elif occupants == "delete":
        for agent in residents:
            # Deleting the agent deletes its trigger rows, claim included.
            await services.reset_agent_runtime(agent.id)
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
    "FLOOR_DELETED",
    "FLOOR_SUSPENDED_DENY",
    "LOBBY_ID",
    "LOBBY_NAME",
    "VACATION_DENY",
    "AgentOnVacation",
    "FloorDeleteResult",
    "FloorDenied",
    "FloorOccupants",
    "FloorOccupantsChoiceRequired",
    "FloorSuspended",
    "agent_wake_refusal",
    "assert_agent_reachable",
    "assert_assignment",
    "assert_on_channel",
    "assignment_stays_on_floor",
    "bring_back",
    "channel_floor_id",
    "delete_floor",
    "filter_ids",
    "home_floor_id",
    "is_floor_suspended",
    "is_on_vacation",
    "keep_one_floor",
    "on_floor",
    "peers_share_floor",
    "require_shared_home",
    "same_floor_agents",
    "send_home",
    "shared_floor_id",
    "task_floor_id",
    "wake_refusal",
]
