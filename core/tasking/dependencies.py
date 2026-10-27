"""BossMod AI — Task prerequisites: resolve what a PM wrote, release or fail what waits.

A task created with an unfinished prerequisite is born ``held``
(``db.create_task``). When any task reaches a terminal status,
``transition_task`` calls :func:`settle_dependents` for it: a held task whose
prerequisites are now all ``complete`` is released to ``pending`` and its
assignee is woken; one whose prerequisite closed without completing moves to
``blocked`` and the agent that set the dependency is told.

The invariant every assignee-facing check relies on: a non-terminal task with
an edge to a prerequisite that is not ``complete`` has not been released. It
is ``held``, or ``blocked`` because a prerequisite failed, and its assignee
never sees it (:func:`is_released`). The operator's
overrides (release, resume, edit) delete those edges before they start the
task, so the invariant holds.

The PM never types an id: :func:`resolve_prerequisites` matches plan-local
keys and the titles of the PM's own tasks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import db
from core.models import Agent, Task
from core.tasking.resolution import OPEN_TASK_STATUSES, normalize_workstream_title
from core.tasking.transitions import (
    TERMINAL_TASK_STATUSES,
    TaskStatusChanged,
    is_terminal_task_status,
    transition_task,
)

logger = logging.getLogger(__name__)

# What meets a prerequisite, and what never can. db/task_dependencies.py
# spells ``complete`` in its queries; keep the two in step.
MET_PREREQUISITE_STATUSES = frozenset({"complete"})
DEAD_PREREQUISITE_STATUSES = frozenset({"abandoned", "declined", "cancelled"})

# A partial title shorter than this matches too much to mean anything.
_MIN_PARTIAL_TITLE_CHARS = 3

_RELEASE_REASON = "All prerequisites complete"
_FAILURE_NOTICE_TAIL = "It will not start until the boss resumes or cancels it."


class PrerequisiteUnavailable(Exception):
    """A prerequisite closed without completing in the instant after it was resolved.

    Raised by ``core.tasking.service`` when ``db.create_task`` refuses the
    task (``db.PrerequisiteClosed``, chained as ``__cause__``): nothing was
    created. ``str(exc)`` is the sentence for the delegating agent, the same
    one resolution gives (:func:`closed_prerequisite_sentence`).
    """


@dataclass(frozen=True, slots=True)
class PrerequisiteResolution:
    """What the references a PM wrote resolved to.

    Attributes:
        tasks: Every matched prerequisite, in the order written, duplicates
            collapsed to the first.
        errors: One sentence per entry that could not be used, each naming
            the entry. Any error refuses the whole request.
    """

    tasks: tuple[Task, ...]
    errors: tuple[str, ...]


def resolve_prerequisites(
    *,
    agent: Agent,
    references: Sequence[str],
    sibling_ids: Mapping[str, str] | None = None,
    parent_task_id: str | None = None,
) -> PrerequisiteResolution:
    """Turn the ``after`` entries a delegating agent wrote into tasks.

    Each entry is resolved on its own, first match wins:

    1. Plan key: an entry equal to a key of ``sibling_ids`` (trimmed,
       ignoring case) is that sibling.
    2. Exact id of a candidate.
    3. Exact title: equal ``normalize_workstream_title`` forms. One is a
       match; more than one is ambiguous.
    4. Partial title, only with no exact title match and an entry of at
       least three characters: the entry's words appear as a contiguous run
       in the candidate's title. One is a match; more is ambiguous.

    Candidates are the open tasks (``held`` included: chaining onto held work
    is the normal case) the agent requested on its own floor. Only an entry
    that matches none of them is tried against the agent's finished tasks on
    its floor: a unique ``complete`` match is a prerequisite already met, a
    unique match closed without completing is an error, since waiting on it
    could never end. Other agents' tasks and other floors are never matched,
    so an agent's references cannot reach anyone else's work.

    A match that is the new task's parent, or any task above it, is an
    error: the parent cannot complete while this child is open, so waiting
    on it would never end.

    Args:
        agent: The delegating agent.
        references: The entries as written (``after``).
        sibling_ids: Plan-local key -> id of the children already created or
            bound in the same plan, or ``None`` outside a plan.
        parent_task_id: The parent the new task is created under, or
            ``None`` for a top-level task.

    Returns:
        The matched tasks and the error sentences.

    Raises:
        LookupError: A task in the parent chain has no row.
    """
    from core.floors import home_floor_id, task_floor_id

    floor_id = home_floor_id(agent.id)
    ancestor_ids = _ancestor_ids(parent_task_id)

    def on_agent_floor(tasks: list[Task]) -> list[Task]:
        # An agent with no floor has no floor to share with any task.
        return [task for task in tasks if floor_id is not None and task_floor_id(task) == floor_id]

    open_candidates = on_agent_floor(db.list_tasks_by_statuses(OPEN_TASK_STATUSES, requester_id=agent.id))
    finished_candidates: list[Task] | None = None
    siblings = {str(key).strip().lower(): task_id for key, task_id in (sibling_ids or {}).items()}
    sibling_tasks = db.get_tasks_by_ids(list(siblings.values()))

    matched: list[Task] = []
    errors: list[str] = []

    def take(task: Task) -> None:
        if task.id in ancestor_ids:
            errors.append(f'"{task.title}" is the task this one belongs to, so it cannot wait on it.')
        else:
            _append_once(matched, task)

    for raw in references:
        entry = str(raw).strip()
        sibling_id = siblings.get(entry.lower())
        if sibling_id is not None:
            take(sibling_tasks[sibling_id])
            continue
        outcome, found = _match_entry(entry, open_candidates)
        if outcome == "none":
            if finished_candidates is None:
                finished_candidates = on_agent_floor(
                    db.list_tasks_by_statuses(tuple(TERMINAL_TASK_STATUSES), requester_id=agent.id)
                )
            outcome, found = _match_entry(entry, finished_candidates)
            if outcome == "unique" and found[0].status in DEAD_PREREQUISITE_STATUSES:
                errors.append(closed_prerequisite_sentence(found[0].title, found[0].status))
                continue
        if outcome == "unique":
            take(found[0])
        elif outcome == "ambiguous":
            errors.append(_ambiguous_sentence(entry, found))
        else:
            errors.append(f'Nothing of yours matches "{entry}". Use the title of one of your open tasks.')
    return PrerequisiteResolution(tasks=tuple(matched), errors=tuple(errors))


def closed_prerequisite_sentence(title: str, status: str) -> str:
    """The sentence for a prerequisite that closed without completing.

    Shared by resolution and by creation (``core.tasking.service``), where a
    prerequisite can close in the instant after it was resolved.
    """
    return f'"{title}" was {status}, so a task cannot wait on it.'


def has_unfinished_prerequisites(task_id: str) -> bool:
    """Return whether ``task_id`` still waits on a prerequisite that is not ``complete``.

    The one predicate behind every assignee-facing exclusion: such a task is
    held, or blocked because a prerequisite failed, and is not its
    assignee's to act on.
    """
    return db.count_unfinished_prerequisites(task_id) > 0


def is_released(task: Task) -> bool:
    """Return whether ``task`` is its assignee's to see and act on.

    The assignee-facing form of the module's invariant: a task is released
    unless it is ``held``, or still waits on a prerequisite that is not
    ``complete`` (blocked because one failed). Every selector that hands work
    to its assignee filters on this.

    Args:
        task: The task as read (``prerequisites`` hydrated). One with no
            prerequisites skips the read: it cannot have an unfinished one.
    """
    return task.status != "held" and not (task.prerequisites and has_unfinished_prerequisites(task.id))


def unfinished_prerequisites(task: Task) -> list[Task]:
    """Return ``task``'s prerequisites that are not ``complete``, in edge order.

    One read, none when the task has no prerequisites.
    """
    if not task.prerequisites:
        return []
    by_id = db.get_tasks_by_ids(task.prerequisites)
    return [
        by_id[prerequisite_id]
        for prerequisite_id in task.prerequisites
        if by_id[prerequisite_id].status not in MET_PREREQUISITE_STATUSES
    ]


def waiting_phrase(labels: Sequence[str]) -> str:
    """Say what a task waits on: ``<a> finishes``, ``<a> and <b> finish``, ``<a>, <b> and <c> finish``.

    Args:
        labels: Already formatted names, e.g. ``"Research"`` or
            ``"Research" (Taylor)``. Must not be empty.

    Raises:
        ValueError: ``labels`` is empty.
    """
    if not labels:
        raise ValueError("waiting_phrase needs at least one label")
    if len(labels) == 1:
        return f"{labels[0]} finishes"
    return f"{', '.join(labels[:-1])} and {labels[-1]} finish"


def assignee_label(task: Task, agents: Mapping[str, Agent]) -> str:
    """Name a task's assignee for an agent-facing sentence about a prerequisite.

    Args:
        task: The task.
        agents: Agent id -> agent, covering ``task.assigned_to`` when it is set.

    Returns:
        The assignee's name; its id when the agent row is gone (as the board
        shows it); ``unassigned`` when nobody is assigned.
    """
    if not task.assigned_to:
        return "unassigned"
    agent = agents.get(task.assigned_to)
    return agent.name if agent is not None else task.assigned_to


def board_status_cells(tasks: Sequence[Task]) -> dict[str, str]:
    """The status each Task Board row shows, with what an unreleased task waits on.

    A held row reads ``held, after "Research", "Outline"`` (its unfinished
    prerequisites). A blocked row whose prerequisite closed without
    completing reads ``blocked, "Research" was cancelled``; one blocked with
    other prerequisites still open (the other-floor case) reads
    ``blocked, after "Outline"``. Every other row is its plain status.

    Args:
        tasks: The rows shown.

    Returns:
        Task id -> status cell. One read for all the rows' prerequisites,
        none when no held or blocked row has any.
    """
    waiting = [task for task in tasks if task.status in {"held", "blocked"} and task.prerequisites]
    by_id = db.get_tasks_by_ids([prerequisite_id for task in waiting for prerequisite_id in task.prerequisites])
    cells = {task.id: task.status for task in tasks}
    for task in waiting:
        unfinished = [
            by_id[prerequisite_id]
            for prerequisite_id in task.prerequisites
            if by_id[prerequisite_id].status not in MET_PREREQUISITE_STATUSES
        ]
        if not unfinished:
            continue
        closed = [item for item in unfinished if item.status in DEAD_PREREQUISITE_STATUSES]
        if task.status == "blocked" and closed:
            cells[task.id] = "blocked, " + ", ".join(f'"{item.title}" was {item.status}' for item in closed)
        else:
            cells[task.id] = f"{task.status}, after " + ", ".join(f'"{item.title}"' for item in unfinished)
    return cells


def settle_dependents(finished: Task) -> None:
    """Release or fail the held tasks waiting on ``finished``, which just closed.

    Called by ``transition_task`` on entry to a terminal status, after that
    task's own write and event. For each held dependent, in order:

    - its parent task is closed: skipped and left held for the operator to
      cancel (a closed workstream wakes nobody);
    - ``finished`` closed without completing: blocked with
      ``Prerequisite "<title>" was <status>.``, requester told. Checked
      before floors: a prerequisite cancelled because its agent was deleted
      can have no floor, and it was cancelled, not moved;
    - it and ``finished`` are on different floors: blocked with
      ``Prerequisite "<title>" is on another floor.`` (a chain never releases
      across floors);
    - ``finished`` completed and nothing else is unfinished: released;
    - otherwise (other prerequisites still open): left held.

    A ``TaskStatusChanged`` means another settle already moved the dependent
    (two prerequisites finishing at once): it is skipped. Any other exception
    for one dependent is logged at ERROR with both task ids and the loop goes
    on, deliberately: ``finished``'s own transition has already committed and
    is correct, so a downstream fault must not fail the agent's done or the
    operator's cancel and trigger a retry. A fault before the release leaves
    that dependent held, shown on the board as held with every prerequisite
    finished, where the operator can release it. A fault after the release
    but before the wake leaves it ``pending`` with no wake:
    ``next_work_after_end`` or the dispatcher's backlog rebuild presents it
    on the assignee's next work pick-up.

    Args:
        finished: The task as stored after its terminal transition.
    """
    for dependent_id in db.list_held_dependents(finished.id):
        try:
            _settle_one(dependent_id, finished)
        except TaskStatusChanged as exc:
            logger.info("Dependent %s was already settled elsewhere (now %s)", dependent_id, exc.actual)
        except Exception:
            logger.exception(
                "Failed to settle dependent task %s after prerequisite %s finished",
                dependent_id,
                finished.id,
            )


def release_task(task: Task) -> None:
    """Move a held task to ``pending`` and wake its assignee: every prerequisite is complete.

    The transition is a compare-and-set from ``held`` and logs its own
    ``status_update`` event. When ``assignment_wake_trigger`` returns
    nothing (the assignee has left the task's floor), the task waits in
    ``pending``, as an operator resume leaves it.

    Raises:
        TaskStatusChanged: The task is no longer held.
    """
    from core.agent_loop.activity_scheduler import assignment_wake_trigger, persist_result_triggers

    released = transition_task(
        task.id,
        "pending",
        expected_from="held",
        reason=_RELEASE_REASON,
        actor="BossMod",
        status_note=None,
    )
    wake = assignment_wake_trigger(released)
    if wake is None:
        return
    persist_result_triggers({"trigger_requests": [wake]})


def fail_dependent(task: Task, failed: Task, *, sentence: str | None = None) -> None:
    """Move a held task to ``blocked`` because it can no longer start on its own.

    Records a ``blocker`` event and sends a ``task_update`` notice to the
    task's requester, the agent that wrote the dependency (its owner can be
    the assignee itself). Nothing goes to the assignee: the task shows in
    the operator's "Needs you" queue, and only the boss's Resume or Cancel
    moves it on.

    Args:
        task: The held dependent.
        failed: The prerequisite that caused it.
        sentence: Why, when it is not that ``failed`` closed without
            completing (``Prerequisite "<title>" was <status>.``).

    Raises:
        TaskStatusChanged: The task is no longer held.
    """
    from core.agent_loop.activity_scheduler import build_task_update_trigger, persist_result_triggers
    from core.tasking.service import append_task_event

    text = sentence or f'Prerequisite "{failed.title}" was {failed.status}.'
    blocked = transition_task(
        task.id,
        "blocked",
        expected_from="held",
        reason=text,
        actor="BossMod",
        status_note=text,
    )
    append_task_event(
        task_id=task.id,
        author_type="system",
        author_name="BossMod",
        event_type="blocker",
        content=text,
    )
    requester = db.get_agent(task.requester_id) if task.requester_id else None
    if requester is None:
        logger.info(
            "Blocked task %s has no live requester (%s) to tell", task.id, task.requester_id,
        )
        return
    persist_result_triggers({
        "trigger_requests": [
            build_task_update_trigger(
                blocked,
                recipient_agent_id=requester.id,
                from_agent=None,
                from_name="BossMod",
                content=f"{text} {_FAILURE_NOTICE_TAIL}",
                attention_kind="blocker",
                source_channel="work",
            )
        ]
    })


def _settle_one(dependent_id: str, finished: Task) -> None:
    """Apply :func:`settle_dependents`' rules to one held dependent."""
    from core.floors import task_floor_id

    dependent = db.get_task(dependent_id)
    if dependent is None:
        raise LookupError(f"Held dependent {dependent_id} has no task row")
    parent = db.get_task(dependent.parent_task_id) if dependent.parent_task_id else None
    if parent is not None and is_terminal_task_status(parent.status):
        logger.info("not settling %s: parent %s is closed", dependent.id, parent.id)
        return
    if finished.status in DEAD_PREREQUISITE_STATUSES:
        fail_dependent(dependent, finished)
    elif task_floor_id(dependent) != task_floor_id(finished):
        fail_dependent(dependent, finished, sentence=f'Prerequisite "{finished.title}" is on another floor.')
    elif finished.status in MET_PREREQUISITE_STATUSES and db.count_unfinished_prerequisites(dependent.id) == 0:
        release_task(dependent)


