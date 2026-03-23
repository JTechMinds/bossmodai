"""BossMod AI — Trigger execution policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TriggerPolicy:
    """Execution rules for a trigger type."""

    trigger_type: str
    end_turn_after_direct_reply: bool = False
    require_work_activity: bool = False
    allow_idle_with_active_work: bool = False


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
    "watchdog_status_ping": TriggerPolicy(
        trigger_type="watchdog_status_ping",
        end_turn_after_direct_reply=True,
        require_work_activity=True,
    ),
    "task_assigned": TriggerPolicy(
        trigger_type="task_assigned",
    ),
    "activity_resumed": TriggerPolicy(
        trigger_type="activity_resumed",
    ),
    "social": TriggerPolicy(
        trigger_type="social",
        end_turn_after_direct_reply=True,
        allow_idle_with_active_work=True,
    ),
}


def get_trigger_policy(trigger_type: str) -> TriggerPolicy:
    """Return execution policy for a trigger type."""
    return _POLICIES.get(trigger_type, _DEFAULT_POLICY)
