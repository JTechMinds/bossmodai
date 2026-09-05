"""BossMod AI — Turn router for direct decisions and execution actions.

Public entrypoint: run_turn. Decision LLM loop lives in decision_turn;
execution LLM loop lives in execution_turn. Shared helpers live in
turn_context and turn_helpers.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.communication import (
    build_communication_snapshot,
    communication_snapshot_json,
)
from core.agent_loop.decision_turn import _is_decision_turn, _run_decision_turn
from core.agent_loop.execution_turn import _run_execution_turn
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.policies import get_trigger_policy
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.turn_context import (
    _COMMUNICATION_TRIGGER_TYPES,
    _contract_kind_for_trigger,
    _determine_mode,
    _get_current_activity,
    _get_current_channel,
    _get_current_session,
    _get_current_task,
    _get_nearby_agents,
    _get_reference_materials,
)
from core.agent_loop.turn_helpers import (
    _cli_result_to_turn_result,
    _finalize_turn,
    _skip_turn,
)
from core.llm import context_builder, routing
from core.models import Agent, AgentState
from core.runtime.events import runtime_events as manager
import db

logger = logging.getLogger(__name__)

# Re-export for existing tests (HA-SEC-P1-01).
__all__ = ["run_turn", "_cli_result_to_turn_result"]


async def run_turn(
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
) -> TurnOutcome:
    """Execute a multi-turn agent activation.

    Loops calling the LLM until the agent produces a terminal action
    (idle/complete/blocked/delegated/abandoned), walks somewhere, or
    the Guardian intervenes. Every exit path refreshes the visible runtime
    status and updates last_active_at.
    """
    start = time.monotonic()
    logger.info("Running turn for %s (trigger: %s)", agent.name, trigger.get("type"))

    trigger_type = trigger.get("type", "unknown")
    policy = get_trigger_policy(trigger_type)

    # 1. Determine activation mode
    mode = _determine_mode(trigger)

    # 2. Select model
    model, model_source = routing.select_model_with_source(agent, mode)
    if model is None:
        return await _skip_turn(agent, trigger, trigger_type, mode, model_source, start)

    api_config = routing.get_api_config(agent)

    # 3. Build initial context
    is_decision_turn = _is_decision_turn(trigger)
    prompt_history = build_prompt_history_view(agent, trigger, token_model=model)
    nearby = _get_nearby_agents(agent.id, state)
    initial_activity = activity_runtime.get_active_activity(agent.id)
    current_task = _get_current_task(agent.id)
    current_activity = _get_current_activity(agent.id)
    current_session = _get_current_session(agent.id, trigger)
    current_channel = _get_current_channel(trigger)
    reference_materials = _get_reference_materials(agent.id)
    pending_count = max(db.count_queued_triggers(agent.id) - 1, 0)
    initial_task_id = activity_runtime.get_active_task_id(agent.id)
    communication_snapshot = None
    if trigger_type in _COMMUNICATION_TRIGGER_TYPES:
        communication_snapshot = build_communication_snapshot(
            agent=agent,
            state=state,
            trigger=trigger,
        )

    contract_kind = _contract_kind_for_trigger(trigger_type)
    turn_context = context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger=trigger,
        conversation_history=prompt_history.conversation_history,
        prompt_notifications=prompt_history.prompt_notifications,
        reference_materials=reference_materials,
        current_activity=current_activity,
        current_session=current_session,
        current_channel=current_channel,
        nearby_agents=nearby,
        current_task=current_task,
        pending_trigger_count=pending_count,
        contract_kind=contract_kind,
        communication_snapshot_json=communication_snapshot_json(communication_snapshot) if communication_snapshot else None,
    )
    context = context_builder.build_context(turn_context)
    initial_context_json = json.dumps(context)

    if is_decision_turn:
        return await _run_decision_turn(
            agent=agent,
            state=state,
            trigger=trigger,
            trigger_type=trigger_type,
            mode=mode,
            model=model,
            model_source=model_source,
            api_config=api_config,
            context=context,
            initial_context_json=initial_context_json,
            initial_task_id=initial_task_id,
            start=start,
        )

    if policy.require_work_activity and not initial_task_id:
        result = {
            "event": "agent_error",
            "detail": f"{agent.name} could not find an active task for {trigger_type}",
            "agent_name": agent.name,
        }
        await manager.broadcast_activity(**result)
        return await _finalize_turn(
            agent=agent,
            trigger=trigger,
            trigger_type=trigger_type,
            mode=mode,
            model=model,
            model_source=model_source,
            initial_context_json=initial_context_json,
            outcome=TurnOutcome.failure(
                result=result,
                    error="Trigger requires active work activity",
                action=None,
                action_summary="",
                raw_response="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            ),
            start=start,
        )

    return await _run_execution_turn(
        agent=agent,
        state=state,
        trigger=trigger,
        trigger_type=trigger_type,
        mode=mode,
        model=model,
        model_source=model_source,
        api_config=api_config,
        context=context,
        initial_context_json=initial_context_json,
        initial_activity=initial_activity,
        policy=policy,
        start=start,
    )
