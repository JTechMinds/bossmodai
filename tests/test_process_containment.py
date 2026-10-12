"""No process started by a shell run outlives it (core/bm_cli/process_containment.py).

Real processes only. Every process these tests start is either tagged (and
reaped by the code under test) or killed by the test itself, so nothing
else on the machine is ever signalled.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import time
import uuid
from collections.abc import Iterator

import psutil
import pytest

from core.bm_cli.process_containment import RUN_TAG_ENV, ShellRun, contained_run, reap_run

# Prints the pid of a `sleep 60` started in its own session, then exits.
_DETACH = textwrap.dedent(
    """\
    import subprocess
    proc = subprocess.Popen(
        ["sleep", "60"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(proc.pid)
    """
)

# Double fork: the grandchild leaves the session, reports its pid, becomes `sleep 60`.
_DOUBLE_FORK = textwrap.dedent(
    """\
    import os
    read_end, write_end = os.pipe()
    child = os.fork()
    if child == 0:
        os.setsid()
        if os.fork() == 0:
            os.close(read_end)
            os.write(write_end, str(os.getpid()).encode())
            os.close(write_end)
            devnull = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(devnull, fd)
            os.execvp("sleep", ["sleep", "60"])
        os._exit(0)
    os.close(write_end)
    os.waitpid(child, 0)
    print(os.read(read_end, 32).decode())
    """
)


def _gone(pid: int, *, within: float = 5.0) -> bool:
    """Return whether *pid* has exited (a zombie awaiting its reaper counts as exited)."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def started() -> Iterator[list[psutil.Process]]:
    """Processes this test started; any still alive at teardown are killed.

    Held as ``psutil.Process`` objects, which check the creation time before
    signalling, so a pid the OS reused for someone else is never touched.
    """
    procs: list[psutil.Process] = []
    yield procs
    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue


def _run_tagged(code: str, run: ShellRun) -> int:
    """Run a helper script with *run*'s tag in its environment and return the pid it prints."""
    env = {**os.environ, RUN_TAG_ENV: run.tag}
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return int(out.stdout.strip())


def test_a_child_that_starts_a_new_session_is_reaped(started: list[psutil.Process]) -> None:
    with contained_run() as run:
        pid = _run_tagged(_DETACH, run)
        started.append(psutil.Process(pid))
        assert psutil.pid_exists(pid)

    assert _gone(pid)


def test_a_double_forked_grandchild_is_reaped(started: list[psutil.Process]) -> None:
    with contained_run() as run:
        pid = _run_tagged(_DOUBLE_FORK, run)
        started.append(psutil.Process(pid))
        assert psutil.Process(pid).ppid() != os.getpid()

    assert _gone(pid)


def test_an_untagged_sibling_is_left_alone(started: list[psutil.Process]) -> None:
    with contained_run() as run:
        sibling = subprocess.Popen(
            ["sleep", "60"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(psutil.Process(sibling.pid))
        tagged = _run_tagged(_DETACH, run)
        started.append(psutil.Process(tagged))

    assert _gone(tagged)
    assert sibling.poll() is None
    sibling.kill()
    sibling.wait(timeout=5)


def test_reaping_a_run_that_left_nothing_returns_empty_and_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = ShellRun(tag=uuid.uuid4().hex, started_at=time.time())

    with caplog.at_level(logging.WARNING, logger="core.bm_cli.process_containment"):
        assert reap_run(run) == []

    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


def test_reaping_logs_the_pids_it_terminated(
    started: list[psutil.Process], caplog: pytest.LogCaptureFixture,
) -> None:
    run = ShellRun(tag=uuid.uuid4().hex, started_at=time.time())
    pid = _run_tagged(_DETACH, run)
    started.append(psutil.Process(pid))

    with caplog.at_level(logging.WARNING, logger="core.bm_cli.process_containment"):
        assert reap_run(run) == [pid]

    assert _gone(pid)
    assert any(
        record.levelno == logging.WARNING and f"terminated pids=[{pid}]" in record.getMessage()
        for record in caplog.records
    )
