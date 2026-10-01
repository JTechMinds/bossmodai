"""BossMod AI — the agent's ``schedules`` command: its scheduled recurring tasks.

Read-only. A schedule (core/scheduling) creates one task per run; this lists
the calling agent's schedules so it can see what recurs, when the next run
is, and whether the last run's task is still open (an open run makes later
runs skip). Kept out of state_commands.py, which is a different concern and
already long.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.bm_cli.results import success_result, trim
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models.schedule import AgentSchedule
from core.scheduling.recurrence import describe, format_local_run, next_occurrence

NO_SCHEDULES = "You have no scheduled recurring tasks."
_SECTION = "SCHEDULED RECURRING TASKS"
_HEADER = "title | repeats | on/off | next run | last run | last task"


def handle_schedules(
    context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None,
) -> BossModCliResult:
    """List the calling agent's schedules (``schedules``).

    One line per schedule: its title, its rule in words, ``on``/``off``, the
    next run on the host's clock (``off`` while switched off), the last
    outcome with its local time, and the last run's task id with its status.

    Args:
        context: The calling agent's CLI context; only its own schedules are listed.
        parsed: The parsed command (``schedules`` takes no arguments).
        content: Unused; the command takes no body.

    Returns:
        A success result whose ``data["schedules"]`` holds the same rows, or
        the line "You have no scheduled recurring tasks." when there are none.

    Raises:
        pydantic.ValidationError: A stored rule is corrupt.
    """
    now = datetime.now(timezone.utc)
    schedules = db.list_schedules_for_agent(context.agent.id)
    rows = [_row(schedule, now=now) for schedule in schedules]
    lines = [_HEADER, *(_line(row) for row in rows)] if rows else [NO_SCHEDULES]
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked scheduled recurring tasks via BossMod CLI",
        kind="schedules",
        data={"schedules": rows},
        sections=[(_SECTION, lines)],
        cwd=context.cwd,
    )


def _row(schedule: AgentSchedule, *, now: datetime) -> dict[str, Any]:
    """One schedule as plain data for the line and the result's ``data``."""
    last_task = db.get_task(schedule.last_task_id) if schedule.last_task_id else None
    return {
        "id": schedule.id,
        "title": schedule.title,
        "repeats": describe(schedule.recurrence),
        "enabled": schedule.enabled,
        "next_run": format_local_run(next_occurrence(schedule.recurrence, after=now)) if schedule.enabled else None,
        "last_outcome": schedule.last_outcome,
        "last_run": format_local_run(schedule.last_occurrence_at) if schedule.last_occurrence_at else None,
        "last_task_id": schedule.last_task_id,
        "last_task_status": last_task.status if last_task is not None else None,
    }


def _line(row: dict[str, Any]) -> str:
    last_run = f"{row['last_outcome']} {row['last_run']}" if row["last_outcome"] else "none yet"
    if row["last_task_id"]:
        last_task = f"{row['last_task_id']} ({row['last_task_status'] or 'no longer on the board'})"
    else:
        last_task = "-"
    return " | ".join([
        trim(str(row["title"])),
        row["repeats"],
        "on" if row["enabled"] else "off",
        row["next_run"] or "off",
        last_run,
        last_task,
    ])
