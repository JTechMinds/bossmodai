"""Resume waiting work and close assignment wrappers after a decision."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import (
    build_activity_resume_trigger,
    build_task_resume_trigger,
)
from core.agent_loop.decision_contract import ConversationDecision
from core.models import Agent


def _resume_previous_work_if_needed(result: dict[str, Any], active_work: Any | None) -> None:
    """Queue a work resume trigger after a direct interruption if work stayed active."""
    if active_work is None:
        return
    if any(item.get("trigger_type") == "activity_resumed" for item in result["trigger_requests"]):
        return
    result["trigger_requests"].append(
        build_activity_resume_trigger(
            active_work,
            reason=f'Resume work on "{active_work.title or "your task"}".',
        )
    )

def _resume_waiting_work_after_task_attention(
    *,
    result: dict[str, Any],
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
    active_work: Any | None,
) -> None:
    """Resume a waiting/blocked task when a task-attention reply resolves the dependency."""
    if active_work is not None:
        return
    if trigger.get("type") != "task_follow_up" or decision.decision != "answer":
        return

    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return
    attention_kind = str(trigger.get("attention_kind") or "").strip().lower()
    task = db.get_task(task_id)
    if task is None:
        return

    if attention_kind in {"question", "review_request"}:
        target_agent_id = str(trigger.get("from_agent") or "").strip()
        if (
            target_agent_id
            and task.assigned_to == target_agent_id
            and task.status in {"waiting", "blocked"}
            and not db.has_open_trigger_matching(target_agent_id, trigger_types=["activity_resumed"], task_id=task.id)
        ):
            result["trigger_requests"].append(
                build_task_resume_trigger(
                    task,
                    reason=f'You received the task update you needed on "{task.title}". Continue the work.',
                )
            )
        return

    if attention_kind not in {"completion_report", "blocker", "handoff", "abandoned"}:
        return

    parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
    if (
        parent is not None
        and parent.assigned_to == agent.id
        and parent.status in {"waiting", "blocked"}
        and not db.has_open_trigger_matching(agent.id, trigger_types=["activity_resumed"], task_id=parent.id)
    ):
        result["trigger_requests"].append(
            build_task_resume_trigger(
                parent,
                reason=f'You received an update on "{task.title}". Continue "{parent.title}".',
            )
        )
        return

    if (
        task.assigned_to == agent.id
        and task.status in {"waiting", "blocked"}
        and not db.has_open_trigger_matching(agent.id, trigger_types=["activity_resumed"], task_id=task.id)
    ):
        result["trigger_requests"].append(
            build_task_resume_trigger(
                task,
                reason=f'You received the task update you needed on "{task.title}". Continue the work.',
            )
        )

def _resume_waiting_work_after_task_update(
    *,
    result: dict[str, Any],
    agent_id: str,
    trigger: dict[str, Any],
) -> None:
    """Resume waiting/blocked work after a task_update (informational) trigger."""
    if trigger.get("type") != "task_update":
        return
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return
    task = db.get_task(task_id)
    if task is None:
        return
    if task.assigned_to != agent_id:
        return
    if task.status not in {"waiting", "blocked"}:
        return
    if db.has_open_trigger_matching(agent_id, trigger_types=["activity_resumed"], task_id=task.id):
        return
    result["trigger_requests"].append(
        build_task_resume_trigger(
            task,
            reason=f'You received an update on "{task.title}". Continue the work.',
        )
    )

def _record_watchdog_reply_if_needed(*, agent_id: str, trigger: dict[str, Any], reply: str | None) -> None:
    """Refresh task liveness when the agent answers a watchdog ping."""
    if trigger.get("type") != "watchdog_status_ping":
        return
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return

    now = datetime.now(timezone.utc)
    status_note = (reply or "").strip() or None
    update_fields: dict[str, Any] = {
        "watchdog_pinged_at": None,
        "last_heartbeat_at": now,
        "last_activity": now,
    }
    if status_note:
        update_fields["status_note"] = status_note
    db.update_task(task_id, **update_fields)

    active = activity_runtime.get_active_work_activity(agent_id)
    if active and active.task_id == task_id and status_note:
        db.update_activity(active.id, detail=status_note)

def _complete_assignment_if_present(agent_id: str) -> None:
    """Complete the active assignment wrapper, if one exists."""
    active = activity_runtime.get_active_activity(agent_id)
    if active and active.kind == "assignment":
        activity_runtime.complete_activity(active.id, detail=active.detail)
