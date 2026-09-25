"""Fail-closed decision recovery: one operator note and one commitment wake.

Parse failures and LLM timeouts share this path after the decision repair
budget is spent. It does not wipe runtime, open an Approve card, or enqueue
another decision turn.
"""

from __future__ import annotations

from typing import Any

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import (
    build_activity_resume_trigger,
    build_task_resume_trigger,
)
from core.agent_loop.task_origin_mirrors import persist_unbound_status_line
from core.models import Agent, Task
from core.tasking.service import append_task_event

DECISION_REPAIR_ATTEMPTS_SETTING = "decision_repair_attempts"
DEFAULT_DECISION_REPAIR_ATTEMPTS = 6

PARSE_FAIL_NOTE = (
    "Decision parse failed — the turn did not return one JSON envelope. "
    "The commitment was re-queued."
)
PARSE_FAIL_IDLE_NOTE = (
    "Decision parse failed — the turn did not return one JSON envelope. "
    "No work was re-queued."
)
RESUME_REASON = "Decision parse failed. Continue the committed work."

TIMEOUT_NOTE = (
    "LLM request timed out — the turn did not return one JSON envelope. "
    "The commitment was re-queued."
)
TIMEOUT_IDLE_NOTE = (
    "LLM request timed out — the turn did not return one JSON envelope. "
    "No work was re-queued."
)
TIMEOUT_RESUME_REASON = "LLM request timed out. Continue the committed work."

_COMMITMENT_STATUSES = frozenset({"accepted", "active", "waiting", "blocked", "stalled"})
_NOTE_STATUSES = _COMMITMENT_STATUSES | {"pending"}


def decision_repair_attempt_limit() -> int:
    """Return the configured decision-repair budget. Missing or invalid values use the default."""
    configured = config.get_int(DECISION_REPAIR_ATTEMPTS_SETTING)
    if configured is None or configured < 0:
        return DEFAULT_DECISION_REPAIR_ATTEMPTS
    return configured


def surface_decision_parse_failure(
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
) -> dict[str, Any]:
    """Post one parse-fail note and re-queue an open work commitment."""
    return surface_commitment_recovery(
        agent=agent,
        trigger=trigger,
        requeued_note=PARSE_FAIL_NOTE,
        idle_note=PARSE_FAIL_IDLE_NOTE,
        resume_reason=RESUME_REASON,
    )


def surface_llm_timeout_failure(
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
) -> dict[str, Any]:
    """Post one timeout note and re-queue an open work commitment."""
    return surface_commitment_recovery(
        agent=agent,
        trigger=trigger,
        requeued_note=TIMEOUT_NOTE,
        idle_note=TIMEOUT_IDLE_NOTE,
        resume_reason=TIMEOUT_RESUME_REASON,
    )


def surface_commitment_recovery(
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
    requeued_note: str,
    idle_note: str,
    resume_reason: str,
) -> dict[str, Any]:
    """Re-queue an open work commitment and post one note that says whether it was.

    The requeue runs first so the note is chosen from its outcome: the
    operator is never told work was re-queued when nothing was. A second
    call for the same task does not post another copy of the same note. A
    commitment wake is skipped when that resume is already queued.
    """
    task = _task_for_note(agent, trigger)
    requests = requeue_commitment(agent, task, resume_reason=resume_reason)
    note = requeued_note if requests else idle_note
    posted = _post_note(agent, trigger, task, note=note)
    result: dict[str, Any] = {"trigger_requests": requests}
    if posted.get("chat_message"):
        result["chat_message"] = posted["chat_message"]
    if posted.get("channel_message"):
        result["channel_message"] = posted["channel_message"]
    return result


