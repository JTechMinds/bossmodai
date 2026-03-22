"""BossMod AI — Trigger execution policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TriggerPolicy:
    """Execution rules for a trigger type."""

    trigger_type: str
    activation_status: str = "work_active"
    preserve_active_task: bool = False
    bind_trigger_task: bool = False
    require_task_context: bool = False
    end_turn_after_direct_reply: bool = False
    auto_resume_previous_task: bool = False
    allow_idle_with_active_task: bool = False


_DEFAULT_POLICY = TriggerPolicy(trigger_type="unknown")

_POLICIES: dict[str, TriggerPolicy] = {
    "human_chat": TriggerPolicy(
        trigger_type="human_chat",
        preserve_active_task=True,
        end_turn_after_direct_reply=True,
        auto_resume_previous_task=True,
    ),
    "peer_message": TriggerPolicy(
        trigger_type="peer_message",
        preserve_active_task=True,
        end_turn_after_direct_reply=True,
        auto_resume_previous_task=True,
    ),
    "watchdog_status_ping": TriggerPolicy(
        trigger_type="watchdog_status_ping",
        preserve_active_task=True,
        require_task_context=True,
        end_turn_after_direct_reply=True,
        auto_resume_previous_task=True,
    ),
    "task_assigned": TriggerPolicy(
        trigger_type="task_assigned",
        bind_trigger_task=True,
        require_task_context=True,
    ),
    "task_resumed": TriggerPolicy(
        trigger_type="task_resumed",
        bind_trigger_task=True,
        require_task_context=True,
    ),
    "task_attention_required": TriggerPolicy(
        trigger_type="task_attention_required",
        bind_trigger_task=True,
        require_task_context=True,
        end_turn_after_direct_reply=True,
    ),
    "social": TriggerPolicy(
        trigger_type="social",
        activation_status="social_active",
        end_turn_after_direct_reply=True,
        allow_idle_with_active_task=True,
    ),
}


def get_trigger_policy(trigger_type: str) -> TriggerPolicy:
    """Return execution policy for a trigger type."""
    return _POLICIES.get(trigger_type, _DEFAULT_POLICY)
