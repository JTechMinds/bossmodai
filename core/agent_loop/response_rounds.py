"""BossMod AI — Shared meeting/channel response-round coordination.

Observe / reserve / complete is one implementation. Meeting and channel
façades bind parent key, copy, trigger type, and DB helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.models import Agent


@dataclass(frozen=True, slots=True)
class SharedRoundBinding:
    """Names and persistence hooks for one shared-response queue family."""

    parent_key: str
    label: str
    queue_fail_label: str
    trigger_type: str
    source_channel: str
    extra_payload_key: str
    mark_observed: Callable[..., Any]
    mark_responded: Callable[..., Any]
    reserve: Callable[..., Any]
    activate_next: Callable[..., Any]
    maybe_complete: Callable[..., Any]


def observe_shared_message(
    agent: Agent,
    trigger: dict[str, Any],
    binding: SharedRoundBinding,
) -> dict[str, Any]:
    """Mark one shared message as observed without replying."""
    round_id = str(trigger.get("round_id") or "").strip()
    parent_id = str(trigger.get(binding.parent_key) or "").strip()
    if not round_id or not parent_id:
        return {
            "event": "agent_error",
            "detail": f"{agent.name} could not evaluate the shared {binding.label} message",
            "agent_name": agent.name,
            "trigger_requests": [],
        }

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} reviewed the shared {binding.label} message",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    binding.mark_observed(round_id=round_id, agent_id=agent.id)
    binding.maybe_complete(round_id)
    result["detail"] = f"{agent.name} chose to observe the shared {binding.label}"
    return result


def begin_shared_response(
    agent: Agent,
    trigger: dict[str, Any],
    binding: SharedRoundBinding,
) -> tuple[dict[str, Any], bool]:
    """Queue one shared responder and report whether they can speak now."""
    round_id = str(trigger.get("round_id") or "").strip()
    parent_id = str(trigger.get(binding.parent_key) or "").strip()
    if not round_id or not parent_id:
        return (
            {
                "event": "agent_error",
                "detail": f"{agent.name} could not evaluate the shared {binding.label} message",
                "agent_name": agent.name,
                "trigger_requests": [],
            },
            False,
        )

    result = {
        "event": "decision_applied",
        "detail": f"{agent.name} reviewed the shared {binding.label} message",
        "agent_name": agent.name,
        "trigger_requests": [],
    }

    queued = binding.reserve(round_id=round_id, agent_id=agent.id)
    if queued is None:
        result["detail"] = f"{agent.name} could not join the {binding.queue_fail_label}"
        return result, False

    responding = binding.activate_next(round_id)
    if responding and responding.agent_id == agent.id:
        result["detail"] = f"{agent.name} joined the shared reply queue and is up next"
        return result, True
    result["detail"] = f"{agent.name} joined the shared reply queue"
    return result, False


def finalize_shared_response(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
    binding: SharedRoundBinding,
) -> list[dict[str, Any]]:
    """Advance the response queue after one shared reply turn ends."""
    round_id = str(trigger.get("round_id") or "").strip()
    if not round_id:
        return []

    if responded:
        binding.mark_responded(round_id=round_id, agent_id=agent_id)
    else:
        binding.mark_observed(round_id=round_id, agent_id=agent_id)

    next_candidate = binding.activate_next(round_id)
    binding.maybe_complete(round_id)
    if next_candidate is None:
        return []
    return [
        _build_shared_response_trigger(
            agent_id=next_candidate.agent_id,
            trigger=trigger,
            binding=binding,
        )
    ]


def _build_shared_response_trigger(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    binding: SharedRoundBinding,
) -> dict[str, Any]:
    """Build the durable trigger for the next queued shared responder."""
    payload = {
        "content": trigger.get("content", ""),
        binding.parent_key: trigger.get(binding.parent_key),
        "round_id": trigger.get("round_id"),
        "from_name": trigger.get("from_name", "Human Operator"),
        "author_type": trigger.get("author_type", "human"),
        "from_agent": trigger.get("from_agent"),
        "source_message_id": trigger.get("source_message_id"),
        binding.extra_payload_key: trigger.get(binding.extra_payload_key),
    }
    return {
        "agent_id": agent_id,
        "trigger_type": binding.trigger_type,
        "source_channel": binding.source_channel,
        "payload": payload,
    }
