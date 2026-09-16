"""Project-local uv/venv for locked Branch/workspace-copy clones.

HOME is remapped to the clone cwd, but bare ``pip`` still uses whatever
Python is on PATH — often the host interpreter. Installs and test runs on a
locked clone must stay in that clone's ``uv`` / ``venv`` tree, never host
site-packages.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import argv0_policy_names, is_venv_bin_path
from core.bm_cli.results import error_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo, find_git_root
from core.models import Agent

_PIP_BASENAMES = frozenset({"pip", "pip3"})
_PYTEST_BASENAMES = frozenset({"pytest"})
_PIP_MUTATIONS = frozenset({"install", "uninstall"})
_HOST_SITE_FLAGS = frozenset({"--system", "--break-system-packages", "--user"})

HOST_PIP_DENY_MESSAGE = (
    "Host pip is blocked on a locked Branch/workspace-copy. "
    "Packages would land in the host interpreter. "
    "cd to the clone and use uv run pytest or .venv/bin/pytest. "
    "For installs, use uv pip / uv add / .venv/bin/pip (project-local only)."
)


@dataclass(frozen=True, slots=True)
class ProjectEnv:
    """uv / venv tools visible under a clone's project root."""

    root: Path
    has_uv: bool
    venv_dir: Path | None
    venv_pip: Path | None
    venv_pytest: Path | None


def is_uv_argv0(argv0: str) -> bool:
    """Return True when argv0 is the uv binary (path or bare name)."""
    return "uv" in argv0_policy_names(argv0)


def pip_mutation_args(parsed: ParsedCliCommand) -> tuple[str, ...] | None:
    """Return ``install|uninstall`` plus following args for a pip mutation.

    Matches bare ``pip`` / ``pip3``, path-qualified pip, and ``python -m pip``.
    Returns None for ``pip list`` / ``pip show`` and non-pip commands.
    """
    names = argv0_policy_names(parsed.name)
    args = parsed.args
    if any(_is_pip_basename(name) for name in names):
        if is_uv_argv0(parsed.name):
            return None
        return _first_pip_mutation(args)
    if any(_is_python_basename(name) for name in names):
        if len(args) >= 2 and args[0] == "-m" and args[1] == "pip":
            return _first_pip_mutation(args[2:])
    return None


def is_bare_pytest(parsed: ParsedCliCommand) -> bool:
    """Return True for PATH pytest, not ``uv run pytest`` or a venv pytest."""
    if is_uv_argv0(parsed.name):
        return False
    if is_venv_bin_path(parsed.name):
        return False
    return any(name in _PYTEST_BASENAMES for name in argv0_policy_names(parsed.name))


def is_project_local_pip(parsed: ParsedCliCommand) -> bool:
    """Return True when pip is already uv or a clone venv binary."""
    if is_venv_bin_path(parsed.name) and any(
        _is_pip_basename(name) for name in argv0_policy_names(parsed.name)
    ):
        return True
    if not is_uv_argv0(parsed.name) or not parsed.args:
        return False
    sub = parsed.args[0]
    return sub in {"pip", "add"}


def detect_project_env(project_root: Path) -> ProjectEnv:
    """Detect uv / ``.venv`` tools under *project_root*."""
    root = Path(project_root)
    has_project_uv = bool(shutil.which("uv")) and (
        (root / "uv.lock").exists() or (root / "pyproject.toml").exists()
    )
    venv_dir = _existing_venv_dir(root)
    venv_pip = _venv_tool(venv_dir, "pip")
    venv_pytest = _venv_tool(venv_dir, "pytest")
    return ProjectEnv(
        root=root,
        has_uv=has_project_uv,
        venv_dir=venv_dir,
        venv_pip=venv_pip,
        venv_pytest=venv_pytest,
    )


def rewrite_for_project_env(
    parsed: ParsedCliCommand,
    env: ProjectEnv,
    *,
    cwd: Path,
) -> ParsedCliCommand | str | None:
    """Rewrite host pip/pytest to clone uv/venv, or return a deny message.

    Returns:
    - a new :class:`ParsedCliCommand` when rewritten
    - a deny message string when host pip cannot be made project-local
    - ``None`` when the command should pass through unchanged
    """
    pip_args = pip_mutation_args(parsed)
    if pip_args is not None:
        if is_project_local_pip(parsed):
            return None
        rewritten = _rewrite_pip(pip_args, env, cwd=cwd)
        if rewritten is None:
            return HOST_PIP_DENY_MESSAGE
        return parse_cli_command(rewritten)

    if is_bare_pytest(parsed):
        rewritten = _rewrite_pytest(parsed.args, env, cwd=cwd)
        if rewritten is None:
            return None
        return parse_cli_command(rewritten)

    return None


