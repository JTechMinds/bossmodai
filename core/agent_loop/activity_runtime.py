"""BossMod AI — Runtime activity state transitions."""

from __future__ import annotations

from typing import Any

import db
from core.models import Activity, Agent, AgentState, Task
from core.tasking.transitions import is_terminal_task_status, transition_task

TERMINAL_WAKE_WHY = "Board task is already complete"
TERMINAL_WAKE_LINE = f"Blocked — {TERMINAL_WAKE_WHY}"
TERMINAL_WAKE_FEEDBACK_CODE = "complete_task_wake"

_VISIBLE_STATUS_BY_KIND = {
    "assignment": "work_active",
    "break": "social_active",
    "conversation": "work_active",
    "meeting": "work_active",
    "movement": "in_transit",
    "social": "social_active",
    "work": "work_active",
}

_TRANSIENT_ACTIVITY_KINDS = {"assignment", "break", "conversation", "meeting", "movement", "social"}


def _close_transient_activity(activity: Activity, *, detail: str | None = None) -> str | None:
    """Complete a transient activity and preserve its resumable parent chain."""
    parent_id = activity.parent_activity_id
    db.update_activity(activity.id, status="completed", detail=detail or activity.detail)
    return parent_id


def _prepare_parent_for_new_commitment(agent_id: str, *, reason: str) -> str | None:
    """Pause work or replace transient activity before starting a new commitment."""
    active = get_active_activity(agent_id)
    if not active:
        return None
    if active.kind == "work":
        paused = pause_active_work(agent_id, reason)
        return paused.id if paused else None
    return _close_transient_activity(active, detail=reason)


def begin_commitment_activity(
    agent_id: str,
    *,
    kind: str,
    title: str | None = None,
    detail: str | None = None,
    metadata: dict[str, Any] | None = None,
    reason: str,
) -> Activity:
    """Replace the current transient commitment and start a new one."""
    parent_activity_id = _prepare_parent_for_new_commitment(agent_id, reason=reason)
    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind=kind,
        title=title,
        detail=detail,
        parent_activity_id=parent_activity_id,
        metadata=metadata or {},
    )
    refresh_agent_status(agent_id)
    return activity


def get_active_activity(agent_id: str) -> Activity | None:
    """Return the active activity for an agent."""
    return db.get_active_activity(agent_id)


def get_active_work_activity(agent_id: str) -> Activity | None:
    """Return the active work activity for an agent, if any."""
    active = get_active_activity(agent_id)
    if active and active.kind == "work":
        return active
    return None


def get_active_task_id(agent_id: str) -> str | None:
    """Return the task bound to the current active work activity."""
    active = get_active_work_activity(agent_id)
    if not active:
        return None
    return active.task_id


def is_live_work_task(task: Task | None) -> bool:
    """Return whether ``task`` can legally move on a wake/pause/activate path."""
    return task is not None and not is_terminal_task_status(task.status)


def transition_live_work_task(
    task_id: str,
    to_status: str,
    *,
    reason: str,
    actor: str = "BossMod",
    **fields: Any,
) -> Task | None:
    """Transition a live Board task. Terminal rows are a no-op.

    Wake, pause, and activate must never raise ``complete → pending`` or
    ``complete → active``. Other illegal jumps still go through
    ``transition_task`` unchanged.
    """
    task = db.get_task(task_id)
    if task is None:
        return None
    if is_terminal_task_status(task.status):
        return task
    extra = {key: value for key, value in fields.items() if key != "status"}
    return transition_task(
        task_id,
        to_status,
        reason=reason,
        actor=actor,
        **extra,
    )


def resolve_live_wake_task(
    agent_id: str,
    *,
    exclude_task_id: str | None = None,
) -> Task | None:
    """Return a live Board task this agent can continue, if any.

    Prefers the current live work activity, then paused work, then any open
    assigned task. Complete rows are never returned.
    """
    active = get_active_work_activity(agent_id)
    if active and active.task_id and active.task_id != exclude_task_id:
        task = db.get_task(active.task_id)
        if is_live_work_task(task):
            return task

    for task in db.list_tasks(assigned_to=agent_id):
        if exclude_task_id and task.id == exclude_task_id:
            continue
        if not is_live_work_task(task):
            continue
        if db.get_resumable_work_activity(agent_id, task.id):
            return task

    for task in db.list_tasks(assigned_to=agent_id):
        if exclude_task_id and task.id == exclude_task_id:
            continue
        if is_live_work_task(task):
            return task
    return None


