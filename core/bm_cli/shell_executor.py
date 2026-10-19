"""Sandboxed native shell command executor.

Executes real shell commands (npm, pip, python, curl, etc.) with timeout
enforcement, output truncation, environment sanitization, and a path jail.
Commands are parsed via shlex.split() and run without a shell (``shell=False``)
to prevent shell injection. Scripts (pipes, connectors, redirects; see
:mod:`core.bm_cli.shell_script`) run as chains of such processes, wired by
this module, never by a shell.

The path jail (HA-SEC-P0-03) inspects argv tokens that look like filesystem
paths and rejects any that resolve outside the allowed roots (agent workspace,
the projects mount, and any operator-configured extra host roots). Approval
does not bypass this check.

No process started by a shell command outlives it (see
:mod:`core.bm_cli.process_containment`): every spawned process carries the
run's ``BOSSMOD_SHELL_RUN`` tag, and whatever still carries it when the run
ends is terminated, except pipelines started with ``&``, which
:mod:`core.bm_cli.background_processes` owns under a tag of their own.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import shlex
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from core.bm_cli.host_roots import allowed_workspace_roots, is_within_roots
from core.bm_cli.process_containment import RUN_TAG_ENV, ShellRun, contained_run
from core.bm_cli.shell_script import Connector, Pipeline, Redirect, ShellScript, Word
from core.loop_breathing import (
    SHELL_WORKER_ENV,
    in_shell_worker_process,
    on_request_loop,
    shell_is_long,
    shell_uses_worker_process,
)

if TYPE_CHECKING:
    from core.bm_cli.background_processes import BackgroundOwner

logger = logging.getLogger(__name__)

# Permission-denied style exit: the command was not started.
PATH_JAIL_DENIED_EXIT_CODE = 126

# The one redirect target allowed outside the jail: it discards every write
# and reads empty, so it can neither leak nor change host data.
NULL_DEVICE = Path(os.devnull)

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
    """Result of a native shell command execution.

    ``started_process_ids`` names the ``agent_processes`` rows a script's
    ``&`` pipelines created, in order; empty for everything else.
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    denied_by_path_jail: bool = False
    started_process_ids: tuple[str, ...] = ()


class PathJailError(ValueError):
    """Raised when an argv path token resolves outside the allowed roots."""


class ShellOnRequestLoopError(RuntimeError):
    """A long shell wait ran on the serve loop.

    Needs, channel reads, and WebSocket paint share that loop. The wait
    belongs on a worker thread, and a stall-or-backstop wait belongs in a
    child process.
    """


def allowed_shell_roots(agent_storage_key: str) -> tuple[Path, ...]:
    """Return the real filesystem roots a native shell command may touch.

    Roots are the agent's personal workspace, its floor's projects folder
    (none on vacation), and any operator-configured extra host roots.
    ``artifacts/db_backups``, other agents' workspaces, and other floors'
    folders stay outside the jail.
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


def path_candidates_from_token(token: str) -> list[str]:
    """Extract path-like payloads from an argv token.

    Handles bare paths, ``--flag=/abs/path``, and attached forms like
    ``-f/etc/passwd``. Flag-only tokens are ignored.

    A ``-``-led token is option-shaped only when ``-`` (or ``--``) is
    followed by a letter or digit. Getopt-style tools never read other
    shapes (``---x/…``, ``--/x``, ``-./x``, ``-/x``) as an option carrying
    an attached path, so they are operands and are returned whole, to be
    jailed as cwd-relative paths (``..`` is still normalized).
    """
    if not token or token in {"-", "--"}:
        return []
    option_shaped = token.startswith("-") and (
        token[1].isalnum() or (token[1] == "-" and len(token) > 2 and token[2].isalnum())
    )
    if option_shaped:
        if "=" in token:
            return path_candidates_from_token(token.split("=", 1)[1])
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
        for candidate in path_candidates_from_token(raw_token):
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


def _truncate(text: str | bytes, max_bytes: int) -> str:
    """Truncate *text* to *max_bytes* (UTF-8), appending a notice if trimmed.

    POSIX ``TimeoutExpired`` stdout and stderr are ``bytes`` even when the
    child was started in text mode. Decode those as UTF-8 with replacement
    before measuring and trimming.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    total = len(encoded)
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return f"{truncated}\n[truncated — {total} bytes total]"


