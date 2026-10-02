"""Turn a parsed agent script into the concrete commands to decide and run.

The parse (:mod:`core.bm_cli.shell_script`) is pure. This module adds what
needs the agent's workspace, before any command is decided:

* a leading ``cd <dir> &&`` is resolved (not yet persisted) and the rest
  of the script runs in that directory;
* BossMod-only commands (virtual commands with no native binary, such as
  ``write`` or ``task``) are refused with a steer: scripts run native
  programs only;
* unquoted globs are expanded the way bash does (sorted, no hidden files,
  no match keeps the word), and every match must sit inside the path jail;
* redirect targets are rewritten from ``/me``/``/projects`` to real paths
  and must sit inside the path jail.

It reads the filesystem and settings; it writes nothing.
"""

from __future__ import annotations

import glob
import shlex
from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.command_registry import resolve_virtual_command_name
from core.bm_cli.host_roots import is_within_roots
from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.shell_executor import PathJailError, allowed_shell_roots, resolve_redirect_target
from core.bm_cli.shell_script import (
    Redirect,
    ShellScript,
    SimpleCommand,
    Word,
    parse_shell_script,
    segment_raw,
)
from core.bm_cli.types import ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent

# Virtual commands that are also ordinary programs; in a script they run
# natively under shell policy. Every other virtual command is BossMod-only.
NATIVE_VIRTUAL_NAMES = frozenset({"ls", "cat", "git", "mkdir", "pwd"})
CD_STEER = "cd works in a script only as the first command, followed by &&: cd <dir> && <commands>"


def bossmod_only_steer(name: str) -> str:
    """Agent-facing refusal for a BossMod-only command inside a script."""
    return f"{name} is a BossMod command; run it alone"


def bossmod_glob_steer(name: str) -> str:
    """Agent-facing refusal for an unquoted glob on a lone BossMod-only command."""
    return f"{name} is a BossMod command and does not expand * ? [ ]: quote those characters"


class ScriptRefused(ValueError):
    """The script cannot be decided; nothing ran.

    Attributes:
        message: The agent-facing reason with its steer.
        jail: True when a path left the jail (a block), False for a steer.
    """

    def __init__(self, message: str, *, jail: bool) -> None:
        super().__init__(message)
        self.message = message
        self.jail = jail


@dataclass(frozen=True)
class ScriptSegment:
    """One simple command, ready to decide.

    Attributes:
        command: The command with globs expanded (``/me``/``/projects``
            words keep their virtual form, as a lone command would) and
            redirect targets as resolved real paths.
        parsed: The segment's argv as a command: the policy subject.
        redirect_writes: Real paths the segment's redirects write.
    """

    command: SimpleCommand
    parsed: ParsedCliCommand
    redirect_writes: tuple[str, ...]


@dataclass(frozen=True)
class PreparedScript:
    """A script whose every segment can now be decided.

    Attributes:
        raw: The script text the agent sent.
        cd: The leading ``cd <dir>`` to persist when the script runs, or None.
        cwd: The virtual directory the segments run in (after ``cd``).
        cwd_real: Its real path.
        roots: The path-jail roots.
        body: The script without the leading ``cd``, segments expanded.
        segments: ``body``'s simple commands, in source order.
    """

    raw: str
    cd: ParsedCliCommand | None
    cwd: str
    cwd_real: Path
    roots: tuple[Path, ...]
    body: ShellScript
    segments: tuple[ScriptSegment, ...]


def prepare_script(
    agent: Agent, raw: str, cwd_before: str, *, virtual_commands: frozenset[str],
) -> PreparedScript:
    """Parse, steer, resolve ``cd``, expand globs and jail-check redirects.

    Args:
        agent: The agent running the script.
        raw: The script text.
        cwd_before: The agent's virtual working directory.
        virtual_commands: Every virtual command name (core and extensions).

    Returns:
        The prepared script.

    Raises:
        ScriptRefused: A syntax steer, a BossMod-only command, a missing
            ``cd`` directory or a non-workspace cwd (``jail=False``), or a
            glob match or redirect target outside the jail (``jail=True``).
        LookupError: No agent owns ``agent.storage_key``.
    """
    try:
        script = parse_shell_script(raw)
    except ValueError as exc:
        raise ScriptRefused(str(exc), jail=False) from exc
    cd, body, cwd = _leading_cd(agent, script, cwd_before)
    _refuse_bossmod_commands(body, virtual_commands)
    cwd_real = _real_dir(agent, cwd)
    roots = allowed_shell_roots(agent.storage_key)
    try:
        commands = [_prepare_command(agent, command, cwd, cwd_real, roots) for command in body.commands()]
    except PathJailError as exc:
        raise ScriptRefused(str(exc), jail=True) from exc
    expanded = body.with_commands([segment.command for segment in commands])
    return PreparedScript(raw, cd, cwd, cwd_real, roots, expanded, tuple(commands))


