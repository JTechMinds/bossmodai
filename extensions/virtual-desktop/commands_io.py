"""Virtual Desktop — ``vm run``, ``vm launch``, ``vm upload`` and ``vm download``.

Also what every ``vm`` subcommand shares with ``commands``: the ``Call`` it
runs with, argument parsing, and the lines a desktop start adds.

Files move only by copy, between the agent's virtual folders (``/me``,
``/projects`` and host folders the boss allowed, resolved through core's path
jail) and the desktop; nothing from the host is mounted. A path outside them
raises core's ``PathOutsideRootsError`` before anything is done, so core
offers the boss its host-path consent card. The tar streams are ``archive``'s.
"""

from __future__ import annotations

import dataclasses
import math
import posixpath
import shlex
import tarfile
import tempfile
from pathlib import PurePosixPath

from core.bm_cli.results import success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import ResolvedCliPath, resolve_cli_path

from .archive import UnsafeArchive, checked_members, extract, pack_for_agent, top_name, unique_path
from .desktop_types import HOME, BootReport, DesktopHostLike, DesktopPathMissing
from .settings import DesktopDefaults, DesktopSettings

DOWNLOADS_VIRTUAL_DIR = "/me/downloads"
UPLOAD_DEFAULT_DIR = "~/Desktop"
# Copies are staged in memory up to this size, then on disk.
_SPOOL_MAX_BYTES = 8 * 1024 * 1024
RUN_TIMED_OUT_EXIT = 124
UPDATED_NOTE = (
    "desktop updated to a new version: your files in /home/agent were kept; "
    "programs you installed must be installed again"
)


class DesktopCommandError(Exception):
    """A refusal whose message (starting with its code) goes to the agent as is."""


@dataclasses.dataclass(frozen=True)
class Call:
    """One ``vm`` command being run, as its subcommand gets it.

    Attributes:
        ctx: The calling agent and its working directory.
        parsed: The parsed command (``args[0]`` is the subcommand).
        args: The arguments after the subcommand.
        body: The command body, if any.
        settings: The agent's desktop settings.
        boot: What starting the desktop first did; ``None`` when not started first.
        path: The virtual path ``path_arg`` resolved (upload's source,
            download's destination); ``None`` for other subcommands.
    """

    ctx: CliExecutionContext
    parsed: ParsedCliCommand
    args: tuple[str, ...]
    body: str | None
    settings: DesktopSettings
    boot: BootReport | None
    path: ResolvedCliPath | None

    @property
    def agent_id(self) -> str:
        """The calling agent's id."""
        return self.ctx.agent.id


def boot_notes(boot: BootReport | None) -> list[str]:
    """The lines a start adds: ``desktop started (6.1s)`` and, after an image update, what was kept."""
    if boot is None or not boot.started:
        return []
    notes = [f"desktop started ({boot.seconds:.1f}s)"]
    if boot.recreated:
        notes.append(UPDATED_NOTE)
    return notes


def no_args(call: Call, usage: str) -> None:
    """Refuse arguments to a subcommand that takes none.

    Raises:
        DesktopCommandError: ``USAGE`` naming ``usage``.
    """
    if call.args:
        raise DesktopCommandError(f"USAGE: {usage} (it takes no arguments)")


def positive_int(raw: str, what: str) -> int:
    """Parse a whole number of at least 1.

    Raises:
        ValueError: Anything else, naming ``what``.
    """
    message = f"{what} must be a whole number of at least 1; got {raw!r}"
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(message) from None
    if value < 1:
        raise ValueError(message)
    return value


def positive_seconds(raw: str, what: str) -> float:
    """Parse a positive, finite number of seconds.

    Raises:
        ValueError: Anything else, naming ``what``.
    """
    message = f"{what} must be a positive number of seconds; got {raw!r}"
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(message) from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(message)
    return value


