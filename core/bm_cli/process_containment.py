"""BossMod AI — Process containment for shell runs.

Invariant: no process started by a shell command outlives it. A shell
command is meant to end with its last process; this module enforces that
for programs that detach anyway (Node ``spawn(..., {detached: true})``,
``setsid`` / ``start_new_session``, a double fork, a browser launched by
Playwright). The one sanctioned exception is a pipeline the agent ends with
``&``: it runs under a tag of its own, and
:mod:`core.bm_cli.background_processes` reaps that tag when it is stopped.

Mechanism: every shell run gets a unique tag, set as ``BOSSMOD_SHELL_RUN``
in each spawned process's environment. Children inherit the environment
through every way of detaching, so when the run ends every process still
carrying the tag is terminated. One code path on Linux, WSL2 and macOS.

Known limit: a process that deliberately clears its environment (``env -i``)
escapes. That is accepted: the threat is agents working around timeouts,
not hostile code. The path jail and the approval policy remain the security
boundary.

This is the single seam between running a command (``shell_executor``) and
the OS process table; the executor never reads the process table itself.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)

RUN_TAG_ENV = "BOSSMOD_SHELL_RUN"
# Protocol margin between SIGTERM and SIGKILL, not a tunable.
TERMINATE_GRACE_SECONDS = 3.0
# psutil derives create_time from the boot time, which the OS reports in
# whole seconds, so a fresh process can look up to about a second older than
# the wall clock that started its run.
_CREATE_TIME_SLACK_SECONDS = 1.0
# How long to wait for SIGKILLed survivors to disappear before giving up.
_KILL_WAIT_SECONDS = 1.0


@dataclass(frozen=True)
class ShellRun:
    """One shell run's identity, shared by every process it starts.

    Attributes:
        tag: The value of ``BOSSMOD_SHELL_RUN`` in every spawned process.
        started_at: Wall-clock ``time.time()`` when the run began, comparable
            with ``psutil.Process.create_time()``. A tagged process can only
            have been created after this.
    """

    tag: str
    started_at: float


@contextmanager
def contained_run(run: ShellRun | None = None) -> Iterator[ShellRun]:
    """Scope a shell run: yield its identity, then terminate whatever it left behind.

    Args:
        run: A run the caller already owns (the worker-process path passes
            the parent's run so the tag is shared across the process
            boundary). ``None`` starts a new run.

    Yields:
        The run whose ``tag`` the caller must put in each spawned process's
        environment under ``RUN_TAG_ENV``.

    The reap in ``finally`` always runs, whether the body returned, timed
    out or raised. Reaping a run twice (child process, then its parent) is
    idempotent: the second pass finds nothing.
    """
    active = run if run is not None else ShellRun(tag=uuid.uuid4().hex, started_at=time.time())
    try:
        yield active
    finally:
        reap_run(active)


def reap_run(run: ShellRun) -> list[int]:
    """Terminate every process carrying ``run``'s tag and return the pids found.

    Candidates are this user's processes (same real uid) created since the
    run started, other than this process. That filter keeps the environment
    reads to a handful per call. Each candidate's environment is read and
    matched against ``run.tag``. Matches get SIGTERM, then SIGKILL after
    ``TERMINATE_GRACE_SECONDS`` if still alive.

    A candidate that raises ``psutil.NoSuchProcess`` / ``ZombieProcess``
    exited mid-scan, and one that raises ``psutil.AccessDenied`` belongs to
    the OS or another security context and cannot carry our tag; each is
    skipped. That is the documented behaviour, not a fallback.

    Args:
        run: The run whose processes must end.

    Returns:
        The pids that carried the tag (empty when the run left nothing).
        A non-empty result is logged at WARNING so the operator sees agents
        leaving processes behind.
    """
    uid = os.getuid()
    own_pid = os.getpid()
    earliest = run.started_at - _CREATE_TIME_SLACK_SECONDS
    matches: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "uids", "create_time"]):
        info = proc.info
        if info["pid"] == own_pid:
            continue
        uids = info["uids"]
        create_time = info["create_time"]
        # process_iter leaves an attribute None when it could not be read
        # (the process is gone or not ours to inspect): not a candidate.
        if uids is None or create_time is None:
            continue
        if uids.real != uid or create_time < earliest:
            continue
        try:
            if proc.environ().get(RUN_TAG_ENV) == run.tag:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            continue
    if not matches:
        return []

    pids = [proc.pid for proc in matches]
    logger.warning(
        "shell run left %d process(es) running; terminated pids=%s",
        len(matches),
        pids,
    )
    for proc in matches:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
    _gone, alive = psutil.wait_procs(matches, timeout=TERMINATE_GRACE_SECONDS)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=_KILL_WAIT_SECONDS)
    return pids
