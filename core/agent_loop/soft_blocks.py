"""Soft blocked one-liners: wait-without-task and guardian no_progress.

These must not become hard ``agent_error`` / diagnostic spam. Origin thread
gets one locked line. Wait without a task does not freeze the agent. No-progress
blocks the bound task (when there is one) and tags a next owner.

Soft-block stays an operator safety control. It auto-clears when the agent is
actually working the bound task so Board / Needs do not stay sticky Blocked.
"""

from __future__ import annotations

import asyncio
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.next_owner import (
    HUMAN_MENTION_NAMES,
    is_multi_party_channel,
    mention_names_for_channel,
)
from core.agent_loop.blocked_origin import (
    NO_PROGRESS_KIND,
    NO_PROGRESS_WHY,
    finish_blocked_origin,
    format_blocked_line,
)
from core.agent_loop.task_origin_mirrors import (
    attach_operator_status_line,
    named_origin_line,
    persist_unbound_status_line,
)
from core.models import Activity, Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import runtime_events as manager
from core.tasking.transitions import IllegalTaskTransition, transition_task

SOFT_BLOCK_STATUSES = frozenset({"blocked", "stalled"})
LIVE_WORK_CLEARS_REASON = "Live work cleared Soft-block."
STATUS_REPLY_RESUME_NOTE = "Resumed after a status reply."

WAITING_WITHOUT_TASK_CODE = "waiting_without_task"
WAITING_WITHOUT_TASK_LINE = "Blocked — wait needs an active task"

NO_PROGRESS_CODE = "no_progress_block"
NO_PROGRESS_LINE = format_blocked_line(NO_PROGRESS_WHY)


def clear_soft_block_for_live_work(agent_id: str) -> Task | None:
    """Demote Board Soft-block when the agent is executing the bound task.

    Live work activity wins over a sticky ``blocked`` / ``stalled`` row. True
    stalls keep Soft-block: paused work, or no bound task, is left alone.
    """
    active = activity_runtime.get_active_work_activity(agent_id)
    if active is None or not active.task_id:
        return None
    return _clear_soft_block_task(active.task_id, agent_id=agent_id, reason=LIVE_WORK_CLEARS_REASON)


def resume_soft_blocked_work(agent_id: str) -> Activity | None:
    """Clear live Soft-block, or reactivate paused Soft-blocked work.

    A status-only reply must not strand an open Board task. Returns the work
    activity only when Soft-block was actually cleared or resumed.
    """
    active = activity_runtime.get_active_work_activity(agent_id)
    if active is not None and active.task_id:
        if clear_soft_block_for_live_work(agent_id) is None:
            return None
        return activity_runtime.get_active_work_activity(agent_id) or active

    for status in ("blocked", "stalled"):
        for task in db.list_tasks(assigned_to=agent_id, status=status):
            paused = db.get_resumable_work_activity(agent_id, task.id)
            if paused is None:
                continue
            return activity_runtime.activate_work_activity(
                agent_id,
                task,
                title=task.title,
                detail=task.description,
                task_status="active",
                supersede_note=STATUS_REPLY_RESUME_NOTE,
            )
    return None


