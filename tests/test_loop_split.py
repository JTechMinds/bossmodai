"""HA-STRUCT-P1-03 — loop.py / decision_runtime.py split keeps public APIs."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_loop import decision_runtime, loop
from core.agent_loop.decision_runtime import apply_decision, summarize_decision
from core.agent_loop.decision_turn import _is_decision_turn, _run_decision_turn
from core.agent_loop.execution_turn import _run_execution_turn
from core.agent_loop.loop import _cli_result_to_turn_result, run_turn
from core.agent_loop.turn_context import (
    _COMMUNICATION_TRIGGER_TYPES,
    _DECISION_TRIGGER_TYPES,
    _contract_kind_for_trigger,
    _determine_mode,
)
from core.agent_loop.turn_helpers import (
    _build_decision_repair_messages,
    _build_execution_repair_messages,
    _cli_result_to_turn_result as helpers_cli_result,
    _summarize_action_chain,
)


def test_public_exports_still_import_from_loop_and_decision_runtime() -> None:
    assert callable(run_turn)
    assert callable(apply_decision)
    assert callable(summarize_decision)
    assert run_turn is loop.run_turn
    assert apply_decision is decision_runtime.apply_decision
    assert _cli_result_to_turn_result is helpers_cli_result


def test_decision_and_execution_loops_live_outside_router() -> None:
    loop_source = open(loop.__file__, encoding="utf-8").read()
    assert "async def run_turn" in loop_source
    assert "async def _run_decision_turn" not in loop_source
    assert "async def _run_execution_turn" not in loop_source
    assert "def apply_decision" not in loop_source
    assert callable(_run_decision_turn)
    assert callable(_run_execution_turn)
    assert _run_decision_turn.__module__ == "core.agent_loop.decision_turn"
    assert _run_execution_turn.__module__ == "core.agent_loop.execution_turn"


def test_apply_decision_collaborators_live_in_focused_modules() -> None:
    runtime_source = open(decision_runtime.__file__, encoding="utf-8").read()
    assert "def apply_decision" in runtime_source
    assert "def summarize_decision" in runtime_source
    assert "def _resolve_work_execution_plan" not in runtime_source
    assert "def _persist_reply" not in runtime_source
    assert "def _resolve_or_create_work_task" not in runtime_source
    assert "def _resume_previous_work_if_needed" not in runtime_source

    from core.agent_loop import (
        decision_replies,
        decision_resume,
        decision_task_bind,
        decision_work_plan,
    )

    assert callable(decision_work_plan._resolve_work_execution_plan)
    assert callable(decision_work_plan._materialize_work_execution_plan)
    assert callable(decision_task_bind._resolve_or_create_work_task)
    assert callable(decision_task_bind._ensure_deferred_task)
    assert callable(decision_replies._persist_reply)
    assert callable(decision_replies._prepare_shared_response_trigger)
    assert callable(decision_resume._resume_previous_work_if_needed)
    assert callable(decision_resume._complete_assignment_if_present)


def test_trigger_classification_unchanged() -> None:
    assert "human_chat" in _DECISION_TRIGGER_TYPES
    assert "task_assigned" in _DECISION_TRIGGER_TYPES
    assert "task_assigned" not in _COMMUNICATION_TRIGGER_TYPES
    assert _is_decision_turn({"type": "human_chat"}) is True
    assert _is_decision_turn({"type": "cli_approval_resolved"}) is False
    assert _is_decision_turn({"type": "host_path_consent_resolved"}) is False
    assert _is_decision_turn({"type": "activity_resumed"}) is False
    assert _contract_kind_for_trigger("human_chat") == "decision"
    assert _contract_kind_for_trigger("activity_resumed") == "execution"
    assert _determine_mode({"type": "social"}) == "social"
    assert _determine_mode({"type": "human_chat"}) == "work"


def test_summarize_decision_label() -> None:
    label = summarize_decision(
        {
            "decision": "observe",
            "intentKind": "other",
            "commitmentKind": "none",
            "thought": "nothing to do",
        }
    )
    assert label == "observe(none)"


def test_repair_builders_keep_roles_and_error() -> None:
    decision_msgs = _build_decision_repair_messages(parsed_error="not json")
    assert len(decision_msgs) == 3
    assert all(msg["role"] == "system" for msg in decision_msgs)
    assert "not json" in decision_msgs[0]["content"]

    execution_msgs = _build_execution_repair_messages(parsed_error="bad act")
    assert len(execution_msgs) == 3
    assert execution_msgs[0]["role"] == "system"
    assert execution_msgs[-1]["role"] == "user"
    assert "bad act" in execution_msgs[0]["content"]


def test_cli_result_helper_still_imported_from_loop() -> None:
    cli_result = SimpleNamespace(
        ok=True,
        detail="read notes.md",
        prompt_content="BOSSMOD CLI RESULT\ncommand: cat notes.md\n\nSTDOUT:\nok",
        data={},
    )
    result = _cli_result_to_turn_result(SimpleNamespace(name="Ada"), cli_result)
    assert result["event"] == "bm_cli_result"
    assert result["cli_prompt_content"] == cli_result.prompt_content
    assert result["suppress_world_broadcast"] is True


def test_summarize_action_chain_truncates() -> None:
    assert _summarize_action_chain([], "idle") == "idle"
    assert _summarize_action_chain(["work", "bm_cli"], "") == "work -> bm_cli"
    assert _summarize_action_chain(["a", "b", "c", "d", "e"], "") == "a -> b -> c -> e"


def test_router_and_runtime_modules_stay_focused() -> None:
    loop_lines = open(loop.__file__, encoding="utf-8").read().count("\n")
    runtime_lines = open(decision_runtime.__file__, encoding="utf-8").read().count("\n")
    assert loop_lines < 250
    assert runtime_lines < 450
    assert "return await _run_decision_turn" in open(loop.__file__, encoding="utf-8").read()
    assert "return await _run_execution_turn" in open(loop.__file__, encoding="utf-8").read()
