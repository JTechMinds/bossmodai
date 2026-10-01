"""BossMod AI — the one live announcement of a schedule run, for either process.

A run is announced the same way wherever it happened: the runtime worker's
clock (core/scheduling/watch.py, through ``runtime_events``) and the app's
Run now (api/routes/schedules.py, through the websocket ``manager``) both
call ``announce_run`` with their own sink. Kept apart from
core/scheduling/notices.py, which persists the operator's DM note about an
agent's change; this module only broadcasts what a run already persisted.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.task_origin_mirrors import broadcast_origin_line
from core.scheduling.runner import ScheduleRun

_OUTCOME_LINES = {
    "fired": "ran",
    "missed": "missed a run (the scheduler was not running then)",
    "skipped_open": "skipped a run (the last run is still open)",
    "skipped_vacation": "skipped a run (the agent is on vacation)",
    "failed": "could not create its task",
}
_MANUAL_LINE = "ran now"


async def announce_run(sink: Any, run: ScheduleRun, *, agent_name: str | None, manual: bool = False) -> None:
    """Tell the UI what one schedule run did.

    In order: the run's DM "Created" line live (``broadcast_origin_line``;
    nothing when the run posted none), ``task_created`` when it fired with a
    task, and ``schedule_ran`` with ``extra={agent_id, schedule_id, outcome}``
    only when ``run.changed``. That rule lives here rather than at the
    worker's call site because it is a property of the run itself: a fired
    run is always ``changed`` (Run now's included), so only the worker's
    repeated identical skips are ever held back, keeping the persisted
    activity log to one row per skip streak.

    Args:
        sink: Anything with ``broadcast_activity``, ``broadcast_chat_message``
            and ``broadcast_channel_message``: the worker's ``runtime_events``
            or the app's websocket ``manager``.
        run: The handled occurrence (``runner.run_occurrence`` or ``runner.run_now``).
        agent_name: The schedule's agent's name for the activity lines;
            ``None`` when the agent no longer exists.
        manual: The operator's Run now; its ``schedule_ran`` line reads
            "ran now" instead of the outcome's line.

    Raises:
        KeyError: ``run.outcome`` has no line, or the origin line is
            malformed (see ``broadcast_origin_line``); both are bugs.
    """
    await broadcast_origin_line(sink, run.origin_line)
    if run.outcome == "fired" and run.task is not None:
        await sink.broadcast_activity(
            event="task_created",
            detail=f'Scheduled task "{run.task.title}" created',
            agent_name=agent_name,
        )
    if not run.changed:
        return
    line = _MANUAL_LINE if manual else _OUTCOME_LINES[run.outcome]
    await sink.broadcast_activity(
        event="schedule_ran",
        detail=f'Schedule "{run.title}" {line}',
        agent_name=agent_name,
        extra={"agent_id": run.agent_id, "schedule_id": run.schedule_id, "outcome": run.outcome},
    )
