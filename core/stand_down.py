"""BossMod AI — the one clean stop for agents leaving service.

Floor suspend (core/floor_suspend.py) and vacation (core/floors.py
``send_home``) both end an agent's work the same way, so the stop lives here,
in neither. The caller owns the agent's lifecycle flag and closes the wake
gate first (``suspended_at`` on the floor, or ``vacation_since`` on the
agent); :func:`stand_down` then ends everything the agent had going, so
nothing is left waiting on someone who is never woken.

Heavy dependencies (turn claims, tasking, meetings, seating, background
processes) are imported inside the functions: core/floors.py imports this
module for its two dataclasses, and must not become an import hub.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, get_args

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StandDownWhy:
    """The operation that stopped the agents, in the two forms every line is built from.

    Every operator-facing string of a stand-down is derived from one of
    these, so the copy cannot drift between steps. Instances live with their
    callers (``FLOOR_SUSPENDED`` in core/floor_suspend.py, ``FLOOR_DELETED``
    in core/floors.py).

    Attributes:
        label: Short and capitalised: the background-process stop reason,
            e.g. ``"Floor suspended"``. The desk shows
            ``Background processes stopped: <label>``.
        phrase: Completes ``"Cancelled: {phrase}"`` (task event, status note
            and origin line), ``"Closed: {phrase}"`` (approvals, consents,
            activities) and ``"Meeting ended: {phrase}."``, e.g.
            ``"the floor was suspended"``.
    """

    label: str
    phrase: str


@dataclass
class StandDownResult:
    """What one stand-down did.

    Attributes:
        agent_ids: The agents stood down, in the order given (duplicates
            dropped).
        tasks_cancelled: The ids of the open tasks it cancelled, oldest first.
        meetings_ended: How many meetings those agents hosted that it ended.
        approvals_closed: How many pending CLI approvals it rejected.
        consents_closed: How many pending consent cards it denied.
        process_stopped_agent_ids: The agents whose running background
            processes it stopped, so a caller repaints just those desks.
        posted_lines: The transcript lines to paint, in the shape
            ``api/routes/agents.py`` ``_broadcast_archive_side_effects``
            reads: each cancelled task's origin line (as
            ``cancel_tasks_as_operator`` returns it), then one
            ``{"meeting_message": {...}, "agent_ids": [...]}`` per ended
            meeting.
    """

    agent_ids: list[str] = field(default_factory=list)
    tasks_cancelled: list[str] = field(default_factory=list)
    meetings_ended: int = 0
    approvals_closed: int = 0
    consents_closed: int = 0
    process_stopped_agent_ids: list[str] = field(default_factory=list)
    posted_lines: list[dict[str, Any]] = field(default_factory=list)


def _open_task_statuses() -> list[str]:
    """Every task status that is not terminal: the rule ``AgentRepository._open_task_ids`` uses.

    ``delegated`` parents are included: they are still open work.
    """
    from core.models.task import TaskStatus
    from core.tasking.transitions import TERMINAL_TASK_STATUSES

    return [status for status in get_args(TaskStatus) if status not in TERMINAL_TASK_STATUSES]


def open_task_ids_on_floor(floor_id: str) -> list[str]:
    """Return the ids of every non-terminal task on ``floor_id``, oldest first.

    A task's floor is ``core.floors.task_floor_id``: its thread, else its
    assignee, owner or requester. That includes backlog items residents
    filed, because the requester comes last.

    Must be called while the residents still live on the floor: after a
    send-home they have no floor, and their backlog items would resolve to
    none.

    Args:
        floor_id: The floor whose open work is listed.

    Returns:
        The task ids, oldest first.
    """
    import db
    from core.floors import task_floor_id

    return [
        task.id
        for task in db.list_tasks_by_statuses(_open_task_statuses())
        if task_floor_id(task) == floor_id
    ]


def _agents_with_closed_gates(agent_ids: list[str]) -> list[Any]:
    """Read each agent and check its wake gate is already closed.

    Raises:
        LookupError: An id names no agent.
        ValueError: Any agent's gate is still open (``wake_refusal`` is
            None); the message names each one.
    """
    import db
    from core.floors import wake_refusal

    agents: list[Any] = []
    still_open: list[str] = []
    for agent_id in agent_ids:
        agent = db.get_agent(agent_id)
        if agent is None:
            raise LookupError(f"Agent not found: {agent_id}")
        if wake_refusal(agent) is None:
            still_open.append(f"{agent.name} ({agent.id})")
        agents.append(agent)
    if still_open:
        raise ValueError(
            "Close the wake gate before standing agents down; still open for: " + ", ".join(still_open)
        )
    return agents


async def stand_down(
    agent_ids: list[str],
    *,
    why: StandDownWhy,
    extra_task_ids: list[str],
    services: Any,
) -> StandDownResult:
    """End everything these agents had going: the one clean stop.

    Precondition: the caller has already closed every agent's wake gate
    (``core.floors.wake_refusal`` is not None: the floor is suspended, or the
    agent is on vacation). Then no teammate's turn can re-wake an agent that
    was already stopped: the next-member wakes a cancelled channel turn
    re-enqueues, a released meeting's resumes, a released dependent's wake
    are all refused by the closed gates.

    Steps, in order:

    1. Per agent: cancel the live turn and discard its claim
       (``cancel_turn_and_discard_claim``), delete its queued triggers, and
       stop its background processes with ``why.label``.
    2. Cancel, in one ``cancel_tasks_as_operator`` call with
       ``Cancelled: {why.phrase}``, every non-terminal task assigned to any
       of the agents plus every ``extra_task_ids`` task that is still open.
       Invariant: no stood-down agent keeps open assigned work, so the
       watchdog never pings or stalls it. Tasks the agents merely own
       (assigned to someone still active) are left: the assignee can finish
       them, and a later per-agent vacation must not kill colleagues' work.
    3. Per agent: end the meetings it hosts (``Meeting ended: {why.phrase}.``).
    4. Per agent: reject its pending CLI approvals and deny its pending
       consent cards, as ``system`` with ``Closed: {why.phrase}``. A
       system-made deny is never sticky.
    5. Per agent: cancel its open activities (``Closed: {why.phrase}``),
       delete its work snapshots, seat it at its desk unless it is on
       vacation (a vacationer has no body in the office; ``bring_back``
       seats it on return), and refresh its visible status. Everyone is left
       idle, with no walk left "in transit".
    6. Ask the worker to release what extensions run for the agents
       (``services.extensions_release_agents``: desktops, browser sessions).
    7. Log one info line with the counts.

    Thread response rounds (thread-level, owned by the floor callers) and the
    agent's own lifecycle flag (owned by the caller, which closes the gate
    first) are out of scope on purpose.

    Every step is idempotent: a second run finds nothing to stop.

    Args:
        agent_ids: The agents to stand down; duplicates and blanks are dropped.
        why: Why they stood down; every operator-facing line is built from it.
        extra_task_ids: More tasks to cancel with theirs (e.g. a floor's
            backlog). Ids no longer open (finished meanwhile, or gone) are
            already settled and skipped.
        services: The runtime services (``core.runtime.runtime_services``):
            ``reset_agent_runtime``, ``enqueue_trigger`` and
            ``extensions_release_agents``.

    Returns:
        What it did.

    Raises:
        LookupError: An id names no agent; nothing was stopped.
        ValueError: An agent's wake gate is still open; nothing was stopped.
        Exception: Anything a step raises propagates unchanged, e.g.
            ``RuntimeError`` from a timed-out turn cancel,
            ``IllegalTaskTransition``, DB errors. The gates stay closed,
            which is the safe state, and a retry finishes the job.
    """
    import db
    from core.agent_loop import activity_runtime
    from core.agent_loop.meeting_orchestrator import end_meetings_hosted_by
    from core.agent_loop.turn_claims import cancel_turn_and_discard_claim
    from core.bm_cli.background_processes import stop_for_agent
    from core.floors import is_on_vacation
    from core.tasking.service import cancel_tasks_as_operator
    from core.world.seating import place_agent_at_desk

    unique = list(dict.fromkeys(agent_id for agent_id in agent_ids if agent_id))
    agents = _agents_with_closed_gates(unique)
    result = StandDownResult(agent_ids=unique)

    for agent_id in unique:
        await cancel_turn_and_discard_claim(agent_id, services=services)
        db.delete_queued_triggers(agent_id)
        if stop_for_agent(agent_id, reason=why.label):
            result.process_stopped_agent_ids.append(agent_id)

    members = set(unique)
    extras = set(extra_task_ids)
    # One read of all open work; anything not in it is already settled.
    doomed = [
        task.id
        for task in db.list_tasks_by_statuses(_open_task_statuses())
        if task.assigned_to in members or task.id in extras
    ]
    cancelled, task_lines = cancel_tasks_as_operator(doomed, reason=f"Cancelled: {why.phrase}")
    result.tasks_cancelled = [task.id for task in cancelled]
    result.posted_lines.extend(task_lines)

    for agent_id in unique:
        for ended in end_meetings_hosted_by(agent_id, reason=why.phrase):
            result.meetings_ended += 1
            result.posted_lines.append(
                {"meeting_message": {**ended.message}, "agent_ids": list(ended.released_agent_ids)}
            )

    closed_note = f"Closed: {why.phrase}"
    for agent_id in unique:
        result.approvals_closed += db.reject_pending_cli_approvals_for_agent(agent_id, decision_note=closed_note)
        result.consents_closed += db.deny_pending_consent_for_agent(agent_id, decision_note=closed_note)

    for agent in agents:
        db.cancel_open_activities(agent.id, detail=closed_note)
        db.delete_agent_work_snapshots(agent.id)
        if not is_on_vacation(agent):
            place_agent_at_desk(agent.id, agent.desk_x, agent.desk_y)
        activity_runtime.refresh_agent_status(agent.id)

    if unique:
        services.extensions_release_agents(unique)

    logger.info(
        "Stood down %d agent(s) (%s): %d task(s) cancelled, %d meeting(s) ended, "
        "%d approval(s) and %d consent(s) closed, processes stopped for %d",
        len(unique),
        why.label,
        len(result.tasks_cancelled),
        result.meetings_ended,
        result.approvals_closed,
        result.consents_closed,
        len(result.process_stopped_agent_ids),
    )
    return result


__all__ = [
    "StandDownResult",
    "StandDownWhy",
    "open_task_ids_on_floor",
    "stand_down",
]