def close_terminal_work_activity(
    agent_id: str,
    *,
    reason: str,
    task_id: str | None = None,
) -> Activity | None:
    """Complete zombie work bound to a terminal Board task. Do not revive it."""
    closed: Activity | None = None
    active = get_active_work_activity(agent_id)
    if active and (task_id is None or active.task_id == task_id):
        bound = db.get_task(active.task_id) if active.task_id else None
        if bound is None or is_terminal_task_status(bound.status):
            closed = complete_activity(active.id, detail=reason)

    if task_id:
        bound = db.get_task(task_id)
        if bound is None or is_terminal_task_status(bound.status):
            resumable = db.get_resumable_work_activity(agent_id, task_id)
            if resumable:
                db.update_activity(resumable.id, status="completed", detail=reason)
                db.delete_work_snapshot(resumable.id)
                refresh_agent_status(agent_id)
                closed = db.get_activity(resumable.id) or closed
    return closed


def note_terminal_wake_block(agent_id: str, task: Task | None = None) -> str:
    """Post ``Blocked — Board task is already complete`` without wiping the row."""
    from core.agent_loop.task_origin_mirrors import named_origin_line, persist_unbound_status_line

    agent = db.get_agent(agent_id)
    if agent is None:
        return TERMINAL_WAKE_LINE
    channel_id = None
    if task is not None:
        raw = getattr(task, "notification_channel_id", None)
        if isinstance(raw, str) and raw.strip():
            channel_id = raw.strip()
    persist_unbound_status_line(
        agent=agent,
        content=named_origin_line(agent, TERMINAL_WAKE_LINE),
        kind="blocked_no_task",
        channel_id=channel_id,
    )
    return TERMINAL_WAKE_LINE


def terminal_wake_feedback(agent: Agent, task: Task | None = None) -> dict[str, Any]:
    """Soft-block payload when a wake would revive a complete Board task."""
    note_terminal_wake_block(agent.id, task)
    return {
        "event": "world_feedback",
        "feedback_code": TERMINAL_WAKE_FEEDBACK_CODE,
        "detail": TERMINAL_WAKE_LINE,
        "agent_name": agent.name,
        "trigger_requests": [],
    }


def refresh_agent_status(agent_id: str) -> AgentState | None:
    """Derive visible agent status from the active runtime activity."""
    active = get_active_activity(agent_id)
    if active and active.kind == "work" and active.task_id:
        from core.agent_loop.soft_blocks import clear_soft_block_for_live_work

        # Live work demotes sticky Soft-block. Waiting still reads as waiting.
        clear_soft_block_for_live_work(agent_id)
        task = db.get_task(active.task_id)
        if task is not None and task.status == "waiting":
            return db.update_agent_state(agent_id, status="waiting")
    if not active:
        if db.list_tasks(assigned_to=agent_id, status="blocked") or db.list_tasks(assigned_to=agent_id, status="stalled"):
            return db.update_agent_state(agent_id, status="blocked")
        if db.list_tasks(assigned_to=agent_id, status="waiting"):
            return db.update_agent_state(agent_id, status="waiting")
        return db.update_agent_state(agent_id, status="idle")
    return db.update_agent_state(
        agent_id,
        status=_VISIBLE_STATUS_BY_KIND.get(active.kind, "work_active"),
    )


def reconcile_after_turn_failure(agent_id: str, *, detail: str) -> AgentState | None:
    """Repair visible/runtime state after an unexpected turn exception.

    Work activities remain active because the durable task still exists.
    Transient wrapper activities are cancelled so their paused parent can resume.
    """
    active = get_active_activity(agent_id)
    if active and active.kind in _TRANSIENT_ACTIVITY_KINDS:
        cancel_activity(active.id, detail=detail)
    return refresh_agent_status(agent_id)