def path_arg(sub: str, ctx: CliExecutionContext, args: tuple[str, ...]) -> ResolvedCliPath | None:
    """Resolve the virtual path ``vm upload`` copies from or ``vm download`` copies into.

    Called before the desktop is touched, so a path the agent may not use
    changes nothing: core turns the ``PathOutsideRootsError`` into the boss's
    host-path consent card, and once approved the path resolves under the
    granted host folder. Every resolved mount (``me``, ``projects``,
    ``host``) is accepted, as core's own file commands do.

    Args:
        sub: The subcommand.
        ctx: The calling agent and its working directory.
        args: The arguments after the subcommand.

    Returns:
        The resolved path; ``None`` for other subcommands or a wrong number
        of arguments (the subcommand then reports its usage).

    Raises:
        core.bm_cli.host_roots.PathOutsideRootsError: Outside every root
            the agent may use (it carries ``raw_path``).
        ValueError: A relative path escaping its root, or the virtual root
            ``/`` itself.
    """
    if sub not in ("upload", "download") or len(args) not in (1, 2):
        return None
    raw = args[0] if sub == "upload" else (args[1] if len(args) == 2 else DOWNLOADS_VIRTUAL_DIR)
    resolved = resolve_cli_path(ctx.agent.storage_key, ctx.cwd, raw)
    if resolved.real_path is None:
        raise ValueError(f"{resolved.virtual_path} is not a file or folder; use one under /me or /projects")
    return resolved


def command_text(args: tuple[str, ...], body: str | None, *, name: str) -> str:
    """Return the shell command of ``vm run`` / ``vm launch``.

    One argument is the whole command line, as typed inside quotes; several
    are re-quoted (``shlex.join``) so each stays one word, as the parser
    split them. The body, when given, is the command itself.

    Raises:
        DesktopCommandError: No command, or a different one in both places.
    """
    inline = args[0] if len(args) == 1 else shlex.join(args)
    in_body = (body or "").strip()
    if in_body and inline and in_body != inline:
        raise DesktopCommandError(f"USAGE: vm {name}: give the command either after {name} or in the body, not both")
    if not in_body and not inline:
        raise DesktopCommandError(f"USAGE: vm {name} <command> (quote it when it has spaces or shell operators)")
    return in_body or inline


def desktop_path(raw: str) -> str:
    """Return an absolute desktop path: ``~`` is the home folder, relative paths are under it.

    Raises:
        ValueError: The path is empty.
    """
    text = raw.strip()
    if not text:
        raise ValueError("a desktop path is empty")
    if text == "~" or text.startswith("~/"):
        text = HOME + text[1:]
    elif not text.startswith("/"):
        text = f"{HOME}/{text}"
    return posixpath.normpath(text)


def output_text(data: bytes, total: int) -> str:
    """Decode the start of one output stream; when the stream was longer, add core's marker with its full size."""
    text = data.decode("utf-8", errors="replace")
    return text if total <= len(data) else f"{text}\n[truncated — {total} bytes total]"


def run(host: DesktopHostLike, defaults: DesktopDefaults, call: Call) -> BossModCliResult:
    """``vm run <command>``: run a shell command on the desktop; exit code and output, no screenshot.

    A trailing ``&`` leaves a program running in the background (the
    result comes back at once). The result is ``ok`` only for exit 0, as
    for a native shell command.

    Raises:
        DesktopCommandError: Usage.
        DockerUnavailable, DesktopActionError: From the host.
    """
    command = command_text(call.args, call.body, name="run")
    outcome = host.run(call.agent_id, command, defaults.run_timeout_s)
    lines = [f"exit code: {outcome.exit_code}"]
    if outcome.exit_code == RUN_TIMED_OUT_EXIT:
        lines.append(f"timed out after {defaults.run_timeout_s}s; start programs that keep running with vm launch")
    sections = [("DESKTOP", lines + boot_notes(call.boot))]
    streams = (("STDOUT", outcome.stdout, outcome.stdout_size), ("STDERR", outcome.stderr, outcome.stderr_size))
    for title, stream, total in streams:
        text = output_text(stream, total)
        if text.strip():
            sections.append((title, [text.rstrip()]))
    result = success_result(
        command=call.parsed.raw,
        detail=f"Virtual Desktop: run (exit {outcome.exit_code})",
        kind="desktop",
        data={"exit_code": outcome.exit_code, "status_lines": []},
        sections=sections,
        cwd=call.ctx.cwd,
    )
    return dataclasses.replace(result, ok=outcome.exit_code == 0, exit_code=outcome.exit_code)


def launch(host: DesktopHostLike, call: Call) -> tuple[list[str], list[str]]:
    """``vm launch <command>``: start a program detached on the desktop.

    Returns:
        ``(result notes, operator status lines)`` for the screenshot that follows.

    Raises:
        DesktopCommandError: Usage.
        DockerUnavailable, DesktopActionError: From the host.
    """
    command = command_text(call.args, None, name="launch")
    host.launch(call.agent_id, command)
    return [f"launched: {command}"], [f"Opened {command.split()[0]} on the desktop"]