def gate_locked_clone_command(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    task_id: str | None = None,
) -> ParsedCliCommand | BossModCliResult:
    """Rewrite or deny host pip on a locked clone; prefer uv/venv pytest.

    Pass-through when the workspace is not a locked nest-clone (or locked
    copy) so desk ``pip install`` still uses the existing approval card.
    """
    nested = cwd_is_nested_clone_repo(agent, cwd)
    locked = nested or _has_locked_workspace_copy(agent.id, task_id)
    pip_args = pip_mutation_args(parsed)
    local_pip = is_project_local_pip(parsed)

    if locked and (pip_args is not None or local_pip) and _has_host_site_flag(parsed.args):
        return error_result(
            parsed.raw,
            HOST_PIP_DENY_MESSAGE,
            cwd=cwd,
            executor="shell",
        )

    if pip_args is not None and not local_pip:
        if not locked:
            return parsed
        if not nested:
            return error_result(
                parsed.raw,
                HOST_PIP_DENY_MESSAGE,
                cwd=cwd,
                executor="shell",
            )
        env = _env_for_cwd(agent, cwd)
        if env is None:
            return error_result(
                parsed.raw,
                HOST_PIP_DENY_MESSAGE,
                cwd=cwd,
                executor="shell",
            )
        real = _real_cwd(agent, cwd) or env.root
        outcome = rewrite_for_project_env(parsed, env, cwd=real)
        if isinstance(outcome, str):
            return error_result(parsed.raw, outcome, cwd=cwd, executor="shell")
        if isinstance(outcome, ParsedCliCommand):
            return outcome
        return error_result(parsed.raw, HOST_PIP_DENY_MESSAGE, cwd=cwd, executor="shell")

    if nested and is_bare_pytest(parsed):
        env = _env_for_cwd(agent, cwd)
        if env is None:
            return parsed
        real = _real_cwd(agent, cwd) or env.root
        outcome = rewrite_for_project_env(parsed, env, cwd=real)
        if isinstance(outcome, ParsedCliCommand):
            return outcome
    return parsed


def _has_locked_workspace_copy(agent_id: str, task_id: str | None) -> bool:
    from core.agent_loop.runtime_core import locked_workspace_copies_for_turn

    return bool(locked_workspace_copies_for_turn(agent_id, task_id))


def _env_for_cwd(agent: Agent, cwd: str) -> ProjectEnv | None:
    real = _real_cwd(agent, cwd)
    if real is None:
        return None
    root = find_git_root(real) or real
    return detect_project_env(root)


def _real_cwd(agent: Agent, cwd: str) -> Path | None:
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd, ".")
    except (OSError, ValueError):
        return None
    if resolved is None or resolved.real_path is None:
        return None
    return Path(resolved.real_path)


def _existing_venv_dir(root: Path) -> Path | None:
    for name in (".venv", "venv"):
        candidate = root / name
        if (candidate / "bin").is_dir():
            return candidate
    return None


def _venv_tool(venv_dir: Path | None, name: str) -> Path | None:
    if venv_dir is None:
        return None
    path = venv_dir / "bin" / name
    return path if path.exists() else None


def _rewrite_pip(pip_args: tuple[str, ...], env: ProjectEnv, *, cwd: Path) -> str | None:
    if env.venv_pip is not None:
        return shlex.join([_relpath_for_cwd(env.venv_pip, cwd), *pip_args])
    if env.has_uv and env.venv_dir is not None:
        python = env.venv_dir / "bin" / "python"
        target = str(_relpath_for_cwd(python if python.exists() else env.venv_dir, cwd))
        return shlex.join(["uv", "pip", "--python", target, *pip_args])
    if env.has_uv:
        return shlex.join(["uv", "pip", *pip_args])
    return None


def _rewrite_pytest(args: tuple[str, ...], env: ProjectEnv, *, cwd: Path) -> str | None:
    if env.has_uv:
        return shlex.join(["uv", "run", "pytest", *args])
    if env.venv_pytest is not None:
        return shlex.join([_relpath_for_cwd(env.venv_pytest, cwd), *args])
    return None


def _relpath_for_cwd(path: Path, cwd: Path) -> str:
    try:
        return os.path.relpath(str(path), str(cwd))
    except OSError:
        return str(path)


def _has_host_site_flag(args: tuple[str, ...]) -> bool:
    return any(token in _HOST_SITE_FLAGS for token in args)


def _first_pip_mutation(args: tuple[str, ...]) -> tuple[str, ...] | None:
    for index, token in enumerate(args):
        if token.startswith("-"):
            continue
        if token in _PIP_MUTATIONS:
            return args[index:]
        return None
    return None


def _is_pip_basename(name: str) -> bool:
    if name in _PIP_BASENAMES:
        return True
    return name.startswith("pip3.")


def _is_python_basename(name: str) -> bool:
    if name in {"python", "python3"}:
        return True
    return name.startswith("python3.")
