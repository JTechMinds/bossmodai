"""BossMod AI — Record each execution step an agent takes on its task.

The execution turn writes a step when it starts (so a long command shows
while it runs) and finishes it once its result is final. A step whose turn
dies mid-step stays open: nothing guesses how it ended. Steps are the
operator's record only: they never write ``task_events``, ``status_note`` or
``activity_log``, and they never reach an agent's prompt.

Lives in the agent loop because it reads the loop's action and result dicts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import db
from core.agent_loop.liveness import FAILED_STEP_EVENTS
from core.bm_cli.types import BossModCliResult
from core.models import Agent, TaskStep, TaskStepOutcome

logger = logging.getLogger(__name__)


def _optional_text(value: Any, *, field: str, agent: str) -> str | None:
    """Return ``value`` stripped, or ``None`` when it is missing or blank.

    The note, command and result are optional to a step. One that is not
    text is a bug where the action or result was built; it is left out and
    logged rather than failing the agent's turn over a display field.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        logger.error(
            "Task step %s from %s is %s, not text; it is left out of the step",
            field, agent, type(value).__name__,
        )
        return None
    return value.strip() or None


def start_task_step(
    *,
    task_id: str | None,
    agent: Agent,
    action: dict[str, Any],
    at: datetime,
) -> TaskStep | None:
    """Write the open step for an action the agent is about to run.

    Args:
        task_id: The task the turn is bound to, or ``None`` for a turn with no
            bound task (a detached turn, or no live work). Then nothing is
            written.
        agent: The agent taking the step; its id and current name are kept.
        action: The parsed, validated execution action. ``action["action"]``
            is stored verbatim; ``thought`` becomes the note, and ``command``
            the command when the action is ``bm_cli``.
        at: When the step starts (timezone-aware).

    Returns:
        The open step, or ``None`` when ``task_id`` is falsy.

    Raises:
        KeyError: ``action`` has no ``action`` name (parse never returns one
            without it).
        sqlite3.IntegrityError: The task or agent does not exist.
    """
    if not task_id:
        return None
    name = action["action"]
    note = _optional_text(action.get("thought"), field="note", agent=agent.name)
    command = (
        _optional_text(action.get("command"), field="command", agent=agent.name)
        if name == "bm_cli"
        else None
    )
    return db.create_task_step(
        task_id=task_id,
        agent_id=agent.id,
        agent_name=agent.name,
        action=name,
        note=note,
        command=command,
        started_at=at,
    )


def _step_outcome(result: dict[str, Any]) -> TaskStepOutcome:
    """Classify a step's final result for the operator.

    The first that holds: an approval wait, a consent wait, a failure (the
    same events the no-progress guardian counts as failed), else ``ok``.
    """
    if result.get("approval_required"):
        return "approval"
    if result.get("consent_required"):
        return "consent"
    if result.get("event") in FAILED_STEP_EVENTS:
        return "failed"
    return "ok"


def cli_step_result(cli_result: BossModCliResult) -> dict[str, Any]:
    """Return the step result for a CLI result that never became a turn result.

    The resume prelude runs an operator-approved command straight through the
    CLI runtime, so there is no turn-local result dict to finish its step
    with. This builds the few keys :func:`finish_task_step` reads, with the
    same ``bm_cli_result`` / ``bm_cli_error`` event the turn's own mapping
    uses, so the step is classified on the one path every step takes.

    Not ``cli_turn_result.map_cli_result``: that one has side effects (it
    surfaces gate blocks as ``Blocked —`` origin lines and may wake owners),
    which the prelude deliberately does not trigger. This is pure.

    Args:
        cli_result: The command's result from the CLI runtime.

    Returns:
        ``event``, ``detail``, ``approval_required`` and ``consent_required``.
    """
    return {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "approval_required": bool(cli_result.approval_required),
        "consent_required": bool(cli_result.consent_required),
    }


def finish_task_step(step: TaskStep, *, result: dict[str, Any], at: datetime) -> TaskStep:
    """Finish ``step`` with its action's final result.

    Finished by the step's id, not the task: the action may have changed the
    agent's binding (``complete`` ends it), which does not matter here.

    Args:
        step: The open step :func:`start_task_step` returned.
        result: The action's final turn-local result dict.
        at: When the step ended (timezone-aware).

    Returns:
        The finished step.

    Raises:
        ValueError: The step is unknown or has already ended
            (``db.finish_task_step``).
    """
    return db.finish_task_step(
        step.id,
        outcome=_step_outcome(result),
        result=_optional_text(result.get("detail"), field="result", agent=step.agent_name),
        ended_at=at,
    )


def task_step_event(step: TaskStep) -> dict[str, Any]:
    """Return the one wire shape of a step, for the API and the live event alike.

    Shared by ``GET /api/tasks/{id}/steps[/recent]`` and the ``task_step``
    runtime event, so the task detail reads loaded and live rows identically.

    Args:
        step: A stored step.

    Returns:
        ``id``, ``task_id``, ``agent_id``, ``agent_name``, ``action``,
        ``note``, ``command``, ``outcome``, ``result``, and ``started_at`` and
        ``ended_at`` as ISO 8601 (``ended_at`` is ``None`` while open).
    """
    return {
        "id": step.id,
        "task_id": step.task_id,
        "agent_id": step.agent_id,
        "agent_name": step.agent_name,
        "action": step.action,
        "note": step.note,
        "command": step.command,
        "outcome": step.outcome,
        "result": step.result,
        "started_at": step.started_at.isoformat(),
        "ended_at": step.ended_at.isoformat() if step.ended_at is not None else None,
    }