def _task_for_note(agent: Agent, trigger: dict[str, Any] | None) -> Task | None:
    """Prefer the trigger's task, then live work, then a stranded commitment."""
    triggered = _open_task(agent.id, _trigger_task_id(trigger), statuses=_NOTE_STATUSES)
    if triggered is not None:
        return triggered
    active = activity_runtime.get_active_work_activity(agent.id)
    if active is not None:
        bound = _open_task(agent.id, active.task_id, statuses=_NOTE_STATUSES)
        if bound is not None:
            return bound
    for status in ("blocked", "stalled", "waiting", "active", "accepted", "pending"):
        for task in db.list_tasks(assigned_to=agent.id, status=status):
            if db.get_resumable_work_activity(agent.id, task.id) is not None:
                return task
    for status in ("blocked", "stalled", "active", "waiting", "accepted"):
        rows = db.list_tasks(assigned_to=agent.id, status=status)
        if rows:
            return rows[0]
    return None


def requeue_commitment(
    agent: Agent,
    task: Task | None,
    *,
    resume_reason: str,
) -> list[dict[str, Any]]:
    """Return one activity resume for an open commitment, or ``[]``.

    Reactivates paused or soft-blocked work on ``task`` when needed. A
    pending assignment with no paused work is left alone so this path
    cannot re-fire the decision turn. Nothing is returned when a resume for
    the task is already queued.
    """
    if task is None:
        return []
    paused_commitment = (
        task.status == "pending" and db.get_resumable_work_activity(agent.id, task.id) is not None
    )
    if task.status not in _COMMITMENT_STATUSES and not paused_commitment:
        return []
    if db.has_open_trigger_matching(
        agent.id,
        trigger_types=["activity_resumed"],
        task_id=task.id,
    ):
        return []

    active = activity_runtime.get_active_work_activity(agent.id)
    if active is not None and active.task_id == task.id:
        return [_repair_wake(build_activity_resume_trigger(active, reason=resume_reason))]

    # Same resume the status-reply path uses: reactivate paused or
    # soft-blocked work, then queue one activity_resumed wake.
    paused = db.get_resumable_work_activity(agent.id, task.id)
    if paused is not None or task.status in {"blocked", "stalled", "waiting", "accepted"}:
        activated = activity_runtime.activate_work_activity(
            agent.id,
            task,
            title=task.title,
            detail=task.description,
            task_status="active",
            supersede_note=resume_reason,
        )
        if activated is not None and activated.kind == "work" and activated.task_id == task.id:
            return [_repair_wake(build_activity_resume_trigger(activated, reason=resume_reason))]
    return [_repair_wake(build_task_resume_trigger(task, reason=resume_reason))]


def _repair_wake(spec: dict[str, Any]) -> dict[str, Any]:
    """Mark a commitment resume so it waits behind a live channel lead."""
    payload = dict(spec.get("payload") or {})
    payload["repair_wake"] = True
    return {**spec, "payload": payload}


def _post_note(
    agent: Agent,
    trigger: dict[str, Any] | None,
    task: Task | None,
    *,
    note: str,
) -> dict[str, Any]:
    """One task-thread event when a task is bound; otherwise one origin note."""
    if task is not None:
        if _task_already_noted(task.id, note):
            return {}
        append_task_event(
            task_id=task.id,
            author_type="system",
            author_name="BossMod",
            author_agent_id=agent.id,
            event_type="system",
            content=note,
            source_trigger_id=_trigger_id(trigger),
        )
        return {}
    channel_id = None
    if isinstance(trigger, dict):
        raw = trigger.get("channel_id")
        if isinstance(raw, str) and raw.strip():
            channel_id = raw.strip()
    return persist_unbound_status_line(
        agent=agent,
        content=note,
        kind="task_update",
        channel_id=channel_id,
    )


def _task_already_noted(task_id: str, note: str) -> bool:
    for event in db.list_task_events(task_id, limit=30):
        if (event.content or "").strip() == note:
            return True
    return False


def _open_task(
    agent_id: str,
    task_id: str | None,
    *,
    statuses: frozenset[str],
) -> Task | None:
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    task = db.get_task(task_id)
    if task is None or task.assigned_to != agent_id:
        return None
    if task.status not in statuses:
        return None
    return task


def _trigger_task_id(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict):
        return None
    raw = trigger.get("task_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _trigger_id(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict):
        return None
    raw = trigger.get("trigger_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
