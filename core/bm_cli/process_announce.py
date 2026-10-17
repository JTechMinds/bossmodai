"""BossMod AI — the one live announcement of a background-process change, for either process.

A change is announced the same way wherever it happened: the runtime
worker's watchdog (through ``runtime_events``), the app's Stop and Keep and
floor routes (through the websocket ``manager``), and Telegram's check-in
buttons (through the ``broadcast_manager`` it was given) all call these with
their own sink. The UI repaints an agent's desk on ``agent_process_changed``
and refreshes Needs on ``agent_process_checkin``; both carry ``agent_id`` in
``extra`` so only that agent's desk reacts.

A CLI turn cannot broadcast from where the script ran; it declares the
activity under ``data["activity"]`` instead. :func:`changed_extra` and the
event constants keep that declaration's shape identical to a broadcast.
"""

from __future__ import annotations

from typing import Any

from core.models.agent_process import AgentProcess

PROCESS_CHANGED_EVENT = "agent_process_changed"
PROCESS_CHECKIN_EVENT = "agent_process_checkin"


def changed_extra(agent_id: str, process_id: str | None = None) -> dict[str, str]:
    """The ``extra`` an ``agent_process_changed`` activity carries.

    Args:
        agent_id: The agent whose processes changed (its desk repaints).
        process_id: The one process that changed, when there is exactly one.

    Returns:
        ``{"agent_id"}``, plus ``"process_id"`` when given.

    Raises:
        ValueError: ``agent_id`` is empty; the UI could not route the repaint.
    """
    if not agent_id:
        raise ValueError("a process change needs the agent it belongs to")
    extra = {"agent_id": agent_id}
    if process_id is not None:
        extra["process_id"] = process_id
    return extra


async def announce_processes_changed(
    sink: Any,
    *,
    agent_id: str,
    agent_name: str | None,
    detail: str,
    process_id: str | None = None,
) -> None:
    """Tell the UI one agent's background processes changed.

    Args:
        sink: Anything with ``broadcast_activity``: the worker's
            ``runtime_events`` or the app's websocket ``manager``.
        agent_id: The agent whose desk repaints.
        agent_name: Its name for the activity line; ``None`` when the agent
            no longer exists.
        detail: The activity line, in plain words.
        process_id: The one process that changed, when there is exactly one.
    """
    await sink.broadcast_activity(
        event=PROCESS_CHANGED_EVENT,
        detail=detail,
        agent_name=agent_name,
        extra=changed_extra(agent_id, process_id),
    )


async def announce_checkin(sink: Any, row: AgentProcess, *, agent_name: str | None) -> None:
    """Tell the UI (and Telegram) the operator is asked whether to keep ``row`` running.

    Args:
        sink: Anything with ``broadcast_activity`` (see
            :func:`announce_processes_changed`).
        row: The running process whose check-in was just asked.
        agent_name: Its agent's name; ``None`` when the agent no longer exists.
    """
    await sink.broadcast_activity(
        event=PROCESS_CHECKIN_EVENT,
        detail=f"A background process is still running: {row.command}",
        agent_name=agent_name,
        extra={"agent_id": row.agent_id, "process_id": row.id},
    )