def _ancestor_ids(parent_task_id: str | None) -> set[str]:
    """The ids of ``parent_task_id`` and of every task above it; empty for ``None``.

    Raises:
        LookupError: A task in the chain has no row.
    """
    ids: set[str] = set()
    current = parent_task_id
    # parent_task_id is a writable column, so a repeat ends the walk rather
    # than looping on it.
    while current and current not in ids:
        ids.add(current)
        task = db.get_task(current)
        if task is None:
            raise LookupError(f"Task {current} in the parent chain has no task row")
        current = task.parent_task_id
    return ids


def _append_once(tasks: list[Task], task: Task) -> None:
    """Append ``task`` unless a task with its id is already listed."""
    if all(existing.id != task.id for existing in tasks):
        tasks.append(task)


def _match_entry(entry: str, candidates: list[Task]) -> tuple[str, list[Task]]:
    """Match one entry against ``candidates`` by exact id, exact title, then partial title.

    Returns:
        ``("unique", [task])``, ``("ambiguous", [task, ...])`` or ``("none", [])``.
    """
    by_id = [task for task in candidates if task.id == entry]
    if by_id:
        return "unique", by_id[:1]
    wanted = normalize_workstream_title(entry)
    if not wanted:
        return "none", []
    exact = [task for task in candidates if normalize_workstream_title(task.title) == wanted]
    if exact:
        return ("unique", exact) if len(exact) == 1 else ("ambiguous", exact)
    if len(wanted) < _MIN_PARTIAL_TITLE_CHARS:
        return "none", []
    # Word boundaries on both sides: "post" must not match inside "postmortem".
    pattern = re.compile(rf"(^| ){re.escape(wanted)}( |$)")
    partial = [task for task in candidates if pattern.search(normalize_workstream_title(task.title))]
    if not partial:
        return "none", []
    return ("unique", partial) if len(partial) == 1 else ("ambiguous", partial)


def _ambiguous_sentence(entry: str, tasks: list[Task]) -> str:
    """The error for an entry that matches more than one task, naming each."""
    agents = db.get_agents_by_ids([task.assigned_to for task in tasks if task.assigned_to])
    options = "; ".join(
        f'"{task.title}" ({assignee_label(task, agents)}, {task.status})' for task in tasks
    )
    return f'"{entry}" matches more than one task: {options}. Use a more specific title.'