def pause_active_work(agent_id: str, reason: str, *, task_status: str = "pending") -> Activity | None:
    """Pause the active work activity and return it.

    A complete (or otherwise terminal) Board task is not moved back to
    ``pending`` / ``blocked``. The zombie work activity is closed instead.
    """
    active = get_active_work_activity(agent_id)
    if not active:
        return None

    task = db.get_task(active.task_id) if active.task_id else None
    if task is not None and is_terminal_task_status(task.status):
        close_terminal_work_activity(agent_id, reason=reason, task_id=task.id)
        return None

    db.update_activity(active.id, status="paused", detail=reason)
    if active.task_id:
        transition_live_work_task(
            active.task_id,
            task_status,
            reason=reason or f"Paused ({task_status}).",
            actor="BossMod",
            status_note=reason,
            completion_summary=None,
            watchdog_pinged_at=None,
        )
    refresh_agent_status(agent_id)
    return db.get_activity(active.id)


def complete_activity(activity_id: str, detail: str | None = None) -> Activity | None:
    """Complete a runtime activity and drop its frozen work, which cannot resume."""
    updated = db.update_activity(activity_id, status="completed", detail=detail)
    db.delete_work_snapshot(activity_id)
    if updated:
        if updated.parent_activity_id:
            parent = db.get_activity(updated.parent_activity_id)
            if parent and parent.status == "paused":
                db.update_activity(parent.id, status="active")
        refresh_agent_status(updated.agent_id)
    return updated


def cancel_activity(activity_id: str, detail: str | None = None) -> Activity | None:
    """Cancel a runtime activity and drop its frozen work, which cannot resume."""
    updated = db.update_activity(activity_id, status="cancelled", detail=detail)
    db.delete_work_snapshot(activity_id)
    if updated:
        if updated.parent_activity_id:
            parent = db.get_activity(updated.parent_activity_id)
            if parent and parent.status == "paused":
                db.update_activity(parent.id, status="active")
        refresh_agent_status(updated.agent_id)
    return updated


def start_assignment_activity(agent_id: str, task: Task) -> Activity:
    """Activate a pending task-assignment activity."""
    active = get_active_activity(agent_id)
    if active and active.kind == "assignment" and active.task_id == task.id:
        refresh_agent_status(agent_id)
        return active

    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind="assignment",
        task_id=task.id,
        title=task.title,
        detail=task.description,
        metadata={"task_title": task.title},
    )
    refresh_agent_status(agent_id)
    return activity


def start_conversation_activity(
    agent_id: str,
    *,
    title: str | None = None,
    detail: str | None = None,
    parent_activity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity:
    """Create an active conversation activity."""
    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind="conversation",
        title=title,
        detail=detail,
        parent_activity_id=parent_activity_id,
        metadata=metadata or {},
    )
    refresh_agent_status(agent_id)
    return activity


def start_meeting_activity(
    agent_id: str,
    *,
    title: str | None = None,
    detail: str | None = None,
    parent_activity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity:
    """Create an active meeting activity."""
    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind="meeting",
        title=title,
        detail=detail,
        parent_activity_id=parent_activity_id,
        metadata=metadata or {},
    )
    refresh_agent_status(agent_id)
    return activity


def start_break_activity(
    agent_id: str,
    *,
    title: str | None = None,
    detail: str | None = None,
    parent_activity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity:
    """Create an active break activity."""
    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind="break",
        title=title,
        detail=detail,
        parent_activity_id=parent_activity_id,
        metadata=metadata or {},
    )
    refresh_agent_status(agent_id)
    return activity


