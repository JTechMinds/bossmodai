"""Background processes agents started with ``&``: the desk's list, Stop and Keep.

Thin: the lifecycle lives in core/bm_cli/background_processes.py. Stop and
Keep tell the UI through an ``agent_process_changed`` activity carrying the
agent id, so the agent's desk repaints and Needs drops an answered check-in.
Stopping reaps the processes, which can take a few seconds, so it runs off
the serve loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

import db
from api.websocket import manager
from core import config
from core.bm_cli import background_processes
from core.bm_cli.process_announce import announce_processes_changed
from core.models.agent_process import AgentProcess

router = APIRouter()

# How many ended processes the desk lists under the running ones.
RECENT_ENDED_LIMIT = 5
OPERATOR_STOP_REASON = "Stopped by you"
MAX_OUTPUT_SETTING = "cli_shell_max_output_bytes"
LOG_GONE = "The log file is gone."


def process_view(row: AgentProcess, *, max_log_bytes: int) -> dict[str, Any]:
    """One process as the desk reads it, with the end of its log.

    A log file that is gone is said (``log_error``), not shown as empty.

    Args:
        row: The process.
        max_log_bytes: The log tail cap (``cli_shell_max_output_bytes``).

    Returns:
        ``id``, ``agent_id``, ``command``, ``cwd``, ``pid``, ``status``,
        ``stopped_by``, ``stop_reason``, ``started_at``, ``ended_at``,
        ``checkin_requested_at`` (ISO strings or None), ``log_tail`` (str or
        None) and ``log_error`` (str or None).
    """
    try:
        log_tail: str | None = background_processes.read_log_tail(row, max_log_bytes)
        log_error = None
    except FileNotFoundError:
        log_tail, log_error = None, LOG_GONE
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "command": row.command,
        "cwd": row.cwd,
        "pid": row.pid,
        "status": row.status,
        "stopped_by": row.stopped_by,
        "stop_reason": row.stop_reason,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at is not None else None,
        "checkin_requested_at": (
            row.checkin_requested_at.isoformat() if row.checkin_requested_at is not None else None
        ),
        "log_tail": log_tail,
        "log_error": log_error,
    }


async def _announce(row: AgentProcess, detail: str) -> None:
    """Tell the UI one agent's background processes changed."""
    agent = db.get_agent(row.agent_id)
    await announce_processes_changed(
        manager, agent_id=row.agent_id, agent_name=agent.name if agent is not None else None,
        detail=detail, process_id=row.id,
    )


@router.get("/agents/{agent_id}/processes")
async def list_agent_processes(agent_id: str) -> list[dict[str, Any]]:
    """One agent's running processes, newest first, then its newest 5 ended ones.

    Each carries the end of its log. Processes that ended since they were
    last looked at are recorded as ended first.

    Raises:
        HTTPException: 404 when the agent does not exist.
    """
    if db.get_agent(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    max_log_bytes = config.require_int(MAX_OUTPUT_SETTING)
    running = await asyncio.to_thread(background_processes.live_for_agent, agent_id)
    ended = background_processes.recent_ended_for_agent(agent_id, limit=RECENT_ENDED_LIMIT)
    return [process_view(row, max_log_bytes=max_log_bytes) for row in [*running, *ended]]


@router.post("/agent-processes/{process_id}/stop")
async def stop_agent_process(process_id: str) -> dict[str, Any]:
    """Stop one running process and everything it started, as the operator.

    Raises:
        HTTPException: 404 when there is no such process or it already ended.
    """
    stopped = await asyncio.to_thread(
        background_processes.stop, process_id, by="operator", reason=OPERATOR_STOP_REASON,
    )
    if stopped is None:
        raise HTTPException(404, "No running background process with that id")
    await _announce(stopped, f"Stopped a background process: {stopped.command}")
    return process_view(stopped, max_log_bytes=config.require_int(MAX_OUTPUT_SETTING))


@router.post("/agent-processes/{process_id}/keep")
async def keep_agent_process(process_id: str) -> dict[str, Any]:
    """Answer a check-in with "keep running": the next one is a full interval away.

    Raises:
        HTTPException: 404 when there is no such process or it is not running.
    """
    kept = background_processes.keep_running(process_id)
    if kept is None:
        raise HTTPException(404, "No running background process with that id")
    await _announce(kept, f"Kept a background process running: {kept.command}")
    return process_view(kept, max_log_bytes=config.require_int(MAX_OUTPUT_SETTING))
