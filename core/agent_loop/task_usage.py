"""Record what a task's work uses: model tokens, context size, and mid-task summaries.

What counts: the task's work turns only, the turns that resume its work
activity (``loop.py`` decides that once, at the turn's start, and passes the
task down as ``work_task_id``), including the step-summary fold that runs
before a resumed turn's first step. Chat and decision turns about the task
(the accept, the operator messaging the agent mid-task, ``task_follow_up``
and ``task_update``) run on chat-mode context, not on the task's working
context, so counting them would blur what the readout is for: tuning the
Context Window settings. A subtask has its own work turns.

The context size is the prompt of one of the agent's own calls: a step
trace's ``context_prompt_tokens``. A step's ``prompt_tokens`` also counts the
managed writer's sub-calls, which send their own, larger prompts, so it is
not the agent's context. The fold's own trace
(``work_summary.SUMMARY_STEP_ACTION``) is a separate call whose input is the
folded steps, so it is not a model call of the work and not a context size;
a trace with no agent call (a failed call) has a context of 0. The turn's
token totals still come from the outcome: the fold and the sub-calls
included.

Where each fact is recorded:

- a turn's totals and its calls' context sizes: :func:`record_work_turn`,
  from ``turn_helpers._finalize_turn``, before the ``diagnostic`` broadcast
  the task detail refreshes on;
- a fold's success or failure, and the steps it folded:
  :func:`record_work_summary` and :func:`record_work_summary_failure`, from
  ``turn_helpers.summarize_before_restore``, which knows the outcome.

The numbers are as the provider reported them, or litellm's estimate where
it reported none. Database errors propagate, as ``db.create_diagnostic``'s
do on the same database just before.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.work_summary import SUMMARY_STEP_ACTION


@dataclass(frozen=True, slots=True)
class WorkTurnUsage:
    """The model calls of one work turn, read from its step traces.

    Attributes:
        model_calls: Traces whose agent call completed, the fold's left out.
        latest_prompt_tokens: The last such call's context
            (``context_prompt_tokens``); ``None`` when there was none.
        peak_prompt_tokens: The largest such call's context; ``None`` when
            there was none.
    """

    model_calls: int
    latest_prompt_tokens: int | None
    peak_prompt_tokens: int | None


def work_turn_usage(steps: Sequence[Mapping[str, Any]]) -> WorkTurnUsage:
    """Read one work turn's model calls and their context sizes from its step traces.

    A model call is a trace whose ``action_name`` is not
    ``SUMMARY_STEP_ACTION`` and whose ``context_prompt_tokens`` (the agent's
    own call, a managed write's sub-calls left out) is above 0.

    Args:
        steps: The turn's step traces (``turn_helpers._build_step_trace``),
            in order.

    Returns:
        The count of calls, the last call's context and the largest; both
        are ``None`` when there was no call.

    Raises:
        KeyError: A trace lacks ``action_name`` or ``context_prompt_tokens``.
    """
    prompts = [
        step["context_prompt_tokens"]
        for step in steps
        if step["action_name"] != SUMMARY_STEP_ACTION and step["context_prompt_tokens"] > 0
    ]
    if not prompts:
        return WorkTurnUsage(model_calls=0, latest_prompt_tokens=None, peak_prompt_tokens=None)
    return WorkTurnUsage(model_calls=len(prompts), latest_prompt_tokens=prompts[-1], peak_prompt_tokens=max(prompts))


def record_work_turn(task_id: str, outcome: TurnOutcome) -> None:
    """Add one finished work turn to its task's usage.

    The turn's totals are ``outcome.prompt_tokens`` and
    ``outcome.completion_tokens``, which include the fold's and the managed
    writer's sub-calls; the calls and their context sizes come from
    :func:`work_turn_usage` over ``outcome.steps``.

    Args:
        task_id: The task whose work the turn resumed, as ``loop.py``
            decided at the turn's start.
        outcome: The turn's outcome, as ``_finalize_turn`` stores it.

    Raises:
        KeyError: A step trace lacks ``action_name`` or
            ``context_prompt_tokens``.
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    usage = work_turn_usage(outcome.steps)
    db.add_task_work_turn(
        task_id,
        model_calls=usage.model_calls,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        latest_prompt_tokens=usage.latest_prompt_tokens,
        peak_prompt_tokens=usage.peak_prompt_tokens,
        at=datetime.now(timezone.utc),
    )


def record_work_summary(task_id: str, *, summarized_steps: int) -> None:
    """Add one successful fold of the task's saved work, now.

    Args:
        task_id: The task whose saved work was folded.
        summarized_steps: Steps the fold folded in (``StepSummaryRun.summarized_now``).

    Raises:
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    db.add_task_work_summary(task_id, summarized_steps=summarized_steps, at=datetime.now(timezone.utc))


def record_work_summary_failure(task_id: str) -> None:
    """Add one failed fold of the task's saved work, now.

    Args:
        task_id: The task whose saved work could not be folded.

    Raises:
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    db.add_task_work_summary_failure(task_id, at=datetime.now(timezone.utc))
