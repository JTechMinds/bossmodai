"""BossMod AI — background processes agents started with ``&`` (``agent_processes``).

Rows only: whether a process is alive, and stopping it, are
``core.bm_cli.background_processes``'s. A row is written by whichever
process ran the agent's script (the runtime worker, or the shell child it
spawned for a long wait) and read and ended by the worker and the app. An
agent's rows are deleted with the agent (``db.agents.delete_agent_rows``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from core.models.agent_process import AgentProcess, AgentProcessStatus, ProcessStopper
from db.connection import transaction
from db.crud import execute, fetch_all, fetch_one, query

_COLUMNS = (
    "id, agent_id, command, cwd, run_tag, pid, pid_create_time, log_path, status, stopped_by, "
    "stop_reason, started_at, ended_at, checkin_due_at, checkin_requested_at"
)
_ENDED_STATUSES = frozenset({"exited", "stopped"})


def create_agent_process(
    *,
    process_id: str,
    agent_id: str,
    command: str,
    cwd: str,
    run_tag: str,
    pid: int,
    pid_create_time: float,
    log_path: str,
    started_at: datetime,
    checkin_due_at: datetime,
) -> AgentProcess:
    """Record one launched background process as ``running``.

    Args:
        process_id: The id chosen before launch (it names the log file).
        agent_id: An existing agent's id (FK).
        command: The pipeline as the agent wrote it.
        cwd: The virtual directory it started in.
        run_tag: The run tag every process of the launch carries.
        pid: The root process.
        pid_create_time: ``psutil`` creation time of ``pid``.
        log_path: The real path of its log file.
        started_at: When the launch began (aware).
        checkin_due_at: When to ask the operator first (aware).

    Returns:
        The stored row.

    Raises:
        sqlite3.IntegrityError: ``agent_id`` names no agent, or the id is taken.
        RuntimeError: The row could not be read back.
    """
    execute(
        """
        INSERT INTO agent_processes (
            id, agent_id, command, cwd, run_tag, pid, pid_create_time, log_path, status,
            started_at, checkin_due_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'running', $9, $10)
        """,
        [process_id, agent_id, command, cwd, run_tag, pid, pid_create_time, log_path, started_at, checkin_due_at],
    )
    # Re-read rather than RETURNING: SQLite reports no declared column types
    # there, so the timestamps would come back as text.
    row = get_agent_process(process_id)
    if row is None:
        raise RuntimeError(f"Failed to reload created agent process {process_id}")
    return row


def get_agent_process(process_id: str) -> AgentProcess | None:
    """Return one row, or ``None`` when there is no such process."""
    return fetch_one(f"SELECT {_COLUMNS} FROM agent_processes WHERE id = $1", [process_id], AgentProcess)


def list_agent_processes(
    agent_id: str, statuses: Iterable[AgentProcessStatus], *, limit: int | None = None,
) -> list[AgentProcess]:
    """Return one agent's rows in ``statuses``, newest start first.

    Args:
        agent_id: The agent.
        statuses: Which statuses to include.
        limit: At most this many rows; None for all.

    Raises:
        ValueError: ``statuses`` is empty (that would ask for nothing), or
            ``limit`` is below 1.
    """
    wanted = sorted(set(statuses))
    if not wanted:
        raise ValueError("list_agent_processes needs at least one status")
    if limit is not None and limit < 1:
        raise ValueError(f"list_agent_processes limit must be at least 1, got {limit}")
    placeholders = ", ".join(f"${index}" for index in range(2, len(wanted) + 2))
    bounded = f" LIMIT {int(limit)}" if limit is not None else ""
    return fetch_all(
        f"""
        SELECT {_COLUMNS} FROM agent_processes
        WHERE agent_id = $1 AND status IN ({placeholders})
        ORDER BY started_at DESC, id{bounded}
        """,
        [agent_id, *wanted],
        AgentProcess,
    )


def list_running_agent_processes() -> list[AgentProcess]:
    """Return every ``running`` row, oldest start first: the sweep's work list."""
    return fetch_all(
        f"SELECT {_COLUMNS} FROM agent_processes WHERE status = 'running' ORDER BY started_at, id",
        [],
        AgentProcess,
    )


def list_checkin_requested_agent_processes() -> list[AgentProcess]:
    """Return ``running`` rows whose check-in was asked and not yet answered, oldest ask first."""
    return fetch_all(
        f"""
        SELECT {_COLUMNS} FROM agent_processes
        WHERE status = 'running' AND checkin_requested_at IS NOT NULL
        ORDER BY checkin_requested_at, id
        """,
        [],
        AgentProcess,
    )


