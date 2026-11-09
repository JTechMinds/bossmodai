"""core/agent_loop/turn_prompt.py: a turn's prompt, sized around its own measured instructions.

The measurement is arithmetic on a real ``build_context`` prompt: everything
before the history block and the trigger, less the paused-work view. The
build takes one pass when the instructions fit the reserve, and two when the
agent has just become the largest (its first turn on an empty table). The
clock line is pinned so two builds of the same turn measure the same.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop import turn_prompt as turn_prompt_module
from core.agent_loop.prompt_history import PromptHistoryView
from core.agent_loop.turn_context import _get_current_activity, _get_current_task
from core.agent_loop.turn_prompt import build_turn_prompt, measure_instruction_tokens
from core.agent_loop.work_snapshot import freeze_work_turn, render_paused_work_for_agent
from core.context_budget import read_context_budget
from core.llm import context_builder
from core.llm.client import count_text_tokens
from core.models.message import HUMAN_SENDER_ID
from core.tasking import create_or_bind_task
from tests._connections import model_connection

_CHAT = {"type": "human_chat", "content": "Status?", "from_name": "Human Operator", "source_channel": "chat"}
_HISTORY = [
    {"from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "Earlier ask, number one"},
    {"from_agent": "agent-self", "from_name": "Charles", "content": "Earlier reply"},
    {"from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "Earlier ask, number two"},
]


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the prompt's clock line, so a rebuild measures exactly what the first build did."""
    pinned = datetime(2026, 11, 9, 10, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("core.llm.context_builder.now_local", lambda: pinned)


def _agent(name: str = "Charles"):
    return db.create_agent(name, role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))


def _working_agent_with_paused_work():
    """An agent on a task whose frozen steps give it a paused-work view."""
    agent = _agent()
    task = create_or_bind_task(
        title="Execute the 9 fixes", description="Apply the review fixes.", project=None,
        assigned_to=agent.id, requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID,
        parent_task_id=None, work_contract=None, source_channel=None, notification_policy=None,
        notification_channel_id=None, audit_author_name="Human Operator", audit_author_type="human",
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    assert activity is not None
    steps = [
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"status"},"th":"check"}'},
        {"role": "user", "content": "status: all green"},
    ]
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    paused = render_paused_work_for_agent(agent.id)
    assert paused is not None
    return agent, paused


def _context(agent, *, kind: str, history: list[dict[str, Any]], paused: str | None) -> list[dict[str, Any]]:
    turn = context_builder.TurnContext(
        agent=agent,
        state=db.get_agent_state(agent.id),
        trigger=_CHAT if kind == "decision" else {"type": "activity_resumed", "content": "Resume work."},
        conversation_history=history,
        prompt_notifications=[],
        reference_materials=[],
        current_activity=_get_current_activity(agent.id),
        current_task=_get_current_task(agent.id),
        contract_kind=kind,
        paused_work_view=paused,
    )
    return context_builder.build_context(turn)


def _tokens(messages: list[dict[str, Any]]) -> int:
    return sum(count_text_tokens(str(message["content"])) for message in messages)


# ─── measure_instruction_tokens ───


def test_a_decision_prompt_counts_every_instruction_and_skips_history_trigger_and_paused_work() -> None:
    agent, paused = _working_agent_with_paused_work()
    full = _context(agent, kind="decision", history=_HISTORY, paused=paused)
    bare = _context(agent, kind="decision", history=[], paused=None)

    measured = measure_instruction_tokens(full, history_count=len(_HISTORY), paused_work=paused)

    # The instructions are the bare prompt less its trigger: history and the view add nothing to them.
    assert measured == _tokens(bare[:-1])
    assert measured == measure_instruction_tokens(bare, history_count=0, paused_work=None)
    # Every message is either an instruction or one of the three things left out.
    history_block = full[-len(_HISTORY) - 1:-1]
    assert [message["content"] for message in history_block] == [
        "[Human Operator]: Earlier ask, number one",
        "[Charles]: Earlier reply",
        "[Human Operator]: Earlier ask, number two",
    ]
    assert _tokens(full) == measured + count_text_tokens(paused) + _tokens(history_block) + _tokens(full[-1:])
    assert len(bare) >= 4, "the system prompt, turn contract and decision blocks are all counted"


@pytest.mark.parametrize("history", [[], _HISTORY], ids=["no-history", "history"])
def test_an_execution_prompt_counts_everything_before_its_history(history: list[dict[str, Any]]) -> None:
    agent = _agent()
    context = _context(agent, kind="execution", history=history, paused=None)

    measured = measure_instruction_tokens(context, history_count=len(history), paused_work=None)

    assert measured == _tokens(context[: len(context) - len(history) - 1])
    assert measured == measure_instruction_tokens(
        _context(agent, kind="execution", history=[], paused=None), history_count=0, paused_work=None,
    )


def test_a_measurement_that_does_not_match_the_prompt_is_refused() -> None:
    agent, paused = _working_agent_with_paused_work()
    execution = _context(agent, kind="execution", history=[], paused=paused)
    with pytest.raises(ValueError, match="not among the prompt's instruction messages"):
        # An execution prompt never shows the view, so measuring as if it did is a bug, not a 0.
        measure_instruction_tokens(execution, history_count=0, paused_work=paused)
    with pytest.raises(ValueError, match="cannot hold"):
        measure_instruction_tokens(execution, history_count=len(execution), paused_work=None)
    with pytest.raises(ValueError, match="cannot have -1 history entries"):
        measure_instruction_tokens(execution, history_count=-1, paused_work=None)


# ─── build_turn_prompt ───


