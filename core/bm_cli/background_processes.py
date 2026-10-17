"""BossMod AI — background processes: the one owner of their lifecycle.

An agent ends a pipeline with ``&`` (``npx vite --port 5199 &``) to keep it
running after its command returns: dev servers, watchers, previews. A
background process is a shell run whose reap is deferred:

* **Launch** (:func:`launch`): the pipeline starts under its OWN run tag
  (``BOSSMOD_SHELL_RUN``), not the script's, so the script's reap leaves it
  alone. Its stdout and stderr go to a log file outside ``/me`` (``/me`` is
  an auto-committed workspace), and one ``agent_processes`` row records the
  tag, the root pid and that pid's creation time.
* **Alive** (:func:`is_alive`): the root pid exists, has the recorded
  creation time, and is not a zombie. Nothing holds a handle: the launch may
  happen in the shell child process, which exits right after.
* **Stop** (:func:`stop`): ``reap_run`` on the row's tag, the same OS-level
  mechanism that ends a foreground run, so a detached grandchild goes too.
  Either process (the runtime worker or the app) can call it.
* **Check-in** (:func:`sweep`, :func:`keep_running`): after
  ``background_process_checkin_minutes`` the operator is asked whether to
  keep it. An unanswered question does nothing; no timer ever stops a
  process.

Every function here is synchronous and announces nothing: the callers that
own an event sink (the watchdog, the API, the CLI turn) broadcast
``agent_process_changed`` / ``agent_process_checkin`` from what these return.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

import db
from core import config
from core.bm_cli import filesystem
from core.bm_cli.process_containment import ShellRun, reap_run
from core.models.agent_process import AgentProcess, ProcessStopper

logger = logging.getLogger(__name__)

CHECKIN_SETTING = "background_process_checkin_minutes"
EXITED_REASON = "Exited on its own"
# The rows a previous app run left: quitting the app ends its process group.
APP_STOPPED_REASON = "BossMod stopped"
LOG_DIR_NAME = "process-logs"
# psutil derives create_time from the boot time in whole seconds; the same
# process can read a fraction differently between calls.
_CREATE_TIME_TOLERANCE_SECONDS = 1.0
_LOG_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND
_LOG_MODE = 0o600

StartPipeline = Callable[[str, int], tuple[list[subprocess.Popen[bytes]], int | None]]
"""Starts the pipeline with a run tag and a log fd; returns ``(processes, status when the last could not start)``."""


class BackgroundLaunchError(RuntimeError):
    """A backgrounded pipeline did not start; nothing of it is left running.

    Attributes:
        status: The exit status the script reports for it (127 for a missing
            program, as a foreground pipeline would report).
    """

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BackgroundOwner:
    """Who a script's ``&`` pipelines belong to, threaded into the executor.

    Attributes:
        agent_id: The agent running the script.
        storage_key: Its storage key (names its log folder).
        cwd_virtual: The virtual directory the script runs in.
        item_commands: Each script item's pipeline as the agent wrote it, in
            order (one per ``ShellScript.items`` entry).
    """

    agent_id: str
    storage_key: str
    cwd_virtual: str
    item_commands: tuple[str, ...]


@dataclass(frozen=True)
class SweepResult:
    """What one sweep changed, for the caller to announce.

    Attributes:
        changed_agent_ids: Agents whose rows changed (ended or asked).
        checkins: Rows whose check-in this sweep asked.
    """

    changed_agent_ids: tuple[str, ...]
    checkins: tuple[AgentProcess, ...]


def process_log_dir(storage_key: str) -> Path:
    """The folder one agent's process logs live in: ``<artifacts>/process-logs/<storage_key>``.

    Not created here. The agent's delete removes it (``core.agent_repository``).

    Raises:
        ValueError: ``storage_key`` is empty or not a single path component.
    """
    _require_component("storage key", storage_key)
    return filesystem.artifacts_root() / LOG_DIR_NAME / storage_key


def process_log_path(storage_key: str, process_id: str) -> Path:
    """Where one process's log lives: ``<process_log_dir(storage_key)>/<id>.log``.

    Raises:
        ValueError: ``storage_key`` or ``process_id`` is empty or not a
            single path component.
    """
    _require_component("process id", process_id)
    return process_log_dir(storage_key) / f"{process_id}.log"


def _require_component(label: str, part: str) -> None:
    if not part or part in {".", ".."} or "/" in part or "\\" in part:
        raise ValueError(f"Not a usable {label} for a process log: {part!r}")


def launch(*, owner: BackgroundOwner, command: str, start_pipeline: StartPipeline) -> AgentProcess:
    """Start one pipeline in the background and record it.

    Args:
        owner: The agent and directory the script runs as.
        command: The pipeline as the agent wrote it, for the row.
        start_pipeline: Starts the pipeline with the given run tag, sending
            stdout and stderr to the given fd unless its own redirects say
            otherwise; injected by the executor (no import cycle).

    Returns:
        The ``running`` row. Its check-in is due in
        ``background_process_checkin_minutes``.

    Raises:
        BackgroundLaunchError: The log could not be opened, or the
            pipeline's last command did not start; anything it did start is
            reaped first.
        core.config.ConfigError: The check-in setting is missing or not a
            positive whole number (read before anything starts).
        psutil.Error, sqlite3.Error: Recording failed after the start; the
            launch is reaped first, so nothing runs untracked.
    """
    minutes = config.require_positive_int(CHECKIN_SETTING)
    process_id = str(uuid.uuid4())
    log_path = process_log_path(owner.storage_key, process_id)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fd = os.open(log_path, _LOG_FLAGS, _LOG_MODE)
    except OSError as exc:
        raise BackgroundLaunchError(f"Could not open the background log: {exc.strerror}", status=1) from exc
    run = ShellRun(tag=uuid.uuid4().hex, started_at=time.time())
    try:
        procs, failed = start_pipeline(run.tag, log_fd)
    finally:
        # The children hold their own copies.
        os.close(log_fd)
    if failed is not None:
        _abandon(run, procs)
        raise BackgroundLaunchError("The background command did not start.", status=failed)
    root = procs[-1]
    started_at = datetime.fromtimestamp(run.started_at, tz=timezone.utc)
    try:
        # Unwaited, so even a root that already exited is still here as a zombie.
        create_time = psutil.Process(root.pid).create_time()
        row = db.create_agent_process(
            process_id=process_id,
            agent_id=owner.agent_id,
            command=command,
            cwd=owner.cwd_virtual,
            run_tag=run.tag,
            pid=root.pid,
            pid_create_time=create_time,
            log_path=str(log_path),
            started_at=started_at,
            checkin_due_at=started_at + timedelta(minutes=minutes),
        )
    except BaseException:
        _abandon(run, procs)
        raise
    logger.info("agent %s started background process %s (pid %d): %s", owner.agent_id, row.id, root.pid, command)
    return row


def _abandon(run: ShellRun, procs: list[subprocess.Popen[bytes]]) -> None:
    """End a launch that will not be recorded, and collect its exit statuses."""
    reap_run(run)
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def is_alive(row: AgentProcess) -> bool:
    """Whether the row's root process still runs.

    Alive means: ``row.pid`` exists, its creation time matches
    ``row.pid_create_time`` (within a second), and it is not a zombie. A pid
    that cannot be inspected (``AccessDenied``) belongs to another user or
    security context, so it is not this row's process: not alive.
    """
    try:
        proc = psutil.Process(row.pid)
        if abs(proc.create_time() - row.pid_create_time) > _CREATE_TIME_TOLERANCE_SECONDS:
            return False
        return proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def stop(process_id: str, *, by: ProcessStopper, reason: str) -> AgentProcess | None:
    """Stop one running background process and everything it started.

    Args:
        process_id: The row.
        by: ``agent``, ``operator`` or ``system``.
        reason: Why, in plain words (shown on the desk and to the agent).

    Returns:
        The ``stopped`` row, or ``None`` when there is no such row or it had
        already ended.
    """
    row = db.get_agent_process(process_id)
    if row is None or row.status != "running":
        return None
    reap_run(ShellRun(row.run_tag, row.started_at_epoch))
    stopped = db.mark_agent_process_ended(
        row.id, status="stopped", ended_at=_now(), stopped_by=by, stop_reason=reason,
    )
    if stopped is not None:
        logger.info("background process %s stopped by %s: %s", row.id, by, reason)
    return stopped


def stop_for_agent(agent_id: str, *, reason: str) -> list[AgentProcess]:
    """Stop every running background process of one agent (``by="system"``).

    Called before a change that invalidates the agent's roots: removal, a
    floor move, vacation. A second call finds nothing running and stops
    nothing.

    Returns:
        The rows this call stopped, for the caller to announce.
    """
    stopped: list[AgentProcess] = []
    for row in db.list_agent_processes(agent_id, ["running"]):
        ended = stop(row.id, by="system", reason=reason)
        if ended is not None:
            stopped.append(ended)
    return stopped


def stop_under_path(real_root: Path, *, reason: str) -> list[AgentProcess]:
    """Stop every running background process whose root works inside ``real_root``.

    Called before a project folder moves. The working directory is read
    from the live root process, so it is the directory the process really
    uses. A row whose process is already gone is left for the sweep.

    Returns:
        The rows this call stopped, for the caller to announce.
    """
    root = Path(real_root).resolve()
    stopped: list[AgentProcess] = []
    for row in db.list_running_agent_processes():
        if not is_alive(row):
            continue
        try:
            cwd = Path(psutil.Process(row.pid).cwd())
        except psutil.NoSuchProcess:
            # Exited since is_alive; the sweep records it.
            continue
        if cwd != root and root not in cwd.parents:
            continue
        ended = stop(row.id, by="system", reason=reason)
        if ended is not None:
            stopped.append(ended)
    return stopped


def live_for_agent(agent_id: str) -> list[AgentProcess]:
    """The agent's running processes, newest first, after recording any that ended.

    A row whose process is gone is marked ``exited`` here (see
    :func:`_end_dead`), so every reader sees the truth without waiting for
    the next sweep.
    """
    live: list[AgentProcess] = []
    for row in db.list_agent_processes(agent_id, ["running"]):
        if is_alive(row):
            live.append(row)
        else:
            _end_dead(row, EXITED_REASON)
    return live


def recent_ended_for_agent(agent_id: str, *, limit: int) -> list[AgentProcess]:
    """The agent's newest ended processes (``exited`` or ``stopped``), newest first."""
    return db.list_agent_processes(agent_id, ["exited", "stopped"], limit=limit)