def _leading_cd(
    agent: Agent, script: ShellScript, cwd_before: str,
) -> tuple[ParsedCliCommand | None, ShellScript, str]:
    """Split off a leading ``cd <dir> &&``; return ``(cd, rest, cwd for the rest)``."""
    pipeline, connector = script.items[0]
    first = pipeline.commands[0]
    if _canonical(first.argv[0].text) != "cd":
        return None, script, cwd_before
    if len(pipeline.commands) != 1 or connector != "&&" or first.redirects or first.assignments:
        raise ScriptRefused(CD_STEER, jail=False)
    if len(first.argv) != 2:
        raise ScriptRefused('"cd" requires exactly one path argument.', jail=False)
    target_text = first.argv[1].text
    try:
        target = resolve_cli_path(agent.storage_key, cwd_before, target_text)
    except ValueError as exc:
        raise ScriptRefused(str(exc), jail=False) from exc
    if not target.exists or target.real_path is None or not target.real_path.is_dir():
        raise ScriptRefused(f"Directory not found: {target_text}", jail=False)
    cd = parse_cli_command(segment_raw(first))
    return cd, ShellScript(script.items[1:]), target.virtual_path


def _refuse_bossmod_commands(body: ShellScript, virtual_commands: frozenset[str]) -> None:
    commands = body.commands()
    for command in commands:
        name = _canonical(command.argv[0].text)
        if name == "cd":
            raise ScriptRefused(CD_STEER, jail=False)
        if name in virtual_commands and name not in NATIVE_VIRTUAL_NAMES:
            # One plain command reached the script path only for its glob.
            lone = len(commands) == 1 and not command.redirects and not command.assignments
            raise ScriptRefused(bossmod_glob_steer(name) if lone else bossmod_only_steer(name), jail=False)


def _prepare_command(
    agent: Agent, command: SimpleCommand, cwd: str, cwd_real: Path, roots: tuple[Path, ...],
) -> ScriptSegment:
    argv = tuple(word for item in command.argv for word in _expand(agent, item, cwd, cwd_real, roots))
    redirects: list[Redirect] = []
    writes: list[str] = []
    for redirect in command.redirects:
        if redirect.target is None:
            redirects.append(redirect)
            continue
        (real_token,) = _rewrite_tokens(agent, (redirect.target.text,), cwd)
        path = str(resolve_redirect_target(real_token, cwd=cwd_real, allowed_roots=roots))
        redirects.append(Redirect(redirect.fd, redirect.mode, Word(path, False, glob.escape(path))))
        if redirect.mode != "read":
            writes.append(path)
    expanded = SimpleCommand(command.assignments, argv, tuple(redirects))
    return ScriptSegment(expanded, parse_cli_command(segment_raw(expanded)), tuple(writes))


def _expand(agent: Agent, word: Word, cwd: str, cwd_real: Path, roots: tuple[Path, ...]) -> tuple[Word, ...]:
    """Expand one word like bash: sorted matches, or the word itself when none match.

    The directory part before the first glob component keeps its virtual
    form in the result (``/projects/x/*.md`` gives ``/projects/x/a.md``),
    so each segment is decided as the same command typed out would be.

    Raises:
        PathJailError: A match resolves outside the jail.
    """
    if not word.glob:
        return (word,)
    text_parts = word.text.split("/")
    pattern_parts = word.pattern.split("/")
    static = next(index for index, part in enumerate(pattern_parts) if glob.has_magic(part))
    base_text = "/".join(text_parts[:static]) or ("/" if word.text.startswith("/") else "")
    if base_text:
        (real_base,) = _rewrite_tokens(agent, (base_text,), cwd)
        base_real = Path(real_base) if Path(real_base).is_absolute() else cwd_real / real_base
    else:
        base_real = cwd_real
    matches = sorted(glob.glob("/".join(pattern_parts[static:]), root_dir=base_real))
    if not matches:
        return (Word(word.text, False, word.pattern),)
    expanded: list[Word] = []
    for match in matches:
        if not is_within_roots(base_real / match, roots):
            raise PathJailError(
                f"Path jail: {word.text!r} matches {str(base_real / match)!r}, "
                "outside the allowed workspace roots"
            )
        text = f"{base_text.rstrip('/')}/{match}" if base_text else match
        expanded.append(Word(text, False, glob.escape(text)))
    return tuple(expanded)


def _rewrite_tokens(agent: Agent, tokens: tuple[str, ...], cwd: str) -> tuple[str, ...]:
    """``/me`` and ``/projects`` tokens as real paths; other tokens unchanged."""
    parsed = ParsedCliCommand(raw=shlex.join(tokens), name=tokens[0], args=tokens[1:])
    return tuple(shlex.split(rewrite_virtual_shell_paths(agent, parsed, cwd).raw))


def _real_dir(agent: Agent, cwd: str) -> Path:
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd, ".")
    except ValueError as exc:
        raise ScriptRefused(str(exc), jail=False) from exc
    if resolved.real_path is None:
        raise ScriptRefused("Shell cwd is not a real workspace path", jail=False)
    return Path(resolved.real_path).resolve()


def _canonical(name: str) -> str:
    return resolve_virtual_command_name(name) or name
