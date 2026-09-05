"""Sandboxed native shell command executor.

Executes real shell commands (npm, pip, python, curl, etc.) with timeout
enforcement, output truncation, environment sanitization, and a path jail.
Commands are parsed via shlex.split() and run without a shell (``shell=False``)
to prevent shell injection.

The path jail (HA-SEC-P0-03) inspects argv tokens that look like filesystem
paths and rejects any that resolve outside the allowed roots (agent workspace,
the projects mount, and any operator-configured extra host roots). Approval
does not bypass this check.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.host_roots import allowed_workspace_roots, is_within_roots

logger = logging.getLogger(__name__)

# Permission-denied style exit: the command was not started.
PATH_JAIL_DENIED_EXIT_CODE = 126

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
    denied_by_path_jail: bool = False


class PathJailError(ValueError):
    """Raised when an argv path token resolves outside the allowed roots."""


def allowed_shell_roots(agent_storage_key: str) -> tuple[Path, ...]:
    """Return the real filesystem roots a native shell command may touch.

    Roots are the agent's personal workspace, the shared projects mount, and
    any operator-configured extra host roots. ``artifacts/db_backups`` and
    other agents' workspaces stay outside the jail.
    """
    return allowed_workspace_roots(agent_storage_key)


def _looks_like_path(token: str) -> bool:
    """Return True when *token* should be treated as a filesystem path."""
    if not token or token == "-":
        return False
    if token.startswith("~"):
        return True
    if token in {".", ".."}:
        return True
    if token.startswith("./") or token.startswith("../"):
        return True
    if token.startswith("/") or token.startswith("\\"):
        return True
    if "/" in token or "\\" in token:
        return True
    return False


def _path_candidates_from_token(token: str) -> list[str]:
    """Extract path-like payloads from an argv token.

    Handles bare paths, ``--flag=/abs/path``, and attached forms like
    ``-f/etc/passwd``. Flag-only tokens are ignored.
    """
    if not token or token == "-":
        return []
    if token.startswith("-"):
        if "=" in token:
            return _path_candidates_from_token(token.split("=", 1)[1])
        for index, char in enumerate(token):
            if char in "/~":
                return [token[index:]]
        return []
    return [token]


def _resolve_user_path(token: str, cwd: Path) -> Path:
    """Resolve a user-supplied path token against *cwd*.

    ``~`` expands to *cwd* (the sanitized ``HOME``). ``~otheruser`` is
    rejected — the jail must not follow the host passwd database.
    """
    if token.startswith("~"):
        rest = token[1:]
        if rest.startswith("/") or rest.startswith("\\"):
            rest = rest[1:]
        elif rest:
            raise PathJailError(
                f"Path jail: {token!r} is not allowed (~user home expansion is disabled)"
            )
        return (cwd / rest).resolve() if rest else cwd.resolve()

    path = Path(token)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def resolve_jailed_path(token: str, *, cwd: Path) -> Path | None:
    """Return the resolved path a token refers to, or None if it is not a path.

    Bare words that do not exist under *cwd* are treated as non-paths (e.g.
    ``echo hello``). Bare names that exist — including symlinks — are resolved
    so a workspace symlink cannot point at ``/etc/passwd``.
    """
    if _looks_like_path(token):
        return _resolve_user_path(token, cwd)
    candidate = cwd / token
    try:
        if candidate.is_symlink() or candidate.exists():
            return candidate.resolve()
    except OSError:
        return None
    return None


def assert_argv_within_path_jail(
    args: Sequence[str],
    *,
    cwd: Path,
    allowed_roots: Sequence[Path],
) -> None:
    """Reject argv path tokens that resolve outside *allowed_roots*.

    ``args[0]`` (the executable) is not jailed: binaries live on ``PATH``
    outside the workspace. File operands and option values are jailed.
    """
    cwd_resolved = Path(cwd).resolve()
    roots = tuple(Path(root).resolve() for root in allowed_roots) or (cwd_resolved,)

    if not is_within_roots(cwd_resolved, roots):
        raise PathJailError(
            f"Path jail: working directory {str(cwd_resolved)!r} is outside "
            "the allowed workspace roots"
        )

    for raw_token in args[1:]:
        for candidate in _path_candidates_from_token(raw_token):
            try:
                resolved = resolve_jailed_path(candidate, cwd=cwd_resolved)
            except PathJailError:
                raise
            except OSError as exc:
                raise PathJailError(
                    f"Path jail: cannot resolve {candidate!r}: {exc}"
                ) from exc
            if resolved is None:
                continue
            if not is_within_roots(resolved, roots):
                raise PathJailError(
                    f"Path jail: {candidate!r} resolves outside the allowed "
                    "workspace roots"
                )


def _path_jail_denied_result(message: str) -> ShellExecutionResult:
    return ShellExecutionResult(
        exit_code=PATH_JAIL_DENIED_EXIT_CODE,
        stdout="",
        stderr=message,
        timed_out=False,
        duration_ms=0,
        denied_by_path_jail=True,
    )


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
    allowed_roots: Sequence[Path] | None = None,
) -> ShellExecutionResult:
    """Execute a native shell command and return the result.

    The command is parsed via :func:`shlex.split` and run as a list
    (``shell=False``) to prevent shell injection.  The process runs with
    the given *cwd* and a sanitized environment that strips secrets and
    overrides ``HOME`` to the workspace directory.

    Path-like argv tokens are resolved and must stay inside *allowed_roots*
    (default: *cwd* only). This check runs even for previously approved
    commands — approval is not a path-jail bypass.

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
    allowed_roots:
        Real directories the command may read or write. Defaults to *cwd*.

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

    roots = tuple(allowed_roots) if allowed_roots else (Path(cwd),)
    try:
        assert_argv_within_path_jail(args, cwd=Path(cwd), allowed_roots=roots)
    except PathJailError as exc:
        logger.warning("shell command=%r denied by path jail: %s", command, exc)
        return _path_jail_denied_result(str(exc))

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
