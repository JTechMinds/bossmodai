"""Fail-closed scope for agent git.

Agent git may create repositories and branches inside the bound project
(or the agent's own workspace repo, or a locked clone). It must not operate
on the BossMod application install or on any other repository. Virtual
``/projects`` and ``/me`` paths, including ``-C`` and ``--work-tree``
operands, are rewritten to their real directories before the bound-repo
check. ``cd ..`` and other absolute paths are checked after realpath
resolution. A miss is an explicit Blocked reason.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, replace
from pathlib import Path

from core.bm_cli.host_roots import is_within_roots
from core.bm_cli import install_layout
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.results import error_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent

# Virtual git status/log/diff/show/restore is pinned to the agent /me repo
# unless a location override or a locked clone makes it follow cwd.
_PINNED_VIRTUAL_GIT = frozenset({"status", "log", "diff", "show", "restore"})

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

APP_INSTALL_GIT_WHY = (
    "Blocked — agent git resolved to the BossMod application install. "
    "Branches and commits stay in the project workspace, which lives outside "
    "the application tree and has its own repository. "
    "This command was not run."
)

PROJECT_GIT_ESCAPE_WHY = (
    "Blocked — agent git is scoped to the bound project repository. "
    "The working directory, -C path, --git-dir, --work-tree, or another path "
    "resolved outside that repository. "
    "This command was not run."
)


@dataclass(frozen=True, slots=True)
class _GitScope:
    work_tree: Path
    git_dir: Path | None
    invalid: bool = False


def project_git_fence_reason(
    agent: Agent,
    parsed: ParsedCliCommand,
    virtual_cwd: str,
) -> str | None:
    """Return a Blocked reason, or None when this git command may run."""
    if not _is_git_cli(parsed):
        return None
    if _pinned_to_agent_workspace(agent, parsed, virtual_cwd):
        return None

    scoped, real_cwd, scope = _resolved_git_scope(agent, parsed, virtual_cwd)
    if real_cwd is not None:
        _ensure_personal_workspace_repo(agent, real_cwd)
    if scope.invalid:
        if _scope_hits_application(scoped.args, real_cwd, virtual_cwd):
            return APP_INSTALL_GIT_WHY
        return PROJECT_GIT_ESCAPE_WHY
    if _hits_application(scope.work_tree) or _hits_application(scope.git_dir):
        return APP_INSTALL_GIT_WHY

    operands = _escape_operands(scoped.args, scope.work_tree)
    if operands is None:
        return PROJECT_GIT_ESCAPE_WHY
    for operand in operands:
        if _hits_application(operand):
            return APP_INSTALL_GIT_WHY

    # Bind to the repository ``-C`` / ``--work-tree`` selected after virtual
    # ``/projects/<slug>`` (and real absolute paths) are rewritten. The CLI
    # cwd is that scope when the command has no location override.
    bound = _bound_repository(agent, scope.work_tree)
    if bound is not None and _hits_application(bound):
        return APP_INSTALL_GIT_WHY
    if bound is None:
        # A ``-C`` / ``--work-tree`` that missed both the project and the
        # application install is an escape. With no location override, a cwd
        # that is the application install keeps the install reason.
        if git_has_location_override(parsed.args):
            return PROJECT_GIT_ESCAPE_WHY
        if _scope_hits_application(scoped.args, real_cwd, virtual_cwd):
            return APP_INSTALL_GIT_WHY
        return PROJECT_GIT_ESCAPE_WHY

    if not is_within_roots(scope.work_tree, (bound,)):
        return _why_for_path(scope.work_tree)
    if scope.git_dir is not None and not _git_dir_inside(scope.git_dir, bound):
        return _why_for_path(scope.git_dir)
    for operand in operands:
        if not is_within_roots(operand, (bound,)):
            return _why_for_path(operand)

    subcommand = _git_subcommand(scoped.args)
    discovered = _repository_root(scope)
    if discovered is not None and _git_dir_points_at_application(discovered):
        return APP_INSTALL_GIT_WHY
    if subcommand == "init":
        if scope.work_tree.resolve() != bound.resolve():
            return PROJECT_GIT_ESCAPE_WHY
        return None
    if discovered is None or discovered.resolve() != bound.resolve():
        if discovered is not None and _hits_application(discovered):
            return APP_INSTALL_GIT_WHY
        return PROJECT_GIT_ESCAPE_WHY
    return None


def project_git_block(
    agent: Agent,
    parsed: ParsedCliCommand,
    virtual_cwd: str,
    *,
    channel_id: str | None,
    persist: bool,
) -> BossModCliResult | None:
    """Return the operator-visible block, optionally persisting a system note."""
    why = project_git_fence_reason(agent, parsed, virtual_cwd)
    if why is None:
        return None
    chrome: dict[str, object] = {}
    if persist:
        from core.agent_loop.notifications import persist_origin_system_note

        name = (agent.name or "").strip() or "Agent"
        chrome = persist_origin_system_note(
            agent,
            f"{name} tried `{parsed.raw}` — {why}",
            channel_id=channel_id,
            kind="blocked",
        )
    result = error_result(
        parsed.raw,
        why,
        cwd=virtual_cwd,
        executor="shell",
        kind="project_git_fence",
    )
    data = dict(result.data or {})
    data["policy_tier"] = "project_git_fence"
    payload = chrome.get("channel_message") or chrome.get("chat_message")
    if payload:
        data["origin_chrome"] = payload
    return replace(result, data=data)


def _ensure_personal_workspace_repo(agent: Agent, real_cwd: Path) -> None:
    """Give ``/me`` its own git dir before shell git can walk up to the app.

    Locked clones under ``host-work`` keep whatever repository they already
    have. This does not init the application checkout.
    """
    from core.bm_cli.filesystem import agent_artifact_dir
    from core.bm_cli.workspace_git import ensure_agent_workspace_repo

    workspace = agent_artifact_dir(agent.storage_key).resolve()
    if not is_within_roots(real_cwd, (workspace,)):
        return
    host_work = workspace / "host-work"
    if real_cwd != host_work and is_within_roots(real_cwd, (host_work,)):
        return
    ensure_agent_workspace_repo(agent)


def project_git_policy_subject(
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
    if not _is_git_cli(parsed) or not git_has_location_override(parsed.args):
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


def _scope_hits_application(
    args: tuple[str, ...],
    real_cwd: Path | None,
    virtual_cwd: str,
) -> bool:
    base = real_cwd if real_cwd is not None else Path("/")
    if _scope_mentions_application(args, base):
        return True
    if real_cwd is not None and _hits_application(real_cwd):
        return True
    return _absolute_token_hits_application(virtual_cwd)


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


def _is_git_cli(parsed: ParsedCliCommand) -> bool:
    name = Path(parsed.name).name.lower()
    return name in {"git", "git.exe"}


def _pinned_to_agent_workspace(agent: Agent, parsed: ParsedCliCommand, virtual_cwd: str) -> bool:
    if _git_subcommand(parsed.args) not in _PINNED_VIRTUAL_GIT:
        return False
    if git_has_location_override(parsed.args):
        return False
    from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo

    return not cwd_is_nested_clone_repo(agent, virtual_cwd)


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


def _absolute_token_hits_application(token: str) -> bool:
    resolved = _absolute_existing(token)
    return resolved is not None and _hits_application(resolved)


def _bound_repository(agent: Agent, real_cwd: Path) -> Path | None:
    from core.bm_cli.filesystem import agent_artifact_dir
    from core.bm_cli.workspace_preference import find_git_root

    project = project_directory_for(agent.storage_key, real_cwd)
    if project is not None:
        return project.resolve()

    workspace = agent_artifact_dir(agent.storage_key).resolve()
    host_work = workspace / "host-work"
    if is_within_roots(real_cwd, (host_work,)) and real_cwd != host_work:
        clone = host_work / real_cwd.relative_to(host_work).parts[0]
        return clone.resolve()

    if is_within_roots(real_cwd, (workspace,)):
        git_root = find_git_root(real_cwd)
        if git_root is not None and git_root.resolve() == workspace:
            return workspace
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


def _escape_operands(args: tuple[str, ...], base: Path) -> list[Path] | None:
    """Return absolute/parent operands after the git subcommand.

    ``None`` means a path could not be resolved (fail closed).
    """
    tokens = list(args)
    index = 0
    seen_command = False
    found: list[Path] = []
    while index < len(tokens):
        token = tokens[index]
        if not seen_command:
            if token == "--":
                seen_command = True
                index += 1
                continue
            if token.startswith("-"):
                key = token.split("=", 1)[0]
                if key in _GIT_VALUE_OPTIONS and "=" not in token and not (
                    token.startswith("-C") and not token.startswith("--") and token != "-C"
                ):
                    index += 2
                    continue
                index += 1
                continue
            seen_command = True
            index += 1
            continue
        candidate = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
        if _looks_like_escape(candidate):
            resolved = _resolve_against(candidate, base)
            if resolved is None:
                return None
            found.append(resolved)
        index += 1
    return found


def _looks_like_escape(token: str) -> bool:
    if not token or token == "-":
        return False
    if token.startswith("~") or token.startswith("/") or token.startswith("\\"):
        return True
    if token == ".." or token.startswith("../") or token.startswith("..\\"):
        return True
    return False


def _repository_root(scope: _GitScope) -> Path | None:
    from core.bm_cli.workspace_preference import find_git_root

    if scope.git_dir is not None:
        git_path = scope.git_dir
        if git_path.name == ".git":
            return git_path.parent
        if git_path.is_file() or git_path.name.endswith(".git"):
            return git_path.parent
        return git_path
    return find_git_root(scope.work_tree)


def _git_dir_inside(git_dir: Path, bound: Path) -> bool:
    if is_within_roots(git_dir, (bound,)):
        return True
    dereferenced = _dereference_git_dir(git_dir)
    return dereferenced is not None and is_within_roots(dereferenced, (bound,))


def _git_dir_points_at_application(repo: Path) -> bool:
    git = repo / ".git"
    if not git.exists():
        return False
    target = _dereference_git_dir(git)
    return target is not None and _hits_application(target)


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


def _hits_application(path: Path | None) -> bool:
    if path is None:
        return False
    from core.bm_cli.workspace_preference import find_git_root

    install = install_layout.app_install_root().resolve()
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    if resolved == install:
        return True
    app_git = install / ".git"
    if app_git.exists():
        target = _dereference_git_dir(app_git)
        if target is not None and (resolved == target or is_within_roots(resolved, (target,))):
            return True
        if resolved == app_git.resolve() or is_within_roots(resolved, (app_git.resolve(),)):
            return True
    start = resolved if resolved.is_dir() else resolved.parent
    repo = find_git_root(start)
    return repo is not None and repo.resolve() == install


def _why_for_path(path: Path) -> str:
    if _hits_application(path):
        return APP_INSTALL_GIT_WHY
    return PROJECT_GIT_ESCAPE_WHY


def _scope_mentions_application(args: tuple[str, ...], cwd: Path) -> bool:
    scope = _parse_scope(args, cwd)
    if _hits_application(scope.work_tree) or _hits_application(scope.git_dir):
        return True
    operands = _escape_operands(args, cwd)
    if not operands:
        return False
    return any(_hits_application(item) for item in operands)


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


def _git_subcommand(args: tuple[str, ...] | list[str]) -> str:
    index = 0
    tokens = list(args)
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            key = token.split("=", 1)[0]
            attached_c = (
                token.startswith("-C")
                and not token.startswith("--")
                and token not in {"-C", "-c"}
            )
            if (key in _GIT_VALUE_OPTIONS or attached_c) and "=" not in token and not attached_c:
                index += 2
                continue
            index += 1
            continue
        return token
    return tokens[index] if index < len(tokens) else ""
