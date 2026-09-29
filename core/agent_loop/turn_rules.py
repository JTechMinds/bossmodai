"""BossMod AI — Context-aware validation rules for execution turns."""

from __future__ import annotations

from typing import Any

from core.agent_loop.policies import TriggerPolicy
from core.agent_loop.work_binding import is_detached


_TASK_STATE_ACTIONS = {"waiting", "complete", "blocked", "delegated", "abandoned"}
# Actions that start a movement or meeting activity, which would pause the
# live work activity a detached turn must leave untouched.
_MOVEMENT_MEETING_ACTIONS = {"walkTo", "attendMeeting", "remoteMeeting"}


def validate_action_for_turn(
    action: dict[str, Any],
    policy: TriggerPolicy,
    active_activity_kind: str | None,
    active_task_id: str | None,
    *,
    trigger: dict[str, Any],
) -> str | None:
    """Validate an action against the runtime turn context.

    Args:
        action: The parsed action.
        policy: The trigger type's policy.
        active_activity_kind: Kind of the turn's bound activity, if any.
        active_task_id: The turn's bound task, if any.
        trigger: The turn's live trigger. A detached turn
            (``work_binding.is_detached``) binds no task or activity, and
            task-state, movement and meeting actions are refused.

    Returns:
        The refusal message, or ``None`` when the action is valid here.
    """
    action_name = action.get("action")

    if is_detached(trigger):
        if action_name in _TASK_STATE_ACTIONS:
            # A detached turn sees no bound task, and "waiting" without one
            # would otherwise pass; the live task must not change state from here.
            return f'"{action_name}" is not available here: your task is paused unchanged; use "idle" when you are done'
        if action_name in _MOVEMENT_MEETING_ACTIONS:
            return f'"{action_name}" is not available while handling an extension event.'

    if policy.require_work_activity and not active_task_id:
        return "trigger requires an active work activity, but no active task is bound"

    if active_task_id and action_name == "idle":
        return 'cannot use "idle" while a task is active; use "wait", "done", "block", or keep working'

    if action_name == "work" and not active_task_id:
        return '"work" requires an active task bound by the runtime'

    if action_name in _TASK_STATE_ACTIONS and not active_task_id:
        if action_name == "waiting":
            return None
        return f'"{action_name}" requires an active task'

    if action_name == "work" and active_activity_kind not in {"work"}:
        return '"work" is only valid while a work commitment is active'

    if action_name in {"attendMeeting", "remoteMeeting"} and active_activity_kind != "meeting":
        return f'"{action_name}" is only valid while a meeting commitment is active'

    if action_name in _TASK_STATE_ACTIONS and active_activity_kind != "work":
        if action_name == "waiting" and not active_task_id:
            return None
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
