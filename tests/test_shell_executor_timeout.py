"""Shell timeouts stay a timeout result when captured output is bytes.

Also: no process a shell command starts outlives it, even one that detached
into its own session (core/bm_cli/process_containment.py).
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

import db
from core import config
from core.bm_cli.shell_executor import _truncate, execute_shell_command, execute_shell_script
from core.bm_cli.shell_script import parse_shell_script


def test_truncate_decodes_bytes_then_trims() -> None:
    assert _truncate(b"ok", 10) == "ok"
    assert _truncate("ok", 10) == "ok"
    assert _truncate(b"", 10) == ""
    assert _truncate(b"a\xffb", 10) == "a\ufffdb"

    trimmed = _truncate(b"x" * 50, 10)
    assert trimmed.startswith("x" * 10)
    assert "[truncated — 50 bytes total]" in trimmed

    trimmed_str = _truncate("y" * 50, 10)
    assert trimmed_str.startswith("y" * 10)
    assert "[truncated — 50 bytes total]" in trimmed_str


def _raise_timeout(stdout: str | bytes | None, stderr: str | bytes | None):
    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0] if args else "echo",
            kwargs.get("timeout", 1),
            output=stdout,
            stderr=stderr,
        )

    return _run


def test_timeout_expired_bytes_return_truncated_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = b"STDOUT-PREFIX-" + (b"x" * 500) + b"\xff"
    stderr = b"STDERR-PREFIX-" + (b"y" * 500)
    monkeypatch.setattr(
        "core.bm_cli.shell_executor.subprocess.run",
        _raise_timeout(stdout, stderr),
    )

    result = execute_shell_command(
        "echo hello",
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_bytes=16,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    assert "AttributeError" not in result.stdout
    assert "AttributeError" not in result.stderr
    assert "Unexpected error" not in result.stderr
    assert result.stdout.startswith("STDOUT-PREFIX-")
    # \xff decodes to U+FFFD (3 UTF-8 bytes) before the trim is measured.
    assert "[truncated — 517 bytes total]" in result.stdout
    assert result.stderr.startswith("STDERR-PREFIX-")
    assert "[truncated — 514 bytes total]" in result.stderr


def test_timeout_expired_str_output_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.bm_cli.shell_executor.subprocess.run",
        _raise_timeout("plain-out", "plain-err"),
    )
    short = execute_shell_command(
        "echo hello",
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_bytes=100,
    )
    assert short.timed_out is True
    assert short.exit_code == 124
    assert short.stdout == "plain-out"
    assert short.stderr == "plain-err"

    monkeypatch.setattr(
        "core.bm_cli.shell_executor.subprocess.run",
        _raise_timeout("o" * 40, "e" * 40),
    )
    long = execute_shell_command(
        "echo hello",
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_bytes=10,
    )
    assert long.timed_out is True
    assert long.exit_code == 124
    assert long.stdout.startswith("o" * 10)
    assert "[truncated — 40 bytes total]" in long.stdout
    assert long.stderr.startswith("e" * 10)
    assert "[truncated — 40 bytes total]" in long.stderr
    assert "AttributeError" not in long.stderr


def test_live_timeout_bytes_output_is_a_clean_timeout(tmp_path: Path) -> None:
    script = tmp_path / "hang.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            import time

            sys.stdout.buffer.write(b"OUT" + b"x" * 20 + bytes([255]))
            sys.stdout.buffer.flush()
            sys.stderr.buffer.write(b"ERR" + b"y" * 20)
            sys.stderr.buffer.flush()
            time.sleep(30)
            """
        ),
        encoding="utf-8",
    )

    result = execute_shell_command(
        "python3 hang.py",
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_bytes=8,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    assert "AttributeError" not in result.stderr
    assert "Unexpected error" not in result.stderr
    assert result.stdout.startswith("OUTxxxxx")
    assert "[truncated — 26 bytes total]" in result.stdout
    assert result.stderr.startswith("ERRyyyyy")
    assert "[truncated — 23 bytes total]" in result.stderr


# ─── containment: detached processes end with the command ───

# Starts `sleep 60` in its own session, records its pid, then exits (or
# hangs past the timeout when given a second argument).
_DETACH = textwrap.dedent(
    """\
    import subprocess
    import sys
    import time

    proc = subprocess.Popen(
        ["sleep", "60"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        handle.write(str(proc.pid))
    print("launched")
    if len(sys.argv) > 2:
        time.sleep(30)
    """
)


@pytest.fixture
def detach(tmp_path: Path) -> Iterator[Path]:
    """Write the detaching helper; kill a sleeper it left alive at teardown (ours only)."""
    (tmp_path / "detach.py").write_text(_DETACH, encoding="utf-8")
    pid_file = tmp_path / "sleeper.pid"
    yield pid_file
    if not pid_file.exists():
        return
    try:
        sleeper = psutil.Process(int(pid_file.read_text(encoding="utf-8")))
        # Only our own sleeper (cwd is this test's tmp_path): a pid the OS
        # reused for another process is never signalled.
        if sleeper.status() != psutil.STATUS_ZOMBIE and sleeper.cwd() == str(tmp_path):
            sleeper.kill()
    except psutil.NoSuchProcess:
        # Already reaped by the executor: the expected outcome.
        return


def _sleeper_gone(pid_file: Path, *, within: float = 5.0) -> bool:
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def test_a_detached_sleeper_dies_when_the_command_exits(tmp_path: Path, detach: Path) -> None:
    result = execute_shell_command("python3 detach.py sleeper.pid", cwd=tmp_path, timeout_seconds=10)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "launched" in result.stdout
    assert _sleeper_gone(detach)


def test_a_detached_sleeper_dies_when_the_command_times_out(tmp_path: Path, detach: Path) -> None:
    result = execute_shell_command("python3 detach.py sleeper.pid hang", cwd=tmp_path, timeout_seconds=2)

    assert result.timed_out is True
    assert result.exit_code == 124
    assert _sleeper_gone(detach)


def _script(tmp_path: Path, text: str, *, timeout: int = 10):
    script = parse_shell_script(text)
    return execute_shell_script(
        script,
        cwd=tmp_path,
        timeout_seconds=timeout,
        max_output_bytes=65536,
        allowed_roots=[tmp_path],
        extra_env=[{}] * len(script.commands()),
    )


def test_a_pipeline_whose_first_command_detaches_leaves_nothing(tmp_path: Path, detach: Path) -> None:
    result = _script(tmp_path, "python3 detach.py sleeper.pid | cat")

    assert result.exit_code == 0
    assert "launched" in result.stdout
    assert _sleeper_gone(detach)


def test_a_script_assignment_cannot_override_the_run_tag(tmp_path: Path, detach: Path) -> None:
    result = _script(tmp_path, "BOSSMOD_SHELL_RUN=x python3 detach.py sleeper.pid")

    assert result.exit_code == 0
    assert _sleeper_gone(detach)


@pytest.fixture
def worker_process_shell() -> Iterator[None]:
    """Route a 2s shell through the child-process path, as test_host_loop_breathing does."""
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    db.set_setting("llm_stall_timeout_seconds", "1", "llm")
    config.reload()
    yield
    db.close_connection()


def test_a_detached_sleeper_dies_on_the_worker_process_path(
    tmp_path: Path, detach: Path, worker_process_shell: None,
) -> None:
    from core.loop_breathing import shell_uses_worker_process

    assert shell_uses_worker_process(2) is True
    result = execute_shell_command("python3 detach.py sleeper.pid", cwd=tmp_path, timeout_seconds=2)

    assert result.exit_code == 0
    assert "launched" in result.stdout
    assert _sleeper_gone(detach)
