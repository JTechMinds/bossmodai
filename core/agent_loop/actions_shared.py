"""Shared helpers for execution action handlers.

Mechanical extract from actions.py (HA-STRUCT-P1-02). Used by domain
handler modules so parse/dispatch in actions.py stays import-cycle free.
"""

from __future__ import annotations

from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.deliverables import build_work_contract
from core.llm.client import count_tokens
from core.models.message import HUMAN_SENDER_ID
from core.models import Agent
from core.models.work_contract import WorkContract
import db

# camelCase destination names → internal room IDs
_DESTINATIONS = {
    "desk": None,  # special: resolved to agent's desk_x/desk_y
    "meetingRoom": "meeting_room",
    "breakRoom": "break_room",
    "mainWorkspace": "workspace_main",
    "southWorkspace": "workspace_south",
    "hallway": "hallway_main",
}

_ACTION_PROMPT_ALLOWED_PATHS = {"room_name", "target", "targets"}
_MAX_INLINE_FILE_DELIVERABLE_WORK_CHARS = 2000


def _resolve_agent_by_id(agent_id: Any) -> Agent | None:
    """Resolve an explicit agent ID from an action payload."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    return db.get_agent(agent_id.strip())


def _resolve_task_lifecycle_target(
    agent: Agent,
    action: dict[str, Any],
    *,
    action_name: str,
) -> tuple[str | None, str | None]:
    """Resolve the task targeted by a lifecycle action.

    Task lifecycle actions always act on the currently bound active task.
    """
    active_task_id = activity_runtime.get_active_task_id(agent.id)
    if not active_task_id:
        return None, f'"{action_name}" requires an active task'

    return active_task_id, None


def _build_trigger_request(
    *,
    agent_id: str,
    trigger_type: str,
    source_channel: str,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a normalized trigger request emitted by an action."""
    return {
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "source_channel": source_channel,
        "payload": payload,
        "task_id": task_id,
    }


def _resolve_token_model(agent: Agent, action: dict[str, Any]) -> str | None:
    """Resolve the tokenizer model for action-side token accounting."""
    explicit_model = action.get("_token_model")
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model.strip()
    for field in (
        agent.model_work,
        agent.model_social,
        agent.model_reasoning,
        agent.model_extraction,
        agent.model_self_queue,
    ):
        if field and field.strip():
            return field.strip()
    return config.get("default_model_work")


def _count_action_tokens(agent: Agent, action: dict[str, Any], text: str) -> int:
    """Count tokens for persisted artifacts/messages without heuristic fallback."""
    return count_tokens(text, model=_resolve_token_model(agent, action))


def _task_is_human_visible(task: Any | None) -> bool:
    """Return whether task lifecycle notifications should reach the human chat."""
    return bool(
        task
        and task.requester_id == HUMAN_SENDER_ID
        and (task.notification_policy or "none") != "none"
    )


def _normalize_delegate_work_contract(
    *,
    agent: Agent,
    action: dict[str, Any],
) -> WorkContract | None:
    """Normalize any delegateTask deliverables against the delegator's CLI cwd."""
    deliverables = action.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        return None
    cli_state = db.ensure_agent_cli_state(agent.id)
    return build_work_contract(
        deliverables,
        agent_storage_key=agent.storage_key,
        cwd=cli_state.cwd,
    )
