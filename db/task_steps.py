"""BossMod AI — Task step CRUD: one row per execution step an agent takes on a task.

A step is written when it starts and finished once when it ends
(``core.agent_loop.task_steps``). Steps never write ``task_events``,
``status_note`` or ``activity_log``, and nothing that builds an agent's
prompt reads them: they are the operator's record of what was done.
"""

from __future__ import annotations

from datetime import datetime
from typing import get_args

from core.models import TaskStep, TaskStepOutcome
from db.crud import fetch_all, fetch_one, insert_returning

_TASK_STEP_COLUMNS = (
    "id, task_id, agent_id, agent_name, action, note, command, "
    "outcome, result, started_at, ended_at"
)

_OUTCOMES: frozenset[str] = frozenset(get_args(TaskStepOutcome))


def create_task_step(
    *,
    task_id: str,
    agent_id: str,
    agent_name: str,
    action: str,
    note: str | None,
    command: str | None,
    started_at: datetime,
) -> TaskStep:
    """Insert an open step: one the agent has started and not yet finished.

    Args:
        task_id: The task the step works on. Must exist.
        agent_id: The agent taking the step. Must exist.
        agent_name: The agent's name now, kept as written even after a rename.
        action: The runtime action name, verbatim (``bm_cli``, ``message``…).
        note: The agent's own note for the step, or ``None``.
        command: The CLI command for a ``bm_cli`` step, else ``None``.
        started_at: When the step started (timezone-aware).

    Returns:
        The stored step, with ``outcome``, ``result`` and ``ended_at`` unset.

    Raises:
        sqlite3.IntegrityError: The task or agent does not exist.
    """
    return insert_returning(
        f"""
        INSERT INTO task_steps (task_id, agent_id, agent_name, action, note, command, started_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_TASK_STEP_COLUMNS}
        """,
        [task_id, agent_id, agent_name, action, note, command, started_at],
        TaskStep,
    )


def finish_task_step(
    step_id: str,
    *,
    outcome: TaskStepOutcome,
    result: str | None,
    ended_at: datetime,
) -> TaskStep:
    """Record how an open step ended. A step is finished exactly once.

    The update only matches a step that is still open, so finishing is one
    statement and cannot overwrite an earlier finish.

    Args:
        step_id: The step to finish.
        outcome: ``ok``, ``failed``, ``approval`` or ``consent``.
        result: The step's one-line result, verbatim, or ``None``.
        ended_at: When the step ended (timezone-aware).

    Returns:
        The finished step.

    Raises:
        ValueError: ``outcome`` is not one of the four, ``step_id`` names no
            step, or the step has already ended. A second finish is a caller
            bug, not something to absorb.
    """
    if outcome not in _OUTCOMES:
        raise ValueError(f"Unknown task step outcome {outcome!r}")
    finished = fetch_one(
        f"""
        UPDATE task_steps
        SET outcome = $1, result = $2, ended_at = $3
        WHERE id = $4 AND ended_at IS NULL
        RETURNING {_TASK_STEP_COLUMNS}
        """,
        [outcome, result, ended_at, step_id],
        TaskStep,
    )
    if finished is not None:
        return finished
    if get_task_step(step_id) is None:
        raise ValueError(f"Unknown task step {step_id!r}")
    raise ValueError(f"Task step {step_id!r} has already ended")


def get_task_step(step_id: str) -> TaskStep | None:
    """Return one step by id, or ``None`` when there is no such step."""
    return fetch_one(
        f"SELECT {_TASK_STEP_COLUMNS} FROM task_steps WHERE id = $1",
        [step_id],
        TaskStep,
    )


def list_task_steps(
    task_id: str,
    *,
    limit: int,
    before: tuple[datetime, str] | None = None,
) -> list[TaskStep]:
    """Return one page of a task's steps, newest first.

    Args:
        task_id: The task whose steps are listed.
        limit: Maximum rows returned, at least 1. A caller asks for one more
            than a page to learn whether older rows exist.
        before: Keyset cursor ``(started_at, id)`` of the oldest step already
            shown; only strictly older steps (by ``started_at``, then ``id``)
            are returned. ``None`` returns the newest page.

    Returns:
        Steps ordered by ``started_at`` then ``id``, both descending.

    Raises:
        ValueError: ``limit`` is below 1. SQLite reads a negative LIMIT as
            "no limit", so a bad page size would otherwise list everything.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit!r}")
    conditions = ["task_id = $1"]
    params: list[object] = [task_id]
    if before is not None:
        before_at, before_id = before
        params.extend([before_at, before_id])
        at, sid = len(params) - 1, len(params)
        # A plain comparison, unlike the office-chatter cursor's julianday():
        # started_at has no column default, so every value was bound through
        # the one datetime adapter (UTC, "+00:00") and text order is time
        # order. The cursor then compares exactly as ORDER BY sorts, to the
        # microsecond, and the index still serves the range.
        conditions.append(f"(started_at < ${at} OR (started_at = ${at} AND id < ${sid}))")
    params.append(limit)
    return fetch_all(
        f"""
        SELECT {_TASK_STEP_COLUMNS}
        FROM task_steps
        WHERE {' AND '.join(conditions)}
        ORDER BY started_at DESC, id DESC
        LIMIT ${len(params)}
        """,
        params,
        TaskStep,
    )