def _make_turn(agent, *, grow_on_call: int | None = None):
    """The loop's make_turn: a TurnContext from the history view and the paused-work view.

    ``grow_on_call`` adds a reference on that call, an incidental change that
    makes the instructions larger than the pass before.
    """
    calls = {"count": 0}

    def make_turn(view: PromptHistoryView, paused_work: str | None) -> context_builder.TurnContext:
        calls["count"] += 1
        references = ["A long reference that arrived between passes. " * 40] if calls["count"] == grow_on_call else []
        return context_builder.TurnContext(
            agent=agent,
            state=db.get_agent_state(agent.id),
            trigger=_CHAT,
            conversation_history=view.conversation_history,
            prompt_notifications=view.prompt_notifications,
            reference_materials=references,
            current_activity=_get_current_activity(agent.id),
            current_task=_get_current_task(agent.id),
            contract_kind="decision",
            paused_work_view=paused_work,
        )

    return make_turn


def _spy_history_budgets(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The history budget of every pass, in order."""
    real = turn_prompt_module.build_prompt_history_view
    budgets: list[int] = []

    def _spy(agent, trigger, **kwargs):
        budgets.append(kwargs["budget_tokens"])
        return real(agent, trigger, **kwargs)

    monkeypatch.setattr(turn_prompt_module, "build_prompt_history_view", _spy)
    return budgets


def _build(agent, **kwargs: Any):
    return build_turn_prompt(
        agent, _CHAT, token_model="test/mock", work_turn=False, decision_contract=True,
        make_turn=kwargs.pop("make_turn", _make_turn(agent)), **kwargs,
    )


def _recorded() -> list[dict[str, Any]]:
    return db.query("SELECT agent_id, tokens FROM agent_instruction_sizes ORDER BY agent_id")


def test_the_first_turn_on_an_empty_table_measures_then_rebuilds_with_its_own_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    for index in range(3):
        db.create_message(HUMAN_SENDER_ID, agent.id, f"Ask {index}", message_type="human")
    budgets = _spy_history_budgets(monkeypatch)
    assert read_context_budget().reserved == 0, "nothing measured yet"

    built = _build(agent)

    measured = built.instruction_tokens
    assert measured > 0
    assert _recorded() == [{"agent_id": agent.id, "tokens": measured}], "one size, recorded for this agent"
    window, share = config.require_int("context_window_tokens"), config.require_int("context_history_percent")
    # Pass 1 had no reserve; pass 2 is sized with this agent's own instructions.
    assert budgets == [window * share // 100, (window - measured) * share // 100]
    assert built.prompt_history.conversation_history, "the DM history is in the prompt"
    assert measure_instruction_tokens(
        built.context, history_count=len(built.prompt_history.conversation_history), paused_work=None,
    ) == measured


def test_the_next_turn_fits_its_reserve_and_takes_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    first = _build(agent)
    budgets = _spy_history_budgets(monkeypatch)

    second = _build(agent)

    window, share = config.require_int("context_window_tokens"), config.require_int("context_history_percent")
    assert budgets == [(window - first.instruction_tokens) * share // 100]
    assert second.instruction_tokens == first.instruction_tokens
    assert _recorded() == [{"agent_id": agent.id, "tokens": second.instruction_tokens}]


def test_the_reserve_is_the_largest_agent_and_a_smaller_one_takes_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    larger = _agent("Eng-A")
    db.record_instruction_size(larger.id, 30_000)
    budgets = _spy_history_budgets(monkeypatch)

    built = _build(agent)

    window, share = config.require_int("context_window_tokens"), config.require_int("context_history_percent")
    assert built.instruction_tokens < 30_000
    assert budgets == [(window - 30_000) * share // 100], "sized with the largest agent's instructions, once"
    largest = db.largest_instruction_size()
    assert largest is not None and (largest.agent_name, largest.tokens) == ("Eng-A", 30_000)
    assert {row["tokens"] for row in _recorded()} == {30_000, built.instruction_tokens}


def test_a_decision_turn_passes_its_paused_work_and_leaves_it_out_of_the_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, paused = _working_agent_with_paused_work()

    built = _build(agent)

    assert sum(1 for message in built.context if message["content"] == paused) == 1
    history_count = len(built.prompt_history.conversation_history)
    assert built.instruction_tokens == measure_instruction_tokens(
        built.context, history_count=history_count, paused_work=paused,
    )
    instructions = built.context[: len(built.context) - history_count - 1]
    assert built.instruction_tokens == _tokens(instructions) - count_text_tokens(paused)


def test_a_rebuild_that_still_measures_over_warns_and_is_used(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _agent()
    budgets = _spy_history_budgets(monkeypatch)
    real_record = db.record_instruction_size
    recorded: list[int] = []

    def _record(agent_id: str, tokens: int) -> None:
        recorded.append(tokens)
        real_record(agent_id, tokens)

    monkeypatch.setattr(db, "record_instruction_size", _record)

    with caplog.at_level(logging.WARNING, logger="core.agent_loop.turn_prompt"):
        built = _build(agent, make_turn=_make_turn(agent, grow_on_call=2))

    assert len(budgets) == 2, "two passes at most: never a third"
    first, second = recorded
    assert second > first and built.instruction_tokens == second
    (record,) = [item for item in caplog.records if item.name == "core.agent_loop.turn_prompt"]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        f"Charles's instructions measured {second} tokens on the rebuilt prompt, over the {first}-token reserve "
        "it was sized with; the free room in the context window covers the difference"
    )
    assert "A long reference that arrived between passes." in built.context[0]["content"], "pass 2's prompt is used"
    assert _recorded() == [{"agent_id": agent.id, "tokens": second}]
