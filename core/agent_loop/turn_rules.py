"""BossMod AI — Context-aware validation rules for execution turns."""

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
        return '"work" requires an active task bound by the runtime'

    if action_name in _TASK_STATE_ACTIONS and not active_task_id:
        return f'"{action_name}" requires an active task'

    if action_name == "work" and active_activity_kind not in {"work"}:
        return '"work" is only valid while a work commitment is active'

    if action_name in {"attendMeeting", "remoteMeeting"} and active_activity_kind != "meeting":
        return f'"{action_name}" is only valid while a meeting commitment is active'

    if action_name in _TASK_STATE_ACTIONS and active_activity_kind != "work":
        return f'"{action_name}" is only valid while a work commitment is active'

    return None


def should_end_turn_after_action(
    action: dict[str, Any],
    policy: TriggerPolicy,
    active_activity_kind: str | None = None,
    result: dict[str, Any] | None = None,
) -> bool:
    """Return whether the turn should stop after this action."""
    action_name = action.get("action")
    if action_name in {"attendMeeting", "remoteMeeting"}:
        return (result or {}).get("event") == "meeting_started"
    if action_name != "message":
        return False
    recipient_type = (action.get("recipientType") or "").strip().lower()
    if recipient_type not in {"human", "agent"}:
        return False
    if policy.end_turn_after_direct_reply:
        return True
    if policy.trigger_type == "activity_resumed" and active_activity_kind in {"conversation", "meeting"}:
        return True
    return False