def _redact_injected_secrets(text: str, extra_env: dict[str, str] | None) -> str:
    """Strip Nest git / GH token values from captured output before it leaves."""
    from core.bm_cli.secret_env import redact_secret_env_values

    return redact_secret_env_values(text, extra_env)


def _shell_process_main(conn: Any, payload: dict[str, Any]) -> None:
    """Run one shell command or script in a child process and send the result back."""
    os.environ[SHELL_WORKER_ENV] = "1"
    try:
        raw_roots = payload.get("allowed_roots")
        roots = tuple(Path(item) for item in raw_roots) if raw_roots else None
        # The parent's run, so this child tags and reaps under the same tag.
        run = ShellRun(**payload["run"])
        if "script" in payload:
            result = execute_shell_script(
                payload["script"],
                cwd=Path(str(payload["cwd"])),
                timeout_seconds=int(payload["timeout_seconds"]),
                max_output_bytes=int(payload["max_output_bytes"]),
                allowed_roots=roots or (),
                extra_env=payload["extra_env"],
                run=run,
                background=payload["background"],
            )
        else:
            result = execute_shell_command(
                str(payload["command"]),
                cwd=Path(str(payload["cwd"])),
                timeout_seconds=int(payload["timeout_seconds"]),
                max_output_bytes=int(payload["max_output_bytes"]),
                allowed_roots=roots,
                extra_env=payload.get("extra_env"),
                run=run,
            )
        conn.send(result)
    except Exception as exc:
        conn.send(exc)
    finally:
        conn.close()


def _run_shell_in_worker_thread(run: Callable[[], ShellExecutionResult]) -> ShellExecutionResult:
    """Run one shell (command or script) on this thread when the child process cannot start.

    The caller is already off the serve loop. The env flag stops another spawn.
    """
    previous = os.environ.get(SHELL_WORKER_ENV)
    os.environ[SHELL_WORKER_ENV] = "1"
    try:
        return run()
    finally:
        if previous is None:
            os.environ.pop(SHELL_WORKER_ENV, None)
        else:
            os.environ[SHELL_WORKER_ENV] = previous


def _execute_shell_in_process(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    allowed_roots: Sequence[Path] | None,
    extra_env: dict[str, str] | None,
    run: ShellRun,
) -> ShellExecutionResult:
    """Wait for a stall-or-backstop shell in a child process.

    The caller is a worker thread, not the serve loop. The child runs the
    same executor with ``BOSSMOD_SHELL_WORKER`` set so it does not spawn again.
    ``run`` crosses the process boundary as a plain dict, so the child tags
    its processes with the caller's tag and the caller's reap covers a child
    it had to kill.
    """
    payload = {
        "command": command,
        "cwd": str(cwd),
        "timeout_seconds": int(timeout_seconds),
        "max_output_bytes": int(max_output_bytes),
        "allowed_roots": [str(root) for root in allowed_roots] if allowed_roots else None,
        "extra_env": extra_env,
        "run": {"tag": run.tag, "started_at": run.started_at},
    }

    def fallback() -> ShellExecutionResult:
        return execute_shell_command(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            allowed_roots=allowed_roots,
            extra_env=extra_env,
            run=run,
        )

    return _wait_in_child_process(payload, timeout_seconds, fallback)


