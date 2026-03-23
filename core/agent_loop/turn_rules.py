"""BossMod AI — Context-aware turn validation rules."""

from __future__ import annotations

from typing import Any

from core.agent_loop.policies import TriggerPolicy


_TASK_STATE_ACTIONS = {"complete", "blocked", "delegated", "abandoned"}


def validate_action_for_turn(
    action: dict[str, Any],
    policy: TriggerPolicy,
    active_activity_kind: str | None,
    active_task_id: str | None,
) -> str | None:
    """Validate an action against the runtime turn context."""
    action_name = action.get("action")

    if policy.require_work_activity and not active_task_id:
        return "trigger requires an active work activity, but no active task is bound"

    if active_task_id and action_name == "idle" and not policy.allow_idle_with_active_work:
        return 'cannot use "idle" while a task is active'

    if action_name == "work" and not active_task_id:
        return '"work" requires an active task. Use "startTask" or "resumeTask" first.'

    if action_name in _TASK_STATE_ACTIONS and not active_task_id:
        return f'"{action_name}" requires an active task'

    if action_name == "startTask" and policy.trigger_type not in {"human_chat", "peer_message"}:
        return '"startTask" is only valid while handling a direct conversation'

    if action_name == "resumeTask" and policy.trigger_type not in {"human_chat", "peer_message", "task_assigned", "activity_resumed"}:
        return '"resumeTask" is only valid while handling a direct interruption, assignment, or resumed activity'

    if action_name == "resumeTask" and active_task_id:
        return 'cannot use "resumeTask" while a task is already active'

    if action_name in {"attendMeeting", "remoteMeeting"} and active_activity_kind == "assignment":
        return "complete or convert the assignment before starting a meeting"

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