def start_movement_activity(
    agent_id: str,
    *,
    destination: str,
    parent_activity_id: str | None = None,
    detail: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity:
    """Pause the current activity and activate a movement activity."""
    active = get_active_activity(agent_id)
    resume_parent_id = parent_activity_id
    if active and active.kind != "movement":
        db.update_activity(active.id, status="paused")
        resume_parent_id = active.id

    activity = db.create_runtime_activity(
        agent_id=agent_id,
        kind="movement",
        parent_activity_id=resume_parent_id,
        destination=destination,
        detail=detail,
        metadata=metadata or {},
    )
    refresh_agent_status(agent_id)
    return activity


def activate_work_activity(
    agent_id: str,
    task: Task,
    *,
    title: str | None = None,
    detail: str | None = None,
    task_status: str = "active",
    supersede_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Activity | None:
    """Create or reactivate the runtime work activity for a live task.

    Complete Board tasks are never revived (no ``complete → active``). Callers
    that need work should pass a live task, or let ``prepare_trigger_context``
    retarget one.
    """
    task = db.get_task(task.id) or task
    if not is_live_work_task(task):
        close_terminal_work_activity(
            agent_id,
            reason=TERMINAL_WAKE_WHY,
            task_id=task.id,
        )
        return get_active_activity(agent_id)

    active = get_active_activity(agent_id)
    if active and active.kind == "work" and active.task_id == task.id:
        transition_live_work_task(
            task.id,
            task_status,
            reason=f"Work activity already active ({task_status}).",
            actor="BossMod",
            status_note=None,
            watchdog_pinged_at=None,
        )
        merged_metadata = metadata or active.metadata
        if merged_metadata != active.metadata:
            db.update_activity(active.id, metadata=merged_metadata)
        refresh_agent_status(agent_id)
        refreshed = db.get_activity(active.id)
        return refreshed or active

    if active and active.kind == "work" and active.task_id and active.task_id != task.id:
        pause_active_work(agent_id, supersede_note or "Paused for newer work.")
        active = get_active_activity(agent_id)

    active = get_active_activity(agent_id)
    if active and active.kind in {"assignment", "conversation", "meeting", "social"}:
        db.update_activity(active.id, status="completed")

    resumable = db.get_resumable_work_activity(agent_id, task.id)
    if resumable:
        activity = db.update_activity(
            resumable.id,
            status="active",
            title=title or resumable.title,
            detail=detail or resumable.detail,
            metadata=metadata or resumable.metadata,
        )
    else:
        activity = db.create_runtime_activity(
            agent_id=agent_id,
            kind="work",
            task_id=task.id,
            title=title or task.title,
            detail=detail or task.description,
            metadata=metadata or {},
        )

    transition_live_work_task(
        task.id,
        task_status,
        reason=f"Activated work activity ({task_status}).",
        actor="BossMod",
        status_note=None,
        watchdog_pinged_at=None,
    )
    refresh_agent_status(agent_id)
    return activity


def resolve_arrival(agent_id: str) -> Activity | None:
    """Complete active movement and resume the paused parent activity.

    A parent ``meeting`` whose session ended during the walk is not resumed:
    it is completed the way ``idle`` leaves a meeting, which resumes the work
    paused under it, and that work is returned instead.
    """
    active = get_active_activity(agent_id)
    if not active or active.kind != "movement":
        refresh_agent_status(agent_id)
        return active

    db.update_activity(active.id, status="completed")
    parent = db.get_activity(active.parent_activity_id) if active.parent_activity_id else None
    if parent and parent.status == "paused":
        # Imported here: actions_meetings imports this module at load time.
        from core.agent_loop.actions_meetings import _finished_meeting_hint

        # Every arrival (dispatcher, startup movement recovery, desk seating)
        # passes here, the one place a walker's meeting becomes current again.
        if _finished_meeting_hint(parent) is not None:
            complete_activity(parent.id, detail=parent.detail)
            return get_active_activity(agent_id)
        update_fields: dict[str, Any] = {"status": "active"}
        if parent.kind == "work":
            refreshed_parent = _clear_satisfied_desk_preference(agent_id, parent)
            if refreshed_parent is not None and refreshed_parent != parent.metadata:
                update_fields["metadata"] = refreshed_parent
        db.update_activity(parent.id, **update_fields)
        refresh_agent_status(agent_id)
        return db.get_activity(parent.id)

    refresh_agent_status(agent_id)
    return None


def list_active_movements() -> list[Activity]:
    """Return active movement activities for movement recovery."""
    return db.list_activities(kind="movement", status="active", limit=500)


def _clear_satisfied_desk_preference(agent_id: str, activity: Activity) -> dict[str, Any] | None:
    """Drop a work activity's desk preference once the agent is physically at the desk."""
    if (activity.metadata or {}).get("preferred_destination") != "desk":
        return None

    state = db.get_agent_state(agent_id)
    agent = db.get_agent(agent_id)
    if state is None or agent is None:
        return None
    if agent.desk_x is None or agent.desk_y is None:
        return None
    if (state.x, state.y) != (agent.desk_x, agent.desk_y):
        return None

    metadata = dict(activity.metadata or {})
    metadata.pop("preferred_destination", None)
    return metadata
