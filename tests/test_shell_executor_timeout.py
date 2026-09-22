"""Shell timeouts stay a timeout result when captured output is bytes."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from core.bm_cli.shell_executor import _truncate, execute_shell_command


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
