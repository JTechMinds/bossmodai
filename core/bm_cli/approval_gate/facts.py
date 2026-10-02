"""Deterministic facts about one shell command, computed before any review.

Code owns these facts so they are reproducible and testable: which real
paths the command touches, what each path is called on the agent's virtual
mounts, which of them are whole workspace roots, and a coarse effect class
(from the table in ``effects``).
System AI receives the facts as hints. It does not compute them, and the
gate's invariants (jail, system roots, root destruction, host processes)
are decided from them in code.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from core.bm_cli import install_layout
from core.bm_cli.approval_gate.effects import (
    REDIRECT_TOKENS,
    WRITE_NAMES,
    EffectClass,
    classify_effect,
)
from core.bm_cli.cli_always import NEST_CWD_PREFIX
from core.bm_cli.effective_commands import unwrap_argv
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import agent_floor_id, floor_root
from core.bm_cli.host_roots import is_within_roots
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import argv0_basename_after_resolve
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.shell_executor import (
    PathJailError,
    allowed_shell_roots,
    path_candidates_from_token,
    resolve_jailed_path,
)
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent

Workspace = Literal["me", "clone", "project", "other"]

_SYSTEM_ROOTS = (
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/root"),
    Path("/boot"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
    Path("/lib"),
    Path("/lib64"),
    Path("/var"),
    Path("/opt"),
    Path("/run"),
)

_HOST_WORK_DIR = PurePosixPath(NEST_CWD_PREFIX).relative_to("/me").as_posix()

# Strictest first: the command's effect is the first class any of its
# effective commands has.
_EFFECT_STRICTNESS: tuple[EffectClass, ...] = (
    "host_process", "delete", "network_write", "install",
    "local_write", "network_read", "unknown", "read_only",
)


@dataclass(frozen=True)
class PathFact:
    """One real path a command touches, with its name on the agent's mounts.

    Attributes:
        virtual: The path as the agent sees it (``/me/...``, ``/projects/...``),
            or the real path when it is on neither mount.
        real: The resolved host path.
        workspace: Which kind of workspace holds the path.
        label: Operator- and reviewer-facing name, e.g.
            ``"/projects/diablo-poc (floor project diablo-poc)"``.
        is_root: The path is a whole workspace: the floor projects mount, a
            project directory, the agent's ``/me``, a clone root under
            ``/me/host-work``, or a ``.git`` directory. A delete target that
            is the start path of a ``find`` with test predicates is not a
            root: only matches under it are deleted.
    """

    virtual: str
    real: Path
    workspace: Workspace
    label: str
    is_root: bool


@dataclass(frozen=True)
class CommandFacts:
    """Everything code knows about one command before System AI is asked.

    Attributes:
        command: The raw command string.
        cwd_virtual: The virtual working directory.
        cwd_real: The resolved real working directory.
        paths: Every path-like operand, flag payload and redirect target,
            over every effective command (wrappers unwrapped, ``find``
            actions added).
        write_targets: The subset of ``paths`` the commands write, move or
            delete, in argv order.
        effect: The strictest class from the effect table across the
            effective commands.
    """

    command: str
    cwd_virtual: str
    cwd_real: Path
    paths: tuple[PathFact, ...]
    write_targets: tuple[PathFact, ...]
    effect: EffectClass


@dataclass(frozen=True)
class _Mounts:
    me: Path
    projects: Path | None


def command_facts(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> CommandFacts:
    """Resolve and classify one shell command for the approval gate.

    ``/me`` and ``/projects`` argv tokens are rewritten to real paths first,
    so every path is judged where it really lands. Each command it really
    runs (:func:`effective_parsed_commands`) contributes its paths, its
    write targets (operands of its own write command) and its effect.

    Args:
        agent: The agent running the command.
        parsed: The parsed command (virtual paths not yet rewritten).
        cwd: The agent's virtual working directory.

    Returns:
        The command's facts.

    Raises:
        PathJailError: A token cannot be resolved, or uses ``~user``.
        ValueError: ``cwd`` is not a real workspace path, or a redirect has
            no target.
        LookupError: No agent owns ``agent.storage_key``.
    """
    from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths
    from core.bm_cli.virtual_fs import resolve_cli_path

    resolved = resolve_cli_path(agent.storage_key, cwd, ".")
    if resolved.real_path is None:
        raise ValueError("cwd is not a real workspace path")
    real_cwd = Path(resolved.real_path).resolve()
    rewritten = rewrite_virtual_shell_paths(agent, parsed, cwd)
    mounts = _mounts(agent)
    paths: list[PathFact] = []
    writes: list[PathFact] = []
    effects: set[EffectClass] = set()
    for command, filtered in _effective_with_filters(rewritten):
        force = argv0_basename_after_resolve(command.name) in WRITE_NAMES
        effect = classify_effect(command)
        for token, is_write in _path_tokens(command.args, force_operands=force):
            fact = _path_fact(agent, _resolve_token(token, real_cwd), mounts)
            if is_write and effect == "delete" and token in filtered:
                # A filtered find deletes matches under the start path,
                # not the path itself; the reviewer judges it.
                fact = replace(fact, is_root=False)
            paths.append(fact)
            if is_write:
                writes.append(fact)
        effects.add(effect)
    return CommandFacts(
        command=parsed.raw,
        cwd_virtual=cwd,
        cwd_real=real_cwd,
        paths=tuple(paths),
        write_targets=tuple(writes),
        effect=next(effect for effect in _EFFECT_STRICTNESS if effect in effects),
    )


def effective_parsed_commands(parsed: ParsedCliCommand) -> tuple[ParsedCliCommand, ...]:
    """Return the commands *parsed* really runs, for facts and gate invariants.

    The same unwrapping policy uses (:mod:`core.bm_cli.effective_commands`),
    except that ``{}`` in a ``find -exec`` command becomes the find's start
    paths, so ``find /projects/x -exec rm -rf {} +`` targets ``/projects/x``.

    Args:
        parsed: The parsed command.

    Returns:
        *parsed* itself first when no wrapper was unwrapped, else the
        wrapped command; then any ``find`` or ``time -o`` side commands.
    """
    return tuple(command for command, _filtered in _effective_with_filters(parsed))


def _effective_with_filters(
    parsed: ParsedCliCommand,
) -> list[tuple[ParsedCliCommand, frozenset[str]]]:
    """Effective commands, each with its filtered-find start-path operands."""
    unwrapped = unwrap_argv((parsed.name, *parsed.args), braces_to_paths=True)
    commands = [parse_cli_command(shlex.join(argv)) for argv in unwrapped.argvs]
    if not unwrapped.replaced:
        commands[0] = parsed
    return list(zip(commands, unwrapped.filtered_starts, strict=True))


def host_refusal(agent: Agent, facts: CommandFacts) -> str | None:
    """Refuse system roots, the application install, and jail escapes.

    Args:
        agent: The agent running the command.
        facts: The command's facts.

    Returns:
        A ``Path jail: ...`` message for the first refused path (the working
        directory first), or None when every path is inside the jail.
    """
    roots = allowed_shell_roots(agent.storage_key)
    me = agent_artifact_dir(agent.storage_key).resolve()
    if not is_within_roots(facts.cwd_real, roots):
        return (
            f"Path jail: working directory {str(facts.cwd_real)!r} is outside "
            "the allowed workspace roots"
        )
    for path in (facts.cwd_real, *(fact.real for fact in facts.paths)):
        why = _refuse_path(path, jail_roots=roots, me=me)
        if why is not None:
            return why
    return None


def paths_within(facts: CommandFacts, root_real: Path) -> bool:
    """Return whether the working directory and every touched path sit in a root.

    Args:
        facts: The command's facts.
        root_real: The resolved real root to contain everything.

    Returns:
        True when ``facts.cwd_real`` and every ``facts.paths`` entry equal
        ``root_real`` or are inside it.
    """
    roots = (root_real,)
    if not is_within_roots(facts.cwd_real, roots):
        return False
    return all(is_within_roots(fact.real, roots) for fact in facts.paths)


def looks_like_path(token: str) -> bool:
    """Return whether a bare argv operand reads as a filesystem path."""
    if not token or token == "-":
        return False
    if token.startswith("~") or token.startswith("/") or token.startswith("\\"):
        return True
    if token in {".", ".."} or token.startswith("./") or token.startswith("../"):
        return True
    return "/" in token or "\\" in token


def _path_tokens(args: tuple[str, ...], *, force_operands: bool) -> list[tuple[str, bool]]:
    """Return ``(token, is_write)`` for every path-like argv entry.

    Operands of a write command and redirect targets are writes. Flag
    payloads (``--out=/x``) and other path operands are reads.
    """
    tokens = list(args)
    found: list[tuple[str, bool]] = []
    end_flags = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in REDIRECT_TOKENS:
            if index + 1 >= len(tokens):
                raise ValueError("redirect has no target")
            found.append((tokens[index + 1], True))
            index += 2
            continue
        if not end_flags and token == "--":
            end_flags = True
            index += 1
            continue
        if not end_flags and token.startswith("-") and token != "-":
            found.extend((item, False) for item in path_candidates_from_token(token))
            index += 1
            continue
        if force_operands:
            found.append((token, True))
        elif looks_like_path(token):
            found.append((token, False))
        index += 1
    return found


def _resolve_token(token: str, cwd: Path) -> Path:
    if token.startswith("~"):
        resolved = resolve_jailed_path(token, cwd=cwd)
        if resolved is None:
            raise PathJailError(f"Path jail: {token!r} is not allowed")
        return resolved
    path = Path(token)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except OSError as exc:
        raise PathJailError(f"Path jail: cannot resolve {token!r}: {exc}") from exc


def _mounts(agent: Agent) -> _Mounts:
    me = agent_artifact_dir(agent.storage_key).resolve()
    floor_id = agent_floor_id(agent.storage_key)
    projects = floor_root(floor_id).resolve() if floor_id is not None else None
    return _Mounts(me=me, projects=projects)


def _path_fact(agent: Agent, real: Path, mounts: _Mounts) -> PathFact:
    fact = _mount_fact(agent, real, mounts)
    if real.name == ".git" and not fact.is_root:
        return replace(fact, is_root=True)
    return fact


def _mount_fact(agent: Agent, real: Path, mounts: _Mounts) -> PathFact:
    if is_within_roots(real, (mounts.me,)):
        rel = real.relative_to(mounts.me).parts
        virtual = "/me" + "".join(f"/{part}" for part in rel)
        if len(rel) >= 2 and rel[0] == _HOST_WORK_DIR:
            return PathFact(virtual, real, "clone", f"{virtual} (locked clone {rel[1]})", len(rel) == 2)
        return PathFact(virtual, real, "me", f"{virtual} (agent workspace /me)", not rel)
    if mounts.projects is not None and is_within_roots(real, (mounts.projects,)):
        rel = real.relative_to(mounts.projects).parts
        virtual = "/projects" + "".join(f"/{part}" for part in rel)
        if not rel:
            return PathFact(virtual, real, "project", f"{virtual} (floor projects mount)", True)
        project = project_directory_for(agent.storage_key, real)
        is_root = project is not None and project.resolve() == real
        return PathFact(virtual, real, "project", f"{virtual} (floor project {rel[0]})", is_root)
    return PathFact(str(real), real, "other", f"{real} (outside /me and /projects)", False)


def _refuse_path(path: Path, *, jail_roots: tuple[Path, ...], me: Path) -> str | None:
    if path == Path(path.anchor):
        return f"Path jail: {str(path)!r} is the filesystem root"
    for root in _SYSTEM_ROOTS:
        if path == root or root in path.parents:
            return f"Path jail: {str(path)!r} is a host system path"
    install = install_layout.app_install_root().resolve()
    if (path == install or install in path.parents) and not is_within_roots(path, (me,)):
        return f"Path jail: {str(path)!r} is inside the application install"
    if not is_within_roots(path, jail_roots):
        return (
            f"Path jail: {str(path)!r} resolves outside the allowed workspace roots"
        )
    return None
