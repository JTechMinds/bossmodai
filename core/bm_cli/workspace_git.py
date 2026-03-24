"""BossMod AI — Per-agent workspace Git support for /me files."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent

_GITIGNORE_MANAGED_LINES = [
    "scratchpad/",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
    "*.swo",
    "*~",
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.orig",
    "*.rej",
    "__pycache__/",
    ".idea/",
    ".vscode/",
]


def ensure_agent_workspace_repo(agent: Agent) -> Path:
    """Ensure the agent's personal workspace is a usable Git repository."""
    root = agent_artifact_dir(agent.storage_key)
    git_dir = root / ".git"
    repo_exists = git_dir.exists()
    if not repo_exists:
        _run_git(root, ["init"], agent=agent)

    gitignore = root / ".gitignore"
    gitignore_changed = _ensure_workspace_gitignore(gitignore)
    if not repo_exists or gitignore_changed:
        _run_git(root, ["add", "--", ".gitignore"], agent=agent)
        if _has_staged_changes(root):
            message = "Initialize agent workspace" if not repo_exists else "Update workspace Git ignore rules"
            _run_git(root, ["commit", "-m", message], agent=agent)
    return root


def auto_commit_workspace_change(agent: Agent, virtual_path: str, *, reason: str) -> str | None:
    """Stage and commit one tracked workspace change when appropriate."""
    if not _is_trackable_virtual_path(virtual_path):
        ensure_agent_workspace_repo(agent)
        return None

    root = ensure_agent_workspace_repo(agent)
    relative_path = _repo_relative_path(agent, virtual_path)
    _run_git(root, ["add", "--", ".gitignore", relative_path], agent=agent)
    if not _has_staged_changes(root):
        return None

    _run_git(root, ["commit", "-m", reason], agent=agent)
    return _current_head(root)


def build_git_status(agent: Agent, *, relative_path: str | None = None) -> str:
    """Return a concise status view for the agent workspace repo."""
    root = ensure_agent_workspace_repo(agent)
    args = ["status", "--short", "--branch"]
    if relative_path:
        args.extend(["--", relative_path])
    return _run_git(root, args, agent=agent).stdout.strip()


def build_git_log(agent: Agent, *, limit: int = 10) -> str:
    """Return a concise recent history view for the agent workspace repo."""
    root = ensure_agent_workspace_repo(agent)
    safe_limit = max(1, min(limit, 50))
    return _run_git(
        root,
        ["log", f"-n{safe_limit}", "--pretty=format:%h %s"],
        agent=agent,
    ).stdout.strip()


def build_git_diff(agent: Agent, *, relative_path: str | None = None) -> str:
    """Return working-tree diff output for the agent workspace repo."""
    root = ensure_agent_workspace_repo(agent)
    args = ["diff", "--"]
    if relative_path:
        args.append(relative_path)
    return _run_git(root, args, agent=agent).stdout.strip()


def build_git_show(agent: Agent, revision: str, *, relative_path: str | None = None) -> str:
    """Return Git show output for one revision or one file at one revision."""
    root = ensure_agent_workspace_repo(agent)
    if relative_path:
        return _run_git(root, ["show", f"{revision}:{relative_path}"], agent=agent).stdout
    return _run_git(root, ["show", "--stat", "--oneline", revision], agent=agent).stdout


def restore_git_revision(
    agent: Agent,
    *,
    revision: str | None,
    relative_path: str,
    reason: str,
) -> str | None:
    """Restore one tracked file from HEAD or a specific revision and commit the result."""
    root = ensure_agent_workspace_repo(agent)
    args = ["restore"]
    if revision:
        args.extend(["--source", revision])
    args.extend(["--", relative_path])
    _run_git(root, args, agent=agent)
    _run_git(root, ["add", "--", relative_path], agent=agent)
    if not _has_staged_changes(root):
        return None
    _run_git(root, ["commit", "-m", reason], agent=agent)
    return _current_head(root)


def repo_relative_path_for_virtual_path(agent: Agent, cwd: str, raw_path: str) -> str:
    """Resolve a virtual /me path to a Git repo-relative path."""
    resolved = resolve_cli_path(agent.storage_key, cwd, raw_path)
    if resolved.mount != "me":
        raise ValueError("Git workspace commands only support /me paths.")
    return resolved.real_path.resolve().relative_to(agent_artifact_dir(agent.storage_key).resolve()).as_posix()


def _repo_relative_path(agent: Agent, virtual_path: str) -> str:
    resolved = resolve_cli_path(agent.storage_key, "/", virtual_path)
    return resolved.real_path.resolve().relative_to(agent_artifact_dir(agent.storage_key).resolve()).as_posix()


def _is_trackable_virtual_path(virtual_path: str) -> bool:
    return virtual_path.startswith("/me/") and not (
        virtual_path == "/me/scratchpad" or virtual_path.startswith("/me/scratchpad/")
    )


def _ensure_workspace_gitignore(path: Path) -> bool:
    existing_lines = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    missing = [line for line in _GITIGNORE_MANAGED_LINES if line not in existing_lines]
    if not path.exists():
        path.write_text(_render_gitignore(_GITIGNORE_MANAGED_LINES), encoding="utf-8")
        return True
    if not missing:
        return False
    updated = existing_lines + ["", "# BossMod managed ignores", *missing]
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    return True


def _render_gitignore(lines: list[str]) -> str:
    return "\n".join(["# BossMod managed ignores", *lines]) + "\n"


def _has_staged_changes(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 1


def _current_head(root: Path) -> str:
    return _run_git(root, ["rev-parse", "HEAD"]).stdout.strip()


def _run_git(root: Path, args: list[str], *, agent: Agent | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if agent is not None:
        author_name = agent.name or agent.storage_key
        author_email = f"{agent.storage_key}@bossmod.local"
        env.setdefault("GIT_AUTHOR_NAME", author_name)
        env.setdefault("GIT_AUTHOR_EMAIL", author_email)
        env.setdefault("GIT_COMMITTER_NAME", author_name)
        env.setdefault("GIT_COMMITTER_EMAIL", author_email)
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Git command failed.").strip()
        raise ValueError(message)
    return result
