"""BossMod AI — What a task's work has used (the ``task_usage`` side table).

One row per task that has had a work turn, holding a ``TaskUsage``: running
totals over its work turns, the latest and largest prompt of one model call,
and its step-summary folds. Each write is one atomic upsert that adds to the
row, so a row is created by whichever write comes first (a fold runs before
its turn ends). Never read through the task join in ``db/tasks.py``: only the
task detail's endpoint reads it, so task lists and the board never pay for
it. What counts and when it is recorded live in
``core/agent_loop/task_usage.py``; this layer stores what it is given.
"""

from __future__ import annotations

from datetime import datetime

from core.models.task import TaskUsage
from db.crud import execute, query_one


def add_task_work_turn(
    task_id: str,
    *,
    model_calls: int,
    prompt_tokens: int,
    completion_tokens: int,
    latest_prompt_tokens: int | None,
    peak_prompt_tokens: int | None,
    at: datetime,
) -> None:
    """Add one finished work turn to a task's usage, creating the row if it has none.

    The counts are added and ``work_turns`` goes up by one. A turn with a
    model call sets the latest prompt and ``latest_call_at = at``; a turn
    without one (``latest_prompt_tokens`` is ``None``) keeps the row's
    latest. The peak only grows.

    Args:
        task_id: The task whose work the turn resumed.
        model_calls: The turn's agent calls; the fold and the managed
            writer's sub-calls are left out.
        prompt_tokens: The turn's prompt tokens, the fold's and the
            sub-calls' included.
        completion_tokens: The turn's completion tokens, the fold's and the
            sub-calls' included.
        latest_prompt_tokens: The context (prompt) of the turn's last agent
            call; ``None`` when it made none.
        peak_prompt_tokens: The largest context of the turn's agent calls;
            ``None`` when it made none.
        at: When the turn ended (UTC).

    Raises:
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    latest_call_at = at if latest_prompt_tokens is not None else None
    execute(
        """
        INSERT INTO task_usage (
            task_id, work_turns, model_calls, prompt_tokens, completion_tokens,
            latest_prompt_tokens, latest_call_at, peak_prompt_tokens, updated_at
        )
        VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(task_id) DO UPDATE SET
            work_turns = task_usage.work_turns + 1,
            model_calls = task_usage.model_calls + excluded.model_calls,
            prompt_tokens = task_usage.prompt_tokens + excluded.prompt_tokens,
            completion_tokens = task_usage.completion_tokens + excluded.completion_tokens,
            latest_prompt_tokens = COALESCE(excluded.latest_prompt_tokens, task_usage.latest_prompt_tokens),
            latest_call_at = CASE
                WHEN excluded.latest_prompt_tokens IS NULL THEN task_usage.latest_call_at
                ELSE excluded.latest_call_at
            END,
            peak_prompt_tokens = CASE
                WHEN excluded.peak_prompt_tokens IS NULL THEN task_usage.peak_prompt_tokens
                ELSE MAX(COALESCE(task_usage.peak_prompt_tokens, 0), excluded.peak_prompt_tokens)
            END,
            updated_at = excluded.updated_at
        """,
        [
            task_id, model_calls, prompt_tokens, completion_tokens,
            latest_prompt_tokens, latest_call_at, peak_prompt_tokens, at,
        ],
    )


def add_task_work_summary(task_id: str, *, summarized_steps: int, at: datetime) -> None:
    """Add one successful step-summary fold to a task's usage, creating the row if it has none.

    Args:
        task_id: The task whose saved work was folded.
        summarized_steps: Steps this fold folded into the step summary.
        at: When the fold finished (UTC).

    Raises:
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    execute(
        """
        INSERT INTO task_usage (task_id, summaries, summarized_steps, last_summary_at, updated_at)
        VALUES ($1, 1, $2, $3, $3)
        ON CONFLICT(task_id) DO UPDATE SET
            summaries = task_usage.summaries + 1,
            summarized_steps = task_usage.summarized_steps + excluded.summarized_steps,
            last_summary_at = excluded.last_summary_at,
            updated_at = excluded.updated_at
        """,
        [task_id, summarized_steps, at],
    )


def add_task_work_summary_failure(task_id: str, *, at: datetime) -> None:
    """Add one failed step-summary fold to a task's usage, creating the row if it has none.

    Args:
        task_id: The task whose saved work could not be folded.
        at: When the fold failed (UTC).

    Raises:
        sqlite3.IntegrityError: ``task_id`` names no task.
    """
    execute(
        """
        INSERT INTO task_usage (task_id, failed_summaries, last_failed_summary_at, updated_at)
        VALUES ($1, 1, $2, $2)
        ON CONFLICT(task_id) DO UPDATE SET
            failed_summaries = task_usage.failed_summaries + 1,
            last_failed_summary_at = excluded.last_failed_summary_at,
            updated_at = excluded.updated_at
        """,
        [task_id, at],
    )


def get_task_usage(task_id: str) -> TaskUsage | None:
    """Return what a task's work has used, or ``None`` when it has had no work turn or fold.

    Args:
        task_id: The task whose usage is read.

    Returns:
        The stored usage, or ``None`` when the task has no row.

    Raises:
        pydantic.ValidationError: The stored row is malformed (a negative
            count). It is never skipped: a bad row is a fault to fix, not an
            absent one.
    """
    row = query_one(
        """
        SELECT task_id, work_turns, model_calls, prompt_tokens, completion_tokens,
               latest_prompt_tokens, latest_call_at, peak_prompt_tokens,
               summaries, summarized_steps, last_summary_at,
               failed_summaries, last_failed_summary_at
        FROM task_usage WHERE task_id = $1
        """,
        [task_id],
    )
    if row is None:
        return None
    return TaskUsage.model_validate(row)
