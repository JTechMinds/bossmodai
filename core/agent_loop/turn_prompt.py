"""BossMod AI — Build a turn's prompt, sized around the measured instructions.

Every context budget is a share of the window less the reserved instructions
(``core.context_budget``): the largest instructions any agent's turn
measured. This module is where that measurement happens. A turn builds its
prompt, measures its own instructions (every message ``build_context`` emits
before the history block, except the paused-work view) and records them. If
they are larger than the reserve the prompt was sized with, the agent has
just become the largest (its first turn, or its memory or task grew), so the
prompt is rebuilt once with the reserve that now includes it. No turn is
ever sized without its own instructions.

The instructions never depend on the history (``build_context`` reads
``conversation_history`` only to emit the history block), so a rebuild
measures the same unless something incidental changed in between, such as
the clock line or a notification that just arrived; that is logged, and the
room the shares leave free covers it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import db
from core.agent_loop.prompt_history import PromptHistoryView, build_prompt_history_view
from core.agent_loop.work_snapshot import render_paused_work_for_agent
from core.context_budget import read_context_budget
from core.llm import context_builder
from core.llm.client import count_text_tokens
from core.models import Agent

logger = logging.getLogger(__name__)

__all__ = ["BuiltTurnPrompt", "build_turn_prompt", "measure_instruction_tokens"]


@dataclass(frozen=True, slots=True)
class BuiltTurnPrompt:
    """A turn's prompt, built and measured.

    Attributes:
        context: The messages ``build_context`` produced, ready for the model.
        prompt_history: The history view the prompt was built from.
        instruction_tokens: The tokens of the prompt's instructions, as
            recorded for the agent.
    """

    context: list[dict[str, Any]]
    prompt_history: PromptHistoryView
    instruction_tokens: int


def measure_instruction_tokens(
    context: list[dict[str, Any]],
    *,
    history_count: int,
    paused_work: str | None,
) -> int:
    """Count the instructions of a prompt ``build_context`` built.

    ``build_context`` emits the instructions, then exactly one message per
    history entry, then the message being answered (the trigger), last. So
    the instructions are every message before the last ``history_count + 1``,
    less the paused-work view: it is sized by When you ask, not by the
    reserve, so it is skipped by its content rather than guessed by position.

    Args:
        context: The prompt, as ``build_context`` returned it.
        history_count: How many history entries the prompt was built with
            (``len(view.conversation_history)``).
        paused_work: The paused-work view the prompt was built with, or
            ``None`` (or empty, which ``build_context`` does not render)
            when it has none.

    Returns:
        The instruction tokens, counted with the shared generic tokenizer.

    Raises:
        ValueError: ``history_count`` is negative or the prompt is shorter
            than its history plus the trigger, or ``paused_work`` is given
            but is not one of the instruction messages; either means the
            prompt was not built the way this count assumes.
    """
    if history_count < 0:
        raise ValueError(f"A prompt cannot have {history_count} history entries.")
    end = len(context) - history_count - 1
    if end < 0:
        raise ValueError(
            f"A {len(context)}-message prompt cannot hold {history_count} history entries and the trigger."
        )
    total = 0
    # build_context renders the view only when it is non-empty.
    skipped = not paused_work
    for message in context[:end]:
        content = str(message["content"])
        if not skipped and content == paused_work:
            skipped = True
            continue
        total += count_text_tokens(content)
    if not skipped:
        raise ValueError("The paused-work view is not among the prompt's instruction messages.")
    return total


def _build_and_record(
    agent: Agent,
    trigger: dict[str, Any],
    *,
    token_model: str | None,
    work_turn: bool,
    decision_contract: bool,
    make_turn: Callable[[PromptHistoryView, str | None], context_builder.TurnContext],
) -> tuple[BuiltTurnPrompt, int]:
    """One pass: size the history from the current reserve, build, measure, record.

    Returns:
        The built prompt and the reserve it was sized with.
    """
    budget = read_context_budget()
    view = build_prompt_history_view(
        agent, trigger, token_model=token_model,
        budget_tokens=budget.task_log if work_turn else budget.history,
        work_turn=work_turn,
    )
    paused = render_paused_work_for_agent(agent.id) if decision_contract else None
    context = context_builder.build_context(make_turn(view, paused))
    tokens = measure_instruction_tokens(context, history_count=len(view.conversation_history), paused_work=paused)
    db.record_instruction_size(agent.id, tokens)
    return BuiltTurnPrompt(context=context, prompt_history=view, instruction_tokens=tokens), budget.reserved


def build_turn_prompt(
    agent: Agent,
    trigger: dict[str, Any],
    *,
    token_model: str | None,
    work_turn: bool,
    decision_contract: bool,
    make_turn: Callable[[PromptHistoryView, str | None], context_builder.TurnContext],
) -> BuiltTurnPrompt:
    """Build a turn's prompt with budgets sized around its own measured instructions.

    At most two passes. Each pass reads the context budget (the reserve is
    the largest stored measurement), builds the history view with the turn's
    history budget (Task log on a work turn, else History), renders the
    paused-work view on a decision turn, builds the prompt, measures its
    instructions and records them for the agent. When the instructions fit
    the reserve the pass was sized with, that prompt is the turn's. Otherwise
    the second pass reads the reserve again, which now includes this agent's
    instructions, and rebuilds. If the second pass still measures over its
    reserve (only an incidental change between the passes can do that), a
    warning names the agent and both numbers and its prompt is used: the
    room the shares leave free covers the difference.

    Args:
        agent: The agent whose turn this is.
        trigger: The live trigger.
        token_model: The turn's model, for sticky-slot token counts.
        work_turn: Whether this turn resumes frozen work (its history is
            windowed to Task log instead of History).
        decision_contract: Whether the turn uses the decision contract, the
            only kind that shows the paused-work view.
        make_turn: Builds the turn's ``TurnContext`` from the history view
            and the paused-work view (``None`` when there is none); every
            other input is the caller's.

    Returns:
        The prompt, its history view and its measured instruction tokens.

    Raises:
        core.config.ConfigError: The Context Window settings are invalid, or
            the measured instructions do not fit the context window (the
            message names the agent).
        ValueError: The prompt was not built the way the measurement assumes
            (:func:`measure_instruction_tokens`).
    """
    built, reserved = _build_and_record(
        agent, trigger, token_model=token_model, work_turn=work_turn,
        decision_contract=decision_contract, make_turn=make_turn,
    )
    if built.instruction_tokens <= reserved:
        return built
    rebuilt, reserved_again = _build_and_record(
        agent, trigger, token_model=token_model, work_turn=work_turn,
        decision_contract=decision_contract, make_turn=make_turn,
    )
    if rebuilt.instruction_tokens > reserved_again:
        logger.warning(
            "%s's instructions measured %d tokens on the rebuilt prompt, over the %d-token reserve "
            "it was sized with; the free room in the context window covers the difference",
            agent.name, rebuilt.instruction_tokens, reserved_again,
        )
    return rebuilt