def find_agent_processes_by_prefix(agent_id: str, prefix: str) -> list[AgentProcess]:
    """Return ``agent_id``'s rows whose id starts with ``prefix``, newest start first.

    Compared with ``substr`` rather than ``LIKE``, so ``_`` and ``%`` in a
    prefix are not wildcards.

    Raises:
        ValueError: ``prefix`` is empty.
    """
    if not prefix:
        raise ValueError("find_agent_processes_by_prefix needs a non-empty prefix")
    return fetch_all(
        f"""
        SELECT {_COLUMNS} FROM agent_processes
        WHERE agent_id = $1 AND substr(id, 1, length($2)) = $2
        ORDER BY started_at DESC, id
        """,
        [agent_id, prefix],
        AgentProcess,
    )


def mark_agent_process_ended(
    process_id: str,
    *,
    status: AgentProcessStatus,
    ended_at: datetime,
    stopped_by: ProcessStopper | None,
    stop_reason: str,
) -> AgentProcess | None:
    """End a ``running`` row once; a row already ended is left as it is.

    Args:
        process_id: The row.
        status: ``exited`` (it ended on its own) or ``stopped``.
        ended_at: When (aware).
        stopped_by: Who stopped it; must be None for ``exited``.
        stop_reason: Why it ended, in plain words.

    Returns:
        The ended row, or ``None`` when it was not running (already ended
        by someone else, or no such row).

    Raises:
        ValueError: ``status`` is ``running``, or ``stopped_by`` does not
            match ``status``.
    """
    if status not in _ENDED_STATUSES:
        raise ValueError(f"mark_agent_process_ended cannot set status {status!r}")
    if (status == "stopped") != (stopped_by is not None):
        raise ValueError("stopped_by is required for a stop and forbidden for an exit")
    updated = query(
        """
        UPDATE agent_processes
        SET status = $2, ended_at = $3, stopped_by = $4, stop_reason = $5
        WHERE id = $1 AND status = 'running'
        RETURNING id
        """,
        [process_id, status, ended_at, stopped_by, stop_reason],
    )
    return get_agent_process(process_id) if updated else None


def request_agent_process_checkin(process_id: str, *, requested_at: datetime) -> AgentProcess | None:
    """Mark a running row's check-in as asked, once per arming.

    Returns:
        The updated row when this call asked it; ``None`` when it was
        already asked or the row is not running.
    """
    updated = query(
        """
        UPDATE agent_processes SET checkin_requested_at = $2
        WHERE id = $1 AND status = 'running' AND checkin_requested_at IS NULL
        RETURNING id
        """,
        [process_id, requested_at],
    )
    return get_agent_process(process_id) if updated else None


def rearm_agent_process_checkin(process_id: str, *, due_at: datetime) -> AgentProcess | None:
    """Clear an asked check-in and set the next one; running rows only.

    Returns:
        The updated row, or ``None`` when the row is not running.
    """
    updated = query(
        """
        UPDATE agent_processes SET checkin_due_at = $2, checkin_requested_at = NULL
        WHERE id = $1 AND status = 'running'
        RETURNING id
        """,
        [process_id, due_at],
    )
    return get_agent_process(process_id) if updated else None


def prune_ended_agent_processes(before: datetime) -> list[AgentProcess]:
    """Delete ended (``exited`` / ``stopped``) rows that ended before a cutoff.

    ``running`` rows are never touched, however old. The rows are read and
    deleted in one transaction, so the returned list is exactly what was
    deleted; the caller removes their log files. ``ended_at`` is stored by
    the datetime adapter as UTC ISO text, and the cutoff goes through the
    same adapter, so the text comparison orders correctly.

    Args:
        before: The cutoff, an aware datetime.

    Returns:
        The deleted rows, oldest end first.

    Raises:
        ValueError: ``before`` is naive, so its zone would be a guess.
    """
    if before.tzinfo is None:
        raise ValueError("prune_ended_agent_processes needs an aware datetime")
    where = "status IN ('exited', 'stopped') AND ended_at IS NOT NULL AND ended_at < $1"
    with transaction():
        rows = fetch_all(
            f"SELECT {_COLUMNS} FROM agent_processes WHERE {where} ORDER BY ended_at, id",
            [before],
            AgentProcess,
        )
        execute(f"DELETE FROM agent_processes WHERE {where}", [before])
    return rows