def sweep(*, exit_reason: str = EXITED_REASON) -> SweepResult:
    """Record ended processes and ask due check-ins; called on each watchdog tick.

    For every running row: a dead one is marked ``exited`` with
    ``exit_reason`` (boot passes :data:`APP_STOPPED_REASON`); a live one whose
    ``checkin_due_at`` has passed and whose check-in was not yet asked gets
    ``checkin_requested_at`` set, once per arming. Nothing is stopped.

    Returns:
        The agents whose rows changed and the check-ins asked, for the
        caller to announce.
    """
    now = _now()
    changed: set[str] = set()
    checkins: list[AgentProcess] = []
    for row in db.list_running_agent_processes():
        if not is_alive(row):
            if _end_dead(row, exit_reason) is not None:
                changed.add(row.agent_id)
            continue
        if row.checkin_requested_at is not None or row.checkin_due_at > now:
            continue
        asked = db.request_agent_process_checkin(row.id, requested_at=now)
        if asked is not None:
            changed.add(row.agent_id)
            checkins.append(asked)
    return SweepResult(tuple(sorted(changed)), tuple(checkins))


def keep_running(process_id: str) -> AgentProcess | None:
    """Answer a check-in with "keep": the next one is due a full interval from now.

    Returns:
        The updated row, or ``None`` when it is not running.

    Raises:
        core.config.ConfigError: The check-in setting is missing or invalid.
    """
    minutes = config.require_positive_int(CHECKIN_SETTING)
    return db.rearm_agent_process_checkin(process_id, due_at=_now() + timedelta(minutes=minutes))


