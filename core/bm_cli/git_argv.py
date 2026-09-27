"""git argv parsing for policy matching; enforcement lives in the path jail and GIT_CEILING_DIRECTORIES.

Nothing here blocks a command. The helpers let seed policy rules written as
``git status`` / ``git commit`` / ``git checkout`` match the ``git -C
/projects/<slug> …`` form, and tell the runtime when git argv selects a
repository other than the plain cwd.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.host_roots import is_within_roots
from core.bm_cli.nest_git import is_git_cli
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent

_GIT_VALUE_OPTIONS = frozenset({
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--separate-git-dir",
    "--namespace",
    "--exec-path",
    "--config-env",
    "--attr-source",
})

_GIT_DIR_OPTIONS = frozenset({"--git-dir", "--separate-git-dir"})


@dataclass(frozen=True, slots=True)
class _GitScope:
    work_tree: Path
    git_dir: Path | None
    invalid: bool = False


def git_policy_subject(
    agent: Agent,
    parsed: ParsedCliCommand,
    virtual_cwd: str,
) -> str | None:
    """Return ``git <subcommand> …`` when ``-C`` lands in a project repo.

    Seed rules are written as ``git status``, ``git commit``, and
    ``git checkout``. Those match once ``-C`` or ``--work-tree`` rewrites
    onto a project directory. ``git -C .`` from ``/me`` keeps the default
    policy.
    """
    if not is_git_cli(parsed) or not git_has_location_override(parsed.args):
        return None
    _scoped, _real_cwd, scope = _resolved_git_scope(agent, parsed, virtual_cwd)
    project = None if scope.invalid else project_directory_for(agent.storage_key, scope.work_tree)
    if project is None:
        return None
    if scope.git_dir is not None and not _git_dir_inside(scope.git_dir, project):
        return None
    tail = _git_command_tail(parsed.args)
    if not tail:
        return None
    return shlex.join(["git", *tail])


def git_has_location_override(args: tuple[str, ...]) -> bool:
    """Return True when git argv selects a repo other than the plain cwd."""
    for token in args:
        key = token.split("=", 1)[0]
        if key in {"-C", "--git-dir", "--work-tree", "--separate-git-dir"}:
            return True
        if token.startswith("-C") and not token.startswith("--") and token != "-C":
            return True
    return False


def _resolved_git_scope(
    agent: Agent,
    parsed: ParsedCliCommand,
    virtual_cwd: str,
) -> tuple[ParsedCliCommand, Path | None, _GitScope]:
    """Rewrite virtual mounts, then parse ``-C`` / ``--work-tree`` / ``--git-dir``."""
    scoped = _rewrite_virtual_git_paths(agent, parsed, virtual_cwd)
    real_cwd = _resolve_real_cwd(agent, virtual_cwd)
    base = real_cwd if real_cwd is not None else Path("/")
    return scoped, real_cwd, _parse_scope(scoped.args, base)


def _rewrite_virtual_git_paths(
    agent: Agent,
    parsed: ParsedCliCommand,
    virtual_cwd: str,
) -> ParsedCliCommand:
    """Map ``/me`` and ``/projects`` git paths onto their real directories.

    Uses the same token rewrite as the shell, including the path after
    ``-C``, ``--git-dir``, and ``--work-tree``.
    """
    from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths

    return rewrite_virtual_shell_paths(agent, parsed, virtual_cwd)


def _git_command_tail(args: tuple[str, ...]) -> list[str]:
    """Return argv starting at the git subcommand, without global options."""
    index = 0
    tokens = list(args)
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1:]
        if not token.startswith("-"):
            return tokens[index:]
        key, sep, _value = token.partition("=")
        attached_c = (
            token.startswith("-C")
            and not token.startswith("--")
            and token not in {"-C", "-c"}
        )
        if attached_c or sep == "=":
            index += 1
            continue
        if key in _GIT_VALUE_OPTIONS:
            index += 2
            continue
        index += 1
    return []


def _resolve_real_cwd(agent: Agent, virtual_cwd: str) -> Path | None:
    from core.bm_cli.virtual_fs import resolve_cli_path

    try:
        resolved = resolve_cli_path(agent.storage_key, virtual_cwd, ".")
    except (OSError, ValueError):
        return _absolute_existing(virtual_cwd)
    if resolved is None or resolved.real_path is None:
        return _absolute_existing(virtual_cwd)
    try:
        return Path(resolved.real_path).resolve()
    except OSError:
        return None


def _absolute_existing(token: str) -> Path | None:
    text = (token or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def _parse_scope(args: tuple[str, ...], cwd: Path) -> _GitScope:
    work = cwd
    git_dir: Path | None = None
    index = 0
    tokens = list(args)
    while index < len(tokens):
        token = tokens[index]
        if token == "--" or not token.startswith("-"):
            break
        key, sep, inline = token.partition("=")
        value = inline if sep else None
        attached_c = (
            token.startswith("-C")
            and not token.startswith("--")
            and token not in {"-C", "-c"}
        )
        if attached_c:
            key = "-C"
            value = token[2:]
        if key in {"-C", "--work-tree", *_GIT_DIR_OPTIONS}:
            if value is None:
                index += 1
                if index >= len(tokens):
                    return _GitScope(work, git_dir, invalid=True)
                value = tokens[index]
            resolved = _resolve_against(value, work)
            if resolved is None:
                return _GitScope(work, git_dir, invalid=True)
            if key == "-C" or key == "--work-tree":
                work = resolved
            else:
                git_dir = resolved
            index += 1
            continue
        if key == "-c":
            if value is None:
                index += 1
                if index >= len(tokens):
                    return _GitScope(work, git_dir, invalid=True)
                value = tokens[index]
            if _config_sets_worktree(value):
                redirected = _worktree_from_config(value, work)
                if redirected is None:
                    return _GitScope(work, git_dir, invalid=True)
                work = redirected
            index += 1
            continue
        if key in _GIT_VALUE_OPTIONS and sep == "":
            index += 2
            continue
        index += 1
    return _GitScope(work, git_dir, False)


def _config_sets_worktree(value: str) -> bool:
    return "core.worktree=" in (value or "").lower().replace(" ", "")


def _worktree_from_config(value: str, base: Path) -> Path | None:
    if not _config_sets_worktree(value):
        return None
    raw = (value or "").split("=", 1)[1].strip().strip('"').strip("'")
    if not raw:
        return None
    return _resolve_against(raw, base)


def _git_dir_inside(git_dir: Path, bound: Path) -> bool:
    if is_within_roots(git_dir, (bound,)):
        return True
    dereferenced = _dereference_git_dir(git_dir)
    return dereferenced is not None and is_within_roots(dereferenced, (bound,))


def _dereference_git_dir(git: Path) -> Path | None:
    try:
        if git.is_file():
            line = git.read_text(encoding="utf-8", errors="replace").strip()
            if line.lower().startswith("gitdir:"):
                raw = line.split(":", 1)[1].strip()
                target = Path(raw)
                if not target.is_absolute():
                    target = git.parent / target
                return target.resolve()
        return git.resolve()
    except OSError:
        return None


def _resolve_against(token: str, base: Path) -> Path | None:
    if token.startswith("~"):
        return None
    path = Path(token)
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve()
    except OSError:
        return None
