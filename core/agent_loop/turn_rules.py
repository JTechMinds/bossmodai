"""BossMod AI — Context-aware turn validation and follow-up rules."""

from __future__ import annotations

from typing import Any

from core.agent_loop.policies import TriggerPolicy
import db


_TASK_STATE_ACTIONS = {"complete", "blocked", "delegated", "abandoned"}


def validate_action_for_turn(
    action: dict[str, Any],
    policy: TriggerPolicy,
    active_task_id: str | None,
) -> str | None:
    """Validate an action against the runtime turn context."""
    action_name = action.get("action")

    if policy.require_task_context and not active_task_id:
        return "trigger requires active task context, but no active task is bound"

    if active_task_id and action_name == "idle" and not policy.allow_idle_with_active_task:
        return 'cannot use "idle" while a task is active'

    if action_name == "work" and not active_task_id:
        return '"work" requires an active task. Use "startTask" or "resumeTask" first.'

    if action_name in _TASK_STATE_ACTIONS and not (action.get("taskId") or active_task_id):
        return f'"{action_name}" requires an active task'

    if action_name == "startTask" and policy.trigger_type not in {"human_chat", "peer_message"}:
        return '"startTask" is only valid while handling a direct conversation'

    if action_name == "resumeTask" and policy.trigger_type not in {"human_chat", "peer_message", "task_attention_required"}:
        return '"resumeTask" is only valid while handling a direct interruption or attention request'

    if action_name == "resumeTask" and active_task_id:
        return 'cannot use "resumeTask" while a task is already active'

    return None


def should_end_turn_after_action(
    action: dict[str, Any],
    policy: TriggerPolicy,
) -> bool:
    """Return whether the turn should stop after this action."""
    if action.get("action") != "message":
        return False
    if not policy.end_turn_after_direct_reply:
        return False
    recipient_type = (action.get("recipientType") or "").strip().lower()
    return recipient_type in {"human", "agent"}


def maybe_append_resume_trigger(
    result: dict[str, Any],
    *,
    agent_id: str,
    policy: TriggerPolicy,
    initial_task_id: str | None,
    active_task_id: str | None,
) -> None:
    """After an interrupting reply, resume the original active task."""
    if not policy.auto_resume_previous_task:
        return
    if not initial_task_id or active_task_id != initial_task_id:
        return
    if result.get("event") != "message_sent":
        return

    queue_task_resumed_trigger(
        result,
        agent_id=agent_id,
        task_id=initial_task_id,
    )


def queue_task_resumed_trigger(
    result: dict[str, Any],
    *,
    agent_id: str,
    task_id: str | None,
) -> None:
    """Queue a task_resumed trigger when active work needs another turn."""
    if not task_id:
        return

    task = db.get_task(task_id)
    if not task or task.status != "active" or not task.assigned_to:
        return

    if db.has_open_trigger_matching(
        agent_id,
        trigger_types=["task_resumed"],
        task_id=task_id,
    ):
        return

    queued = result.setdefault("queued_triggers", [])
    queued.append({
        "agent_id": agent_id,
        "trigger_type": "task_resumed",
        "source_channel": "work",
        "task_id": task_id,
        "payload": {
            "task_title": task.title,
            "task_description": task.description or "",
        },
    })