def waiting_without_task_result(
    agent: Agent,
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Soft-block wait when no work task is bound. Not a hard error."""
    result: dict[str, Any] = {
        "event": "world_feedback",
        "feedback_code": WAITING_WITHOUT_TASK_CODE,
        "detail": WAITING_WITHOUT_TASK_LINE,
        "agent_name": agent.name,
    }
    _attach_unbound_line(
        result,
        agent=agent,
        trigger=trigger,
        content=WAITING_WITHOUT_TASK_LINE,
        kind="blocked_no_task",
    )
    return result


def apply_no_progress_block(
    agent: Agent,
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Block the bound task (if any) and post a next-owner line. Not guardian noise."""
    mention = next_owner_mention(agent, trigger=trigger)
    content = format_blocked_line(NO_PROGRESS_WHY, mention)
    task_id = activity_runtime.get_active_task_id(agent.id)
    task = db.get_task(task_id) if task_id else None
    result: dict[str, Any] = {
        "event": "status_changed",
        "feedback_code": NO_PROGRESS_CODE,
        "detail": content,
        "agent_name": agent.name,
    }
    if task is not None:
        paused = activity_runtime.pause_active_work(
            agent.id,
            content,
            task_status="blocked",
        )
        if paused is None:
            try:
                transition_task(
                    task.id,
                    "blocked",
                    reason=content,
                    actor="BossMod",
                    actor_type="system",
                    status_note=content,
                    completion_summary=None,
                    watchdog_pinged_at=None,
                )
            except IllegalTaskTransition:
                pass
            activity_runtime.refresh_agent_status(agent.id)
        task = db.get_task(task.id) or task
        attach_operator_status_line(
            result,
            task=task,
            agent=agent,
            kind=NO_PROGRESS_KIND,
            reason=content,
            target_name=mention,
        )
        finish_blocked_origin(result, agent=agent, content=content, mention=mention)
        return result

    _attach_unbound_line(
        result,
        agent=agent,
        trigger=trigger,
        content=content,
        kind=NO_PROGRESS_KIND,
    )
    finish_blocked_origin(result, agent=agent, content=content, mention=mention)
    activity_runtime.refresh_agent_status(agent.id)
    return result


def next_owner_mention(
    agent: Agent,
    trigger: dict[str, Any] | None = None,
) -> str | None:
    """Return ``@Name`` for the next owner, or ``@Human Operator`` when none is clearer."""
    task_id = activity_runtime.get_active_task_id(agent.id)
    task = db.get_task(task_id) if task_id else None
    channel_id = _channel_id(task, trigger)
    author = (agent.name or "").strip().lower()
    if channel_id and is_multi_party_channel(channel_id):
        names = mention_names_for_channel(channel_id)
        teammates = [
            name
            for name in names
            if name
            and name.strip().lower() != author
            and name not in HUMAN_MENTION_NAMES
        ]
        if teammates:
            return f"@{teammates[0]}"
        return "@Human Operator"
    if task is not None:
        requester = getattr(task, "requester_id", None)
        if requester and requester != agent.id:
            if requester == HUMAN_SENDER_ID:
                return "@Human Operator"
            other = db.get_agent(requester)
            if other is not None and (other.name or "").strip() and _same_home(agent.id, other.id):
                return f"@{other.name}"
        owner = getattr(task, "owner_id", None)
        if owner and owner != agent.id:
            if owner == HUMAN_SENDER_ID:
                return "@Human Operator"
            other = db.get_agent(owner)
            if other is not None and (other.name or "").strip() and _same_home(agent.id, other.id):
                return f"@{other.name}"
    return "@Human Operator"


def _same_home(left: str, right: str) -> bool:
    """Same-floor check used when naming a soft-block next owner.

    Cross-floor names are skipped. The block itself is not cleared.
    """
    from core.floors import peers_share_floor

    return peers_share_floor(left, right)


def _channel_id(task: Any, trigger: dict[str, Any] | None) -> str | None:
    if isinstance(trigger, dict):
        raw = trigger.get("channel_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    channel_id = getattr(task, "notification_channel_id", None) if task is not None else None
    if isinstance(channel_id, str) and channel_id.strip():
        return channel_id.strip()
    return None


def _clear_soft_block_task(task_id: str, *, agent_id: str, reason: str) -> Task | None:
    task = db.get_task(task_id)
    if task is None or task.status not in SOFT_BLOCK_STATUSES:
        return None
    try:
        updated = transition_task(
            task.id,
            "active",
            reason=reason,
            actor="BossMod",
            actor_type="system",
            status_note=None,
            watchdog_pinged_at=None,
        )
    except IllegalTaskTransition:
        return None
    _schedule_status_changed(agent_id, updated)
    return updated


def _schedule_status_changed(agent_id: str, task: Task) -> None:
    agent = db.get_agent(agent_id)
    name = agent.name if agent is not None else None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        manager.broadcast_activity(
            event="status_changed",
            detail=_status_changed_detail(name, task),
            agent_name=name,
            extra={"task_id": task.id, "task_status": task.status},
        )
    )


def _status_changed_detail(agent_name: str | None, task: Task) -> str:
    title = (task.title or "task").strip() or "task"
    who = (agent_name or "Agent").strip() or "Agent"
    return f'{who} resumed work on "{title}"'


def _attach_unbound_line(
    result: dict[str, Any],
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
    content: str,
    kind: str,
) -> None:
    posted = persist_unbound_status_line(
        agent=agent,
        content=named_origin_line(agent, content),
        kind=kind,
        channel_id=_channel_id(None, trigger),
    )
    extras = result.setdefault("origin_status_messages", [])
    if posted.get("channel_message"):
        extras.append(posted["channel_message"])
        result.setdefault("channel_message", posted["channel_message"])
    if posted.get("chat_message"):
        extras.append(posted["chat_message"])
        result.setdefault("chat_message", posted["chat_message"])