def upload(host: DesktopHostLike, call: Call) -> BossModCliResult:
    """``vm upload <file or folder> [<desktop folder>]``: copy a virtual file or folder (``call.path``) to the desktop.

    Everything copied is owned by the desktop's ``agent`` account. A file
    of the same name in the destination is replaced.

    Raises:
        DesktopCommandError: Usage, or ``NOT_FOUND`` (the source, or the
            desktop folder).
        DockerUnavailable, DesktopActionError: From the host.
    """
    args, source = call.args, call.path
    if len(args) not in (1, 2) or source is None or source.real_path is None:
        raise DesktopCommandError("USAGE: vm upload <file or folder> [<desktop folder>]")
    if not source.exists:
        raise DesktopCommandError(f"NOT_FOUND: {source.virtual_path} does not exist")
    dest = desktop_path(args[1] if len(args) == 2 else UPLOAD_DEFAULT_DIR)
    name = PurePosixPath(source.virtual_path).name
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES) as spool:
        files, total = pack_for_agent(spool, source.real_path, name)
        spool.seek(0)
        try:
            host.put_archive(call.agent_id, dest, spool)
        except DesktopPathMissing as exc:
            raise DesktopCommandError(f"NOT_FOUND: {exc}") from exc
    target = posixpath.join(dest, name)
    size = f"{total} bytes" if source.real_path.is_file() else f"{files} files, {total} bytes"
    return success_result(
        command=call.parsed.raw,
        detail="Virtual Desktop: upload",
        kind="desktop",
        data={"desktop_path": target, "status_lines": []},
        sections=[("DESKTOP", [f"uploaded: {source.virtual_path} → {target} ({size})", *boot_notes(call.boot)])],
        cwd=call.ctx.cwd,
    )


def download(host: DesktopHostLike, call: Call) -> BossModCliResult:
    """``vm download <desktop path> [<folder>]``: copy a desktop file or folder into a virtual folder (``call.path``).

    The destination folder (default ``/me/downloads``) is created when
    missing. The copy never replaces anything: an existing name gets
    `` (n)`` added, as Browser Vision's downloads do.

    Raises:
        DesktopCommandError: Usage, ``NOT_FOUND``, or ``UNSAFE_ARCHIVE``
            (anything but regular files and folders, or a name that leaves
            the destination).
        ValueError: The destination is a file.
        DockerUnavailable, DesktopActionError: From the host.
        OSError: Writing failed (what was written of this copy is removed).
    """
    args, dest = call.args, call.path
    if len(args) not in (1, 2) or dest is None or dest.real_path is None:
        raise DesktopCommandError("USAGE: vm download <desktop path> [<folder under /me or /projects>]")
    source = desktop_path(args[0])
    if dest.real_path.exists() and not dest.real_path.is_dir():
        raise ValueError(f"{dest.virtual_path} is a file, not a folder")
    try:
        chunks, _stat = host.get_archive(call.agent_id, source)
    except DesktopPathMissing as exc:
        raise DesktopCommandError(f"NOT_FOUND: {exc}") from exc
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES) as spool:
        for chunk in chunks:
            spool.write(chunk)
        spool.seek(0)
        try:
            tar = tarfile.open(fileobj=spool, mode="r:")
        except tarfile.TarError as exc:
            raise DesktopCommandError(f"UNSAFE_ARCHIVE: the copy from the desktop is not a readable archive ({exc})") from exc
        with tar:
            try:
                members = checked_members(tar.getmembers())
            except tarfile.TarError as exc:
                raise DesktopCommandError(f"UNSAFE_ARCHIVE: the copy from the desktop is not a readable archive ({exc})") from exc
            except UnsafeArchive as exc:
                raise DesktopCommandError(f"UNSAFE_ARCHIVE: {exc}") from exc
            dest.real_path.mkdir(parents=True, exist_ok=True)
            target = unique_path(dest.real_path, top_name(members))
            size = extract(tar, members, target)
    virtual = f"{dest.virtual_path.rstrip('/')}/{target.name}"
    return success_result(
        command=call.parsed.raw,
        detail="Virtual Desktop: download",
        kind="desktop",
        data={"downloaded": virtual, "status_lines": [f"Copied {target.name} to {dest.virtual_path}"]},
        sections=[("DESKTOP", [f"downloaded: {virtual} ({size} bytes)", *boot_notes(call.boot)])],
        cwd=call.ctx.cwd,
    )