def prune_ended(before: datetime) -> int:
    """Delete ended rows that ended before ``before``, with their log files.

    Running rows are never touched. A log that is already gone is the state
    this reaches, so it is not an error.

    Args:
        before: The cutoff, an aware datetime (now minus
            ``agent_process_retention_days``).

    Returns:
        How many rows were deleted.

    Raises:
        ValueError: ``before`` is naive.
        OSError: A log could not be deleted for any reason other than being
            gone; the message names the path. Its row is already deleted.
    """
    rows = db.prune_ended_agent_processes(before)
    for row in rows:
        try:
            Path(row.log_path).unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OSError(exc.errno, f"Could not delete the process log {row.log_path}: {exc.strerror}") from exc
    return len(rows)


def read_log_tail(row: AgentProcess, max_bytes: int) -> str:
    """The last ``max_bytes`` of a process's log, decoded as UTF-8.

    When the log is longer, the text starts with one line saying how much
    is shown. Callers pass ``cli_shell_max_output_bytes``.

    Raises:
        ValueError: ``max_bytes`` is below 1.
        FileNotFoundError: The log file is gone (deleted outside BossMod).
    """
    if max_bytes < 1:
        raise ValueError(f"max_bytes must be at least 1, got {max_bytes}")
    with Path(row.log_path).open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        start = max(size - max_bytes, 0)
        handle.seek(start)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if start:
        return f"[last {max_bytes} of {size} bytes]\n{text}"
    return text


def describe_duration(seconds: float) -> str:
    """A running time in words an agent and the operator read: ``45s``, ``12m``, ``2h 5m``, ``3d 4h``."""
    whole = max(int(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    minutes, _ = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def running_for(row: AgentProcess) -> str:
    """How long ``row`` has been running, by :func:`describe_duration`."""
    return describe_duration((_now() - row.started_at).total_seconds())


def _end_dead(row: AgentProcess, reason: str) -> AgentProcess | None:
    """Mark a row whose root process is gone ``exited``, reaping what it left.

    A root that exited can leave tagged children (a launcher that spawned a
    detached server, then quit). Nothing would track them once the row
    ends, so they are reaped: whatever runs in the background is always a
    row the agent and the operator can see.
    """
    leftovers = reap_run(ShellRun(row.run_tag, row.started_at_epoch))
    if leftovers:
        logger.warning("background process %s exited and left pids %s; reaped them", row.id, leftovers)
    return db.mark_agent_process_ended(
        row.id, status="exited", ended_at=_now(), stopped_by=None, stop_reason=reason,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
