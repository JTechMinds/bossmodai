"""Moving people, threads and projects between floors.

Split from core/floors.py, which holds the gates (who may co-mingle with
whom). This module changes which floor things live on.

People and threads move as one plan (``plan_move``) the operator confirms
and then applies (``apply_move``). A thread brings its members. Three
groups come out of a plan:

* **Moving** — the explicit threads, all their members, and the people
  picked on their own.
* **Companions** — other active threads on a source floor whose members
  are ALL moving. They move too unless excluded by id.
* **Split** — threads that keep at least one member and lose at least
  one. They stay; the movers leave them. An excluded companion lands here.

Tasks are not moved: a task's floor derives from its thread, else its
assignee or owner (``core.floors.task_floor_id``). The plan lists the open
tasks a move strands: work of a moving agent bound to a thread that stays.

The plan carries a fingerprint of everything it lists. ``apply_move``
recomputes the plan and refuses one whose fingerprint differs, so nothing
moves that the operator was not shown.

``/me`` (the agent's own folder) is not a floor's and travels with the
agent; ``/projects`` becomes the new floor's folder the moment the agent's
``floor_id`` changes (core/bm_cli/floor_roots.py).
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.floors import AgentOnVacation, is_on_vacation

logger = logging.getLogger(__name__)

MoveReason = Literal["picked", "thread member"]

_PROJECTS_MOUNT = "/projects"
_ME_MOUNT = "/me"


class MoveRefused(ValueError):
    """The request cannot become a plan (nothing chosen, already there, archived, not a companion)."""


class MovePlanChanged(Exception):
    """The plan the operator confirmed is no longer what a move would do."""

    def __init__(self) -> None:
        super().__init__("Something changed since this move was planned. Review it again.")


class ProjectExists(Exception):
    """The target floor already has a project folder with this name."""


@dataclass(frozen=True)
class AgentRef:
    """One agent a plan moves, and why it is moving."""

    id: str
    name: str
    from_floor_id: str
    reason: MoveReason


@dataclass(frozen=True)
class ThreadRef:
    """One thread a plan moves (explicitly, or as a companion)."""

    id: str
    name: str
    from_floor_id: str
    member_names: list[str]


@dataclass(frozen=True)
class SplitRef:
    """A thread that stays on its floor and loses the members who move."""

    id: str
    name: str
    from_floor_id: str
    leaving_names: list[str]
    staying_names: list[str]


@dataclass(frozen=True)
class TaskRef:
    """An open task a move strands on a thread that stays behind."""

    id: str
    title: str
    thread_name: str


@dataclass
class MovePlan:
    """What a move of people and threads would do. Read-only; see ``plan_move``."""

    target_floor_id: str
    agents: list[AgentRef] = field(default_factory=list)
    threads: list[ThreadRef] = field(default_factory=list)
    companions: list[ThreadRef] = field(default_factory=list)
    split_threads: list[SplitRef] = field(default_factory=list)
    stranded_tasks: list[TaskRef] = field(default_factory=list)
    fingerprint: str = ""


@dataclass
class MoveResult:
    """What ``apply_move`` changed, by id."""

    moved_agents: list[str] = field(default_factory=list)
    moved_threads: list[str] = field(default_factory=list)
    left_threads: list[str] = field(default_factory=list)
    # Movers whose CLI working directory pointed into the floor they left.
    cwd_reset: list[str] = field(default_factory=list)


@dataclass
class ProjectMoveResult:
    """What ``move_project`` changed."""

    project: str
    from_floor_id: str
    to_floor_id: str
    artifacts_rewritten: int
    consent_paths_rewritten: int
    cwd_reset_agent_ids: list[str] = field(default_factory=list)


def _floor_of(row: Any) -> str:
    return str(getattr(row, "floor_id", None) or "").strip()


def _unique(ids: Any) -> list[str]:
    return list(dict.fromkeys(str(token).strip() for token in (ids or ()) if str(token or "").strip()))


def _require_floor(floor_id: str) -> Any:
    from db.floors import get_floor

    floor = get_floor((floor_id or "").strip())
    if floor is None:
        raise LookupError("Floor not found")
    return floor


def _fingerprint(plan: MovePlan) -> str:
    """sha256 over every id the plan lists, list by list, each list sorted.

    Split threads contribute their leaving and staying names too: a thread
    that gained a member since the plan was shown means something different.
    """
    parts = [
        "target:" + plan.target_floor_id,
        "agents:" + ",".join(sorted(ref.id for ref in plan.agents)),
        "threads:" + ",".join(sorted(ref.id for ref in plan.threads)),
        "companions:" + ",".join(sorted(ref.id for ref in plan.companions)),
        "split:" + ",".join(sorted(
            f"{ref.id}[{'|'.join(sorted(ref.leaving_names))}/{'|'.join(sorted(ref.staying_names))}]"
            for ref in plan.split_threads
        )),
        "stranded:" + ",".join(sorted(ref.id for ref in plan.stranded_tasks)),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cwd_is_under(cwd: str, *, virtual_root: str, real_root: Path) -> bool:
    """True when a stored CLI cwd points at or below either root.

    A cwd is stored as the virtual path ``cd`` resolved to
    (core/bm_cli/fs_commands.py): ``/me/…``, ``/projects/…``, or, for a
    directory reached through an extra host root, its absolute path. The
    absolute form can therefore name a floor folder directly, so both forms
    are checked.
    """
    if cwd == virtual_root or cwd.startswith(virtual_root + "/"):
        return True
    # A virtual mount is never a real path; resolving "/projects/x" on the
    # host would compare an unrelated directory.
    mounts = (_ME_MOUNT, _PROJECTS_MOUNT)
    if not cwd.startswith("/") or any(cwd == m or cwd.startswith(m + "/") for m in mounts):
        return False
    return Path(cwd).resolve().is_relative_to(real_root)


def _reset_stale_cwds(agent_ids: list[str], *, virtual_root: str, real_root: Path, why: str) -> list[str]:
    """Put agents whose cwd points into a place they no longer reach back at ``/me``.

    Only agents that already have a CLI state row are looked at; none is
    created. Logs INFO per reset.

    Returns:
        The ids whose cwd was reset, in ``agent_ids`` order.
    """
    import db

    reset: list[str] = []
    for agent_id in agent_ids:
        state = db.get_agent_cli_state(agent_id)
        cwd = str(getattr(state, "cwd", None) or "")
        if not cwd or not _cwd_is_under(cwd, virtual_root=virtual_root, real_root=real_root):
            continue
        db.update_agent_cli_state(agent_id, cwd=_ME_MOUNT)
        reset.append(agent_id)
        logger.info("Agent %s was in %s, %s; cwd is now %s", agent_id, cwd, why, _ME_MOUNT)
    return reset


def plan_move(
    target_floor_id: str,
    *,
    agent_ids: list[str],
    channel_ids: list[str],
    exclude_companion_ids: list[str] | tuple[str, ...] = (),
) -> MovePlan:
    """Work out what moving these people and threads to a floor would do.

    Writes nothing.

    Raises:
        LookupError: The target floor, an agent, or a thread does not exist.
        AgentOnVacation: A picked agent is on vacation (``bring_back`` is the way).
        MoveRefused: Nothing was chosen; a thread is archived; an agent or a
            thread is already on the target; an excluded id is not a companion.
    """
    import db
    from core.tasking.resolution import OPEN_TASK_STATUSES

    target = _require_floor(target_floor_id).id
    picked_ids = _unique(agent_ids)
    thread_ids = _unique(channel_ids)
    excluded = set(_unique(exclude_companion_ids))
    if not picked_ids and not thread_ids:
        raise MoveRefused("Choose at least one person or thread to move")

    agents: dict[str, Any] = {}
    already_there: list[str] = []
    for agent_id in picked_ids:
        agent = db.get_agent(agent_id)
        if agent is None:
            raise LookupError(f"Agent not found: {agent_id}")
        if is_on_vacation(agent):
            raise AgentOnVacation(f"{agent.name} is on vacation. Bring them back first.")
        if _floor_of(agent) == target:
            already_there.append(agent.name)
        agents[agent.id] = agent

    threads: dict[str, Any] = {}
    for channel_id in thread_ids:
        channel = db.get_channel(channel_id)
        if channel is None:
            raise LookupError(f"Thread not found: {channel_id}")
        if channel.status != "active":
            raise MoveRefused(f"Thread {channel.name} is archived and cannot move")
        if _floor_of(channel) == target:
            already_there.append(channel.name)
        threads[channel.id] = channel
    if already_there:
        raise MoveRefused(f"Already on this floor: {', '.join(already_there)}")

    reasons: dict[str, MoveReason] = {agent_id: "picked" for agent_id in agents}
    members: dict[str, list[dict[str, Any]]] = {}

    def members_of(channel_id: str) -> list[dict[str, Any]]:
        if channel_id not in members:
            members[channel_id] = db.list_channel_member_details(channel_id)
        return members[channel_id]

    for channel_id in threads:
        for member in members_of(channel_id):
            member_id = str(member["id"])
            reasons.setdefault(member_id, "thread member")
            if member_id not in agents:
                agent = db.get_agent(member_id)
                if agent is None:
                    raise LookupError(f"Agent not found: {member_id}")
                # Vacation releases every seat, so this is a broken row; moving
                # it would give a vacationer a floor.
                if is_on_vacation(agent):
                    raise AgentOnVacation(f"{agent.name} is on vacation. Bring them back first.")
                agents[member_id] = agent
    moving = set(agents)
    source_floors = {_floor_of(agent) for agent in agents.values()} | {
        _floor_of(channel) for channel in threads.values()
    }
    source_floors.discard("")
    source_floors.discard(target)

    plan = MovePlan(target_floor_id=target)
    plan.agents = sorted(
        (AgentRef(agent.id, agent.name, _floor_of(agent), reasons[agent.id]) for agent in agents.values()),
        key=lambda ref: (ref.reason != "picked", ref.name.lower()),
    )
    plan.threads = [
        ThreadRef(channel.id, channel.name, _floor_of(channel),
                  [str(member["name"]) for member in members_of(channel.id)])
        for channel in threads.values()
    ]

    companion_ids: set[str] = set()
    for channel in db.list_channels(status="active"):
        if channel.id in threads or _floor_of(channel) not in source_floors:
            continue
        roster = members_of(channel.id)
        member_ids = {str(member["id"]) for member in roster}
        leaving = member_ids & moving
        if not leaving:
            continue
        names = [str(member["name"]) for member in roster]
        if leaving == member_ids and channel.id not in excluded:
            companion_ids.add(channel.id)
            plan.companions.append(ThreadRef(channel.id, channel.name, _floor_of(channel), names))
            continue
        plan.split_threads.append(SplitRef(
            channel.id,
            channel.name,
            _floor_of(channel),
            leaving_names=[str(m["name"]) for m in roster if str(m["id"]) in moving],
            staying_names=[str(m["name"]) for m in roster if str(m["id"]) not in moving],
        ))
    not_companions = excluded - {ref.id for ref in plan.split_threads if not ref.staying_names}
    if not_companions:
        raise MoveRefused(f"Not a thread that would move along: {', '.join(sorted(not_companions))}")

    moving_threads = set(threads) | companion_ids
    seen: set[str] = set()
    thread_names: dict[str, str] = {}
    for agent_id in sorted(moving):
        for status in OPEN_TASK_STATUSES:
            for task in (*db.list_tasks(assigned_to=agent_id, status=status),
                         *db.list_tasks(owner_id=agent_id, status=status)):
                bound = str(getattr(task, "notification_channel_id", None) or "").strip()
                if task.id in seen or not bound or bound in moving_threads:
                    continue
                if bound not in thread_names:
                    channel = db.get_channel(bound)
                    # A task bound to a thread that no longer exists has no
                    # floor at all; the move does not change that.
                    if channel is None or _floor_of(channel) == target:
                        continue
                    thread_names[bound] = channel.name
                seen.add(task.id)
                plan.stranded_tasks.append(TaskRef(task.id, task.title, thread_names[bound]))

    plan.fingerprint = _fingerprint(plan)
    return plan


async def apply_move(
    target_floor_id: str,
    *,
    agent_ids: list[str],
    channel_ids: list[str],
    exclude_companion_ids: list[str] | tuple[str, ...] = (),
    fingerprint: str,
    services: Any,
) -> MoveResult:
    """Move the planned people and threads to a floor, if the plan still holds.

    Each moving agent's live turn is cancelled (``services.reset_agent_runtime``)
    before anything is written, as a floor delete does. Then, in one
    transaction: moving threads take the target floor; moving agents take it
    too; movers leave every thread that is not on the target afterwards
    (split threads, and archived threads elsewhere); their queued triggers
    bound to those threads are dropped; and a mover whose CLI working
    directory is under ``/projects`` (or, stored as an absolute path, under
    the folder of the floor it left) is put back at ``/me`` (``cwd_reset``).
    Otherwise a stale ``/projects/x`` would resolve to the new floor's
    same-named project.

    Raises:
        MovePlanChanged: The recomputed plan's fingerprint differs.
        LookupError, AgentOnVacation, MoveRefused: As ``plan_move``.
    """
    import db
    from core.bm_cli.floor_roots import floor_root
    from db.connection import transaction

    plan = plan_move(
        target_floor_id,
        agent_ids=agent_ids,
        channel_ids=channel_ids,
        exclude_companion_ids=exclude_companion_ids,
    )
    if plan.fingerprint != (fingerprint or "").strip():
        raise MovePlanChanged()

    target = plan.target_floor_id
    mover_ids = [ref.id for ref in plan.agents]
    thread_ids = [ref.id for ref in (*plan.threads, *plan.companions)]
    for agent_id in mover_ids:
        await services.reset_agent_runtime(agent_id)

    moved = set(thread_ids)
    left_behind = {
        channel.id
        for status in ("active", "archived")
        for channel in db.list_channels(status=status)
        if channel.id not in moved and _floor_of(channel) != target
    }
    # Resolved before the transaction: floor_root may create the folder.
    source_roots = {
        ref.id: floor_root(ref.from_floor_id).resolve() for ref in plan.agents if ref.from_floor_id
    }
    dropped = 0
    cwd_reset: list[str] = []
    with transaction():
        for channel_id in thread_ids:
            db.set_channel_floor(channel_id, target)
        for agent_id in mover_ids:
            if db.update_agent(agent_id, floor_id=target) is None:
                raise LookupError(f"Agent {agent_id} disappeared during the move")
            # Threads were re-floored above, so "not on the target" is now
            # exactly the split threads plus every thread elsewhere.
            db.execute(
                """
                DELETE FROM channel_members
                WHERE agent_id = $1
                  AND channel_id IN (SELECT id FROM channels WHERE floor_id IS NULL OR floor_id != $2)
                """,
                [agent_id, target],
            )
            dropped += db.delete_queued_triggers_for_agent_channels(agent_id, left_behind)
            if agent_id in source_roots:
                cwd_reset += _reset_stale_cwds(
                    [agent_id],
                    virtual_root=_PROJECTS_MOUNT,
                    real_root=source_roots[agent_id],
                    why=f"which is on the floor it left for {target}",
                )

    result = MoveResult(
        moved_agents=mover_ids,
        moved_threads=thread_ids,
        left_threads=[ref.id for ref in plan.split_threads],
        cwd_reset=cwd_reset,
    )
    logger.info(
        "Moved to floor %s: %d agent(s), %d thread(s); left %d thread(s); dropped %d queued trigger(s)",
        target, len(mover_ids), len(thread_ids), len(result.left_threads), dropped,
    )
    return result


def _project_name(project: str) -> str:
    """A project is one folder name on a floor: no separators, no dot-names."""
    name = (project or "").strip()
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Not a project name: {project!r}")
    return name


def list_projects(floor_id: str) -> list[dict[str, Any]]:
    """Every project on a floor: the floor folder's top-level directories.

    Dot-directories are not projects and are left out. Sorted by name,
    ignoring case.

    Returns:
        ``[{"name": str, "modified_at": ISO-8601 UTC str}]``.

    Raises:
        LookupError: No floor has this id (FloorRootUnavailable).
    """
    from core.bm_cli.floor_roots import floor_root

    root = floor_root(floor_id)
    projects = []
    for entry in os.scandir(root):
        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
            continue
        modified = datetime.fromtimestamp(entry.stat(follow_symlinks=False).st_mtime, tz=timezone.utc)
        projects.append({"name": entry.name, "modified_at": modified.isoformat()})
    return sorted(projects, key=lambda row: str(row["name"]).lower())


def move_project(project: str, from_floor_id: str, to_floor_id: str) -> ProjectMoveResult:
    """Move one project folder (a git repo inside moves with it) to another floor.

    Absolute paths stored for artifacts and host-path consent under the old
    folder are rewritten. Agents still on the source floor whose CLI working
    directory was inside the project (as ``/projects/<project>/…`` or as its
    absolute path) are put back at ``/me``: it no longer exists for them.

    Raises:
        LookupError: A floor, or the project on the source floor, does not exist.
        ValueError: The floors are the same, or the name is not a folder name.
        ProjectExists: The target floor already has a folder by this name.
        OSError: The rename itself failed (e.g. the floors are on different
            filesystems); nothing was moved or rewritten.
    """
    import db
    from core.bm_cli.floor_roots import floor_root
    from db.host_path_consent import rewrite_host_path_prefix

    name = _project_name(project)
    source_floor = _require_floor(from_floor_id)
    target_floor = _require_floor(to_floor_id)
    if source_floor.id == target_floor.id:
        raise ValueError("The project is already on this floor")
    source = floor_root(source_floor.id) / name
    if not source.is_dir() or source.is_symlink():
        raise LookupError(f"No project {name} on {source_floor.name}")
    target = floor_root(target_floor.id) / name
    if target.exists() or target.is_symlink():
        raise ProjectExists(f"{target_floor.name} already has a project named {name}")

    old_path = source.resolve()
    # Both floors sit under the company root: a same-filesystem rename, atomic,
    # never a half-finished copy.
    os.replace(source, target)
    new_path = target.resolve()
    artifacts = db.rewrite_artifact_path_prefix(str(old_path), str(new_path))
    consent = rewrite_host_path_prefix(str(old_path), str(new_path))

    reset = _reset_stale_cwds(
        [agent.id for agent in db.list_agents() if _floor_of(agent) == source_floor.id],
        virtual_root=f"{_PROJECTS_MOUNT}/{name}",
        real_root=old_path,
        why=f"which moved to floor {target_floor.id}",
    )

    logger.info(
        "Moved project %s from floor %s to %s: %d artifact path(s), %d consent path(s) rewritten",
        name, source_floor.id, target_floor.id, artifacts, consent,
    )
    return ProjectMoveResult(
        project=name,
        from_floor_id=source_floor.id,
        to_floor_id=target_floor.id,
        artifacts_rewritten=artifacts,
        consent_paths_rewritten=consent,
        cwd_reset_agent_ids=reset,
    )


__all__ = [
    "AgentRef",
    "MovePlan",
    "MovePlanChanged",
    "MoveReason",
    "MoveRefused",
    "MoveResult",
    "ProjectExists",
    "ProjectMoveResult",
    "SplitRef",
    "TaskRef",
    "ThreadRef",
    "apply_move",
    "list_projects",
    "move_project",
    "plan_move",
]
