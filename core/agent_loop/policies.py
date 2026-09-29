"""BossMod AI — Trigger execution and dispatch policy.

Dispatch eligibility (HA-CORR-P1-07) is part of this module so the scheduler
does not invent a second rule table:

- Every trigger waits while the agent is ``in_transit`` (movement). Arrival
  still owns the next turn via ``activity_resumed``.
- ``social`` only runs when the agent is idle with no live activity.
- ``task_assigned`` may run during conversation / meeting / work / assignment
  (same as ``task_follow_up``). It does **not** wait for those activities to
  finish; it still waits for arrival.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TriggerPolicy:
    """Execution and dispatch rules for a trigger type."""

    trigger_type: str
    end_turn_after_direct_reply: bool = False
    require_work_activity: bool = False
    # Dispatch: wait for arrival. Movement sets agent status in_transit.
    blocks_on_in_transit: bool = True
    # Dispatch: require no live activity. Used by social only.
    blocks_on_active_activity: bool = False
    # Execution: run beside any live work without touching it — no frozen
    # transcript is restored or written, the task and activity are treated as
    # unbound (idle is allowed, task-state actions are refused), and idle ends
    # the turn without completing any activity. Used by extension_event.
    # Read only through core.agent_loop.work_binding.is_detached.
    detached_from_work: bool = False


_DEFAULT_POLICY = TriggerPolicy(trigger_type="unknown")

_POLICIES: dict[str, TriggerPolicy] = {
    "human_chat": TriggerPolicy(
        trigger_type="human_chat",
        end_turn_after_direct_reply=True,
    ),
    "peer_message": TriggerPolicy(
        trigger_type="peer_message",
        end_turn_after_direct_reply=True,
    ),
    "meeting_invite": TriggerPolicy(
        trigger_type="meeting_invite",
        end_turn_after_direct_reply=True,
    ),
    "task_follow_up": TriggerPolicy(
        trigger_type="task_follow_up",
        end_turn_after_direct_reply=True,
    ),
    "task_update": TriggerPolicy(
        trigger_type="task_update",
        end_turn_after_direct_reply=True,
    ),
    "session_message": TriggerPolicy(
        trigger_type="session_message",
        end_turn_after_direct_reply=True,
    ),
    "session_response": TriggerPolicy(
        trigger_type="session_response",
        end_turn_after_direct_reply=True,
    ),
    "channel_message": TriggerPolicy(
        trigger_type="channel_message",
        end_turn_after_direct_reply=True,
    ),
    "channel_response": TriggerPolicy(
        trigger_type="channel_response",
        end_turn_after_direct_reply=True,
    ),
    "watchdog_status_ping": TriggerPolicy(
        trigger_type="watchdog_status_ping",
        end_turn_after_direct_reply=True,
        require_work_activity=True,
    ),
    "task_assigned": TriggerPolicy(
        trigger_type="task_assigned",
        # May preempt a live conversation/meeting/work turn; still blocked in transit.
    ),
    "activity_resumed": TriggerPolicy(
        trigger_type="activity_resumed",
    ),
    "extension_event": TriggerPolicy(
        trigger_type="extension_event",
        # Like checking email between steps of a task: the task's transcript
        # and state are untouched, and the dispatcher re-queues it after.
        detached_from_work=True,
    ),
    "social": TriggerPolicy(
        trigger_type="social",
        end_turn_after_direct_reply=True,
        blocks_on_active_activity=True,
    ),
}


def get_trigger_policy(trigger_type: str) -> TriggerPolicy:
    """Return execution policy for a trigger type."""
    return _POLICIES.get(trigger_type, _DEFAULT_POLICY)
