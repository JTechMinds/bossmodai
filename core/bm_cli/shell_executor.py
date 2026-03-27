"""Sandboxed native shell command executor.

Executes real shell commands (npm, pip, python, curl, etc.) with timeout
enforcement, output truncation, and environment sanitization. Commands are
parsed via shlex.split() and run without a shell (shell=False) to prevent
shell injection.
"""

import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Environment allowlist ────────────────────────────────────────────
# Only these environment variables are forwarded to child processes.
# Everything else (API keys, tokens, secrets) is stripped.

_SAFE_ENV_NAMES: set[str] = {
    # System fundamentals
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    # Locale
    "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE",
    # Python
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    # Node.js
    "NODE_PATH", "NODE_ENV", "NPM_CONFIG_PREFIX",
    # Go / Java / Rust
    "GOPATH", "GOROOT", "JAVA_HOME",
    "CARGO_HOME", "RUSTUP_HOME",
    # Display (needed for GUI tools that agents might invoke)
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
    # Misc safe
    "TZ", "TMPDIR", "TEMP", "TMP",
}


@dataclass(frozen=True, slots=True)
class ShellExecutionResult:
    """Result of a native shell command execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def _sanitize_env(cwd: Path) -> dict[str, str]:
    """Build a sanitized environment from the current process env.

    Keeps only variables present in *_SAFE_ENV_NAMES* and overrides
    ``HOME`` to the agent's working directory so that tools like npm/pip
    resolve configs relative to the workspace.
    """
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_NAMES}
    env["HOME"] = str(cwd)
    return env


def _truncate(text: str, max_bytes: int) -> str:
    """Truncate *text* to *max_bytes* (UTF-8), appending a notice if trimmed."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    total = len(encoded)
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return f"{truncated}\n[truncated — {total} bytes total]"


def execute_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int = 30,
    max_output_bytes: int = 65_536,
) -> ShellExecutionResult:
    """Execute a native shell command and return the result.

    The command is parsed via :func:`shlex.split` and run as a list
    (``shell=False``) to prevent shell injection.  The process runs with
    the given *cwd* and a sanitized environment that strips secrets and
    overrides ``HOME`` to the workspace directory.

    Parameters
    ----------
    command:
        The shell command string to execute.
    cwd:
        Working directory for the child process. Also used as ``HOME``.
    timeout_seconds:
        Maximum wall-clock seconds before the process is killed.
    max_output_bytes:
        Stdout and stderr are each truncated to this many bytes.

    Returns
    -------
    ShellExecutionResult
        Structured result with exit code, captured output, timeout flag,
        and wall-clock duration in milliseconds.
    """
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return ShellExecutionResult(
            exit_code=1,
            stdout="",
            stderr=f"Failed to parse command: {exc}",
            timed_out=False,
            duration_ms=0,
        )

    if not args:
        return ShellExecutionResult(
            exit_code=1,
            stdout="",
            stderr="Empty command",
            timed_out=False,
            duration_ms=0,
        )

    sanitized_env = _sanitize_env(cwd)
    start = time.monotonic()

    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            env=sanitized_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        stdout = _truncate(proc.stdout, max_output_bytes)
        stderr = _truncate(proc.stderr, max_output_bytes)

        logger.info(
            "shell command=%r exit_code=%d duration_ms=%d",
            command,
            proc.returncode,
            duration_ms,
        )

        return ShellExecutionResult(
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            duration_ms=duration_ms,
        )

    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = _truncate(exc.stdout or "", max_output_bytes) if exc.stdout else ""
        stderr = _truncate(exc.stderr or "", max_output_bytes) if exc.stderr else ""

        logger.warning(
            "shell command=%r timed out after %ds", command, timeout_seconds,
        )

        return ShellExecutionResult(
            exit_code=124,
            stdout=stdout,
            stderr=stderr or f"Command timed out after {timeout_seconds}s",
            timed_out=True,
            duration_ms=duration_ms,
        )

    except FileNotFoundError:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("shell command=%r not found: %s", command, args[0])

        return ShellExecutionResult(
            exit_code=127,
            stdout="",
            stderr=f"Command not found: {args[0]}",
            timed_out=False,
            duration_ms=duration_ms,
        )

    except PermissionError:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("shell command=%r permission denied: %s", command, args[0])

        return ShellExecutionResult(
            exit_code=126,
            stdout="",
            stderr=f"Permission denied: {args[0]}",
            timed_out=False,
            duration_ms=duration_ms,
        )

    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("shell command=%r unexpected error", command)

        return ShellExecutionResult(
            exit_code=1,
            stdout="",
            stderr=f"Unexpected error: {exc}",
            timed_out=False,
            duration_ms=duration_ms,
        )