def _wait_in_child_process(
    payload: dict[str, Any],
    timeout_seconds: int,
    fallback: Callable[[], ShellExecutionResult],
) -> ShellExecutionResult:
    """Run *payload* in a spawned child and wait; *fallback* runs here if the child dies early."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_shell_process_main, args=(child, payload))
    proc.start()
    child.close()
    message: object
    try:
        if not parent.poll(float(timeout_seconds) + 30.0):
            proc.kill()
            return ShellExecutionResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout_seconds}s",
                timed_out=True,
                duration_ms=int(timeout_seconds * 1000),
            )
        try:
            message = parent.recv()
        except EOFError:
            logger.warning(
                "shell worker process exited before a result; waiting on the worker thread"
            )
            return _run_shell_in_worker_thread(fallback)
    finally:
        parent.close()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
    if isinstance(message, ShellExecutionResult):
        return message
    if isinstance(message, BaseException):
        return ShellExecutionResult(
            exit_code=1,
            stdout="",
            stderr=f"Unexpected error: {message}",
            timed_out=False,
            duration_ms=0,
        )
    return ShellExecutionResult(
        exit_code=1,
        stdout="",
        stderr="Unexpected error: shell worker returned nothing",
        timed_out=False,
        duration_ms=0,
    )


def execute_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int = 30,
    max_output_bytes: int = 65_536,
    allowed_roots: Sequence[Path] | None = None,
    extra_env: dict[str, str] | None = None,
    run: ShellRun | None = None,
) -> ShellExecutionResult:
    """Execute a native shell command and return the result.

    The command is parsed via :func:`shlex.split` and run as a list
    (``shell=False``) to prevent shell injection.  The process runs with
    the given *cwd* and a sanitized environment that strips secrets and
    overrides ``HOME`` to the workspace directory.

    Path-like argv tokens are resolved and must stay inside *allowed_roots*
    (default: *cwd* only). This check runs even for previously approved
    commands — approval is not a path-jail bypass.

    Every command (not only ``git``) runs with ``GIT_CEILING_DIRECTORIES``
    set to the parents of the jail roots, so git — including git spawned by
    tools such as ``uv`` or ``npm`` — stops repository discovery at the top
    of each root instead of walking up into the application repository.
    The value overrides any ``GIT_CEILING_DIRECTORIES`` in *extra_env*.

    No process started by the command outlives it: the command runs with
    ``BOSSMOD_SHELL_RUN`` set to the run's tag (overriding *extra_env*), and
    every process still carrying that tag when the call returns is
    terminated, including ones that detached into a new session.

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
    extra_env:
        Variables added on top of the sanitized environment; their values
        are redacted from the captured output.
    run:
        The shell run this command belongs to. ``None`` (every external
        caller) starts and owns a new run; the worker-process path passes the
        parent's run so both sides share one tag.

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
    if extra_env:
        sanitized_env.update(extra_env)
    # Git discovery must not climb out of a jail root into an enclosing repo
    # (``/me`` sits inside the app checkout). Set last so no caller widens it.
    sanitized_env["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(
        sorted({str(Path(root).resolve().parent) for root in roots})
    )
    if Path(args[0]).name.lower() in {"git", "git.exe"}:
        sanitized_env.setdefault("GIT_TERMINAL_PROMPT", "0")
        sanitized_env.setdefault("GCM_INTERACTIVE", "never")

    long_wait = shell_is_long(timeout_seconds)
    if long_wait and on_request_loop() and not in_shell_worker_process():
        raise ShellOnRequestLoopError(
            f"shell timeout {timeout_seconds}s must run off the request loop"
        )
    with contained_run(run) as active:
        # Set last, beside GIT_CEILING_DIRECTORIES, so extra_env cannot
        # replace the tag that lets the reap find detached processes.
        sanitized_env[RUN_TAG_ENV] = active.tag
        if shell_uses_worker_process(timeout_seconds) and not in_shell_worker_process():
            return _execute_shell_in_process(
                command,
                cwd=Path(cwd),
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                allowed_roots=roots,
                extra_env=extra_env,
                run=active,
            )

        start = time.monotonic()

        try:
            proc = subprocess.run(
                args,
                cwd=str(cwd),
                env=sanitized_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            stdout = _redact_injected_secrets(_truncate(proc.stdout, max_output_bytes), extra_env)
            stderr = _redact_injected_secrets(_truncate(proc.stderr, max_output_bytes), extra_env)

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
            stdout = _redact_injected_secrets(
                _truncate(exc.stdout or "", max_output_bytes) if exc.stdout else "",
                extra_env,
            )
            stderr = _redact_injected_secrets(
                _truncate(exc.stderr or "", max_output_bytes) if exc.stderr else "",
                extra_env,
            )

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


def resolve_redirect_target(token: str, *, cwd: Path, allowed_roots: Sequence[Path]) -> Path:
    """Resolve a script redirect's file and require it inside the jail.

    Unlike an argv operand, a redirect target is always a path, even when
    the file does not exist yet (``> new.txt``). The exact token
    ``os.devnull`` is the one target outside the jail, because it discards
    every write and reads empty.

    Args:
        token: The target word (real or cwd-relative; ``~`` is *cwd*).
        cwd: The script's real working directory.
        allowed_roots: The jail roots.

    Returns:
        The resolved real path, or :data:`NULL_DEVICE` unresolved.

    Raises:
        PathJailError: The target uses ``~user``, cannot be resolved, or
            resolves outside *allowed_roots*.
    """
    # Exact token only: /dev/zero, /dev/../x and symlinks to it stay jailed.
    if token == os.devnull:
        return NULL_DEVICE
    try:
        resolved = _resolve_user_path(token, Path(cwd).resolve())
    except OSError as exc:
        raise PathJailError(f"Path jail: cannot resolve {token!r}: {exc}") from exc
    if not is_within_roots(resolved, tuple(allowed_roots)):
        raise PathJailError(f"Path jail: {token!r} resolves outside the allowed workspace roots")
    return resolved


def assert_script_within_path_jail(script: ShellScript, *, cwd: Path, allowed_roots: Sequence[Path]) -> None:
    """Jail-check every argv and redirect target of *script*, as the executor does first.

    Raises:
        PathJailError: The first argv operand or redirect target outside
            *allowed_roots* (or the cwd itself outside them).
    """
    for command in script.commands():
        assert_argv_within_path_jail([word.text for word in command.argv], cwd=cwd, allowed_roots=allowed_roots)
        for redirect in command.redirects:
            if redirect.target is not None:
                resolve_redirect_target(redirect.target.text, cwd=cwd, allowed_roots=allowed_roots)


def execute_shell_script(
    script: ShellScript,
    *,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    allowed_roots: Sequence[Path],
    extra_env: Sequence[Mapping[str, str]],
    run: ShellRun | None = None,
    background: BackgroundOwner | None = None,
) -> ShellExecutionResult:
    """Run a parsed script as chains of ``shell=False`` processes; never a shell.

    Every argv and every redirect target is checked against the path jail
    before anything starts. Each pipeline is a ``Popen`` chain: stdout feeds
    the next stdin, and the parent closes its pipe copies so SIGPIPE
    reaches a writer whose reader exited. Redirect files are opened here
    (``>`` truncates, ``>>`` appends, ``<`` reads); ``2>&1`` sends stderr
    where stdout goes at that point; ``&>`` sends both to the file.

    Connectors follow bash: ``&&`` runs the next pipeline on status 0,
    ``||`` on non-zero, ``;`` always; a skipped pipeline leaves the status
    unchanged. A pipeline's status is its last command's, and the script's
    is the last run pipeline's. A pipeline followed by ``&`` (when it runs
    at all) is launched through :func:`core.bm_cli.background_processes.launch`
    instead of waited for: its status is 0, one ``Started in background``
    line goes to the captured stdout, and the next pipeline runs as after
    ``;``. Unlike bash, ``a && b &`` backgrounds only ``b``: ``&`` applies to
    the one pipeline before it. One deadline covers the whole script; on
    expiry every live process is killed and the exit is 124. The captured
    stdout (each pipeline's final, unredirected stdout) and stderr are
    capped at *max_output_bytes* each and stripped of injected secrets.

    No process started by the script outlives it, except its ``&``
    pipelines: every command runs with ``BOSSMOD_SHELL_RUN`` set to the
    run's tag (no ``NAME=value`` assignment can override it), and every
    process still carrying that tag when the call returns is terminated,
    including ones that detached. An ``&`` pipeline carries a tag of its
    own, recorded on its ``agent_processes`` row.

    Args:
        script: The script with globs already expanded and virtual paths
            rewritten (no word may still have ``glob=True``).
        cwd: Real working directory; also ``HOME``.
        timeout_seconds: The whole script's wall-clock budget.
        max_output_bytes: Cap for captured stdout and for stderr.
        allowed_roots: Real directories the script may touch.
        extra_env: One mapping per simple command, in source order: the
            env added on top of the sanitized env for that command (agent
            git identity, nest-git auth). Its values are redacted from the
            output. The command's own ``NAME=value`` assignments apply
            after it; ``GIT_CEILING_DIRECTORIES`` and ``BOSSMOD_SHELL_RUN``
            are set last.
        run: The shell run this script belongs to. ``None`` (every external
            caller) starts and owns a new run; the worker-process path
            passes the parent's run so both sides share one tag.
        background: Who the script's ``&`` pipelines belong to; required
            when the script has one, ignored otherwise. It crosses into the
            worker child with the script, so a launch there records its row
            from the child.

    Returns:
        The combined result. A jail refusal returns ``denied_by_path_jail``
        and nothing runs. A missing program is status 127 for that
        command, an unreadable redirect file status 1, as in bash.
        ``started_process_ids`` lists the background rows created.

    Raises:
        ValueError: *extra_env* does not have one entry per command, a word
            still needs glob expansion, *allowed_roots* is empty, or the
            script has an ``&`` pipeline and *background* is missing or does
            not name one command per script item.
        ShellOnRequestLoopError: A long wait was asked on the serve loop.
    """
    commands = script.commands()
    if len(extra_env) != len(commands):
        raise ValueError(f"extra_env has {len(extra_env)} entries for {len(commands)} commands")
    if any(word.glob for command in commands for word in command.argv):
        raise ValueError("expand globs before executing a script")
    roots = tuple(Path(root) for root in allowed_roots)
    if not roots:
        raise ValueError("a script needs at least one allowed root")
    if any(connector == "&" for _pipeline, connector in script.items):
        if background is None:
            raise ValueError("a script with & needs the agent its background processes belong to")
        if len(background.item_commands) != len(script.items):
            raise ValueError(
                f"background names {len(background.item_commands)} commands for {len(script.items)} pipelines"
            )
    try:
        assert_script_within_path_jail(script, cwd=Path(cwd), allowed_roots=roots)
    except PathJailError as exc:
        logger.warning("shell script denied by path jail: %s", exc)
        return _path_jail_denied_result(str(exc))

    if shell_is_long(timeout_seconds) and on_request_loop() and not in_shell_worker_process():
        raise ShellOnRequestLoopError(f"shell timeout {timeout_seconds}s must run off the request loop")
    envs = [dict(item) for item in extra_env]
    with contained_run(run) as active:
        if shell_uses_worker_process(timeout_seconds) and not in_shell_worker_process():
            payload = {
                "script": script,
                "cwd": str(cwd),
                "timeout_seconds": int(timeout_seconds),
                "max_output_bytes": int(max_output_bytes),
                "allowed_roots": [str(root) for root in roots],
                "extra_env": envs,
                "run": {"tag": active.tag, "started_at": active.started_at},
                "background": background,
            }
            return _wait_in_child_process(
                payload,
                timeout_seconds,
                lambda: _run_script(
                    script, Path(cwd), timeout_seconds, max_output_bytes, roots, envs,
                    run=active, background=background,
                ),
            )
        return _run_script(
            script, Path(cwd), timeout_seconds, max_output_bytes, roots, envs, run=active, background=background,
        )


def _run_script(
    script: ShellScript,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    roots: tuple[Path, ...],
    envs: list[dict[str, str]],
    *,
    run: ShellRun,
    background: BackgroundOwner | None,
) -> ShellExecutionResult:
    start = time.monotonic()
    deadline = start + timeout_seconds
    ceiling = os.pathsep.join(sorted({str(root.resolve().parent) for root in roots}))
    status = 0
    timed_out = False
    started: list[str] = []
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        first = 0
        previous: Connector | None = None
        for index, (pipeline, connector) in enumerate(script.items):
            count = len(pipeline.commands)
            pipeline_envs = envs[first:first + count]
            runs = _runs_after(previous, status)
            if runs and connector == "&":
                # execute_shell_script refused an & script without an owner.
                if background is None:
                    raise ValueError("a script with & needs the agent its background processes belong to")
                status = _launch_background(
                    pipeline, pipeline_envs, background, background.item_commands[index],
                    cwd=cwd, roots=roots, ceiling=ceiling, out=out, err=err, started=started,
                )
            elif runs:
                status, timed_out = _run_pipeline(
                    pipeline, pipeline_envs, cwd=cwd, roots=roots, ceiling=ceiling,
                    out=out, err=err, deadline=deadline, run_tag=run.tag,
                )
                if timed_out:
                    break
            first += count
            previous = connector
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = _captured(out, max_output_bytes, envs)
        stderr = _captured(err, max_output_bytes, envs)
    if timed_out:
        logger.warning("shell script timed out after %ds", timeout_seconds)
        return ShellExecutionResult(
            exit_code=124,
            stdout=stdout,
            stderr=stderr or f"Command timed out after {timeout_seconds}s",
            timed_out=True,
            duration_ms=duration_ms,
            started_process_ids=tuple(started),
        )
    logger.info("shell script commands=%d exit_code=%d duration_ms=%d", len(envs), status, duration_ms)
    return ShellExecutionResult(
        exit_code=status, stdout=stdout, stderr=stderr, timed_out=False, duration_ms=duration_ms,
        started_process_ids=tuple(started),
    )


def _runs_after(previous: Connector | None, status: int) -> bool:
    """Whether the next pipeline runs, by bash's rules for the connector before it.

    ``&`` is ``;`` for what follows: the backgrounded pipeline's own status
    (0, or its start failure) gates nothing.
    """
    if previous is None or previous in (";", "&"):
        return True
    return status == 0 if previous == "&&" else status != 0


def _launch_background(
    pipeline: Pipeline,
    envs: list[dict[str, str]],
    owner: BackgroundOwner,
    command: str,
    *,
    cwd: Path,
    roots: tuple[Path, ...],
    ceiling: str,
    out: IO[bytes],
    err: IO[bytes],
    started: list[str],
) -> int:
    """Launch one ``&`` pipeline and say so in the capture; return its status.

    0 when it started, with the ``Started in background`` line on stdout and
    its id appended to *started*; otherwise the start failure's status, with
    the reason on stderr (after any ``Command not found`` the spawn wrote).
    """
    from core.bm_cli.background_processes import BackgroundLaunchError, launch

    def start(run_tag: str, log_fd: int) -> tuple[list[subprocess.Popen[bytes]], int | None]:
        return _start_pipeline(
            pipeline, envs, cwd=cwd, roots=roots, ceiling=ceiling,
            stdout=log_fd, stderr=log_fd, err=err, run_tag=run_tag,
        )

    try:
        row = launch(owner=owner, command=command, start_pipeline=start)
    except BackgroundLaunchError as exc:
        os.write(err.fileno(), f"{exc}\n".encode())
        return exc.status
    started.append(row.id)
    os.write(
        out.fileno(),
        (
            f"Started in background: {row.short_id} (pid {row.pid}). "
            f"Logs: processes logs {row.short_id}. Stop: processes stop {row.short_id}.\n"
        ).encode(),
    )
    return 0


def _run_pipeline(
    pipeline: Pipeline,
    envs: list[dict[str, str]],
    *,
    cwd: Path,
    roots: tuple[Path, ...],
    ceiling: str,
    out: IO[bytes],
    err: IO[bytes],
    deadline: float,
    run_tag: str,
) -> tuple[int, bool]:
    """Start every command of one pipeline, then wait; return ``(status, timed_out)``."""
    procs, final = _start_pipeline(
        pipeline, envs, cwd=cwd, roots=roots, ceiling=ceiling,
        stdout=out.fileno(), stderr=err.fileno(), err=err, run_tag=run_tag,
    )
    try:
        for proc in procs:
            proc.wait(timeout=max(deadline - time.monotonic(), 0))
    except subprocess.TimeoutExpired:
        _kill_all(procs)
        return 124, True
    except BaseException:
        _kill_all(procs)
        raise
    if final is not None:
        return final, False
    return procs[-1].returncode, False


def _start_pipeline(
    pipeline: Pipeline,
    envs: list[dict[str, str]],
    *,
    cwd: Path,
    roots: tuple[Path, ...],
    ceiling: str,
    stdout: int,
    stderr: int,
    err: IO[bytes],
    run_tag: str,
) -> tuple[list[subprocess.Popen[bytes]], int | None]:
    """Start every command of one pipeline without waiting.

    Each command's stdout feeds the next one's stdin; the last writes to
    *stdout*, and every command's stderr goes to *stderr*, unless its own
    redirects say otherwise. Every pipe end this process held is closed
    before it returns, so a reader sees EOF and a writer whose reader exited
    gets SIGPIPE. A spawn failure's reason is written to *err*.

    Returns:
        The started processes, and the status when the LAST command could
        not start (None when it started). On any exception the started
        processes are killed first.
    """
    procs: list[subprocess.Popen[bytes]] = []
    final: int | None = None  # set when the last command could not start
    parent_fds: set[int] = set()  # pipe ends this process still holds
    try:
        stdin = subprocess.DEVNULL
        for index, command in enumerate(pipeline.commands):
            last = index == len(pipeline.commands) - 1
            command_stdout = stdout
            read_end: int | None = None
            if not last:
                read_end, command_stdout = os.pipe()
                parent_fds.update((read_end, command_stdout))
            proc, failed = _spawn(
                command.argv, command.assignments, command.redirects, envs[index],
                stdin=stdin, stdout=command_stdout, stderr=stderr,
                cwd=cwd, roots=roots, ceiling=ceiling, err=err, run_tag=run_tag,
            )
            if proc is not None:
                procs.append(proc)
            if last:
                final = failed
            # The parent's copies must close, or a reader never sees EOF and
            # a writer whose reader exited never gets SIGPIPE.
            for fd in (stdin, None if last else command_stdout):
                if fd is not None and fd in parent_fds:
                    os.close(fd)
                    parent_fds.discard(fd)
            stdin = read_end if read_end is not None else subprocess.DEVNULL
    except BaseException:
        _kill_all(procs)
        raise
    finally:
        for fd in parent_fds:
            os.close(fd)
    return procs, final


def _spawn(
    argv: Sequence[Word],
    assignments: Sequence[tuple[str, str]],
    redirects: Sequence[Redirect],
    extra: Mapping[str, str],
    *,
    stdin: int,
    stdout: int,
    stderr: int,
    cwd: Path,
    roots: tuple[Path, ...],
    ceiling: str,
    err: IO[bytes],
    run_tag: str,
) -> tuple[subprocess.Popen[bytes] | None, int | None]:
    """Start one command; return ``(process, None)`` or ``(None, status)`` when it could not start."""
    args = [word.text for word in argv]
    opened: list[int] = []
    try:
        for redirect in redirects:
            if redirect.mode == "dup_out":
                stderr = stdout
                continue
            if redirect.target is None:
                raise ValueError(f"{redirect.mode} redirect has no target")
            try:
                # Checked before the script started; checked again in case
                # the path changed (a swapped symlink) since then.
                path = resolve_redirect_target(redirect.target.text, cwd=cwd, allowed_roots=roots)
                fd = _open_redirect(redirect, path)
            except PathJailError as exc:
                os.write(err.fileno(), f"{exc}\n".encode())
                return None, PATH_JAIL_DENIED_EXIT_CODE
            except OSError as exc:
                os.write(err.fileno(), f"{redirect.target.text}: {exc.strerror}\n".encode())
                return None, 1
            opened.append(fd)
            if redirect.mode == "read":
                stdin = fd
            elif redirect.fd == "both":
                stdout = stderr = fd
            elif redirect.fd == 2:
                stderr = fd
            else:
                stdout = fd
        env = _sanitize_env(cwd)
        env.update(extra)
        env.update(dict(assignments))
        # Last, so neither the caller nor an assignment widens git discovery
        # or replaces the tag that lets the reap find detached processes.
        env["GIT_CEILING_DIRECTORIES"] = ceiling
        env[RUN_TAG_ENV] = run_tag
        if Path(args[0]).name.lower() in {"git", "git.exe"}:
            env.setdefault("GIT_TERMINAL_PROMPT", "0")
            env.setdefault("GCM_INTERACTIVE", "never")
        try:
            return subprocess.Popen(args, cwd=str(cwd), env=env, stdin=stdin, stdout=stdout, stderr=stderr), None
        except FileNotFoundError:
            os.write(err.fileno(), f"Command not found: {args[0]}\n".encode())
            return None, 127
        except PermissionError:
            os.write(err.fileno(), f"Permission denied: {args[0]}\n".encode())
            return None, 126
    finally:
        for fd in opened:
            os.close(fd)


def _open_redirect(redirect: Redirect, path: Path) -> int:
    if redirect.mode == "read":
        return os.open(path, os.O_RDONLY)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if redirect.mode == "append" else os.O_TRUNC)
    return os.open(path, flags, 0o666)


def _kill_all(procs: Sequence[subprocess.Popen[bytes]]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
    for proc in procs:
        proc.wait()


def _captured(capture: IO[bytes], max_output_bytes: int, envs: Sequence[dict[str, str]]) -> str:
    """The capture, capped, with every command's injected secret values redacted."""
    capture.seek(0)
    text = _truncate(capture.read(), max_output_bytes)
    for env in envs:
        text = _redact_injected_secrets(text, env)
    return text
