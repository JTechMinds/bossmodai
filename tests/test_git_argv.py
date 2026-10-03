"""Agent git is scoped by the path jail, not by a repository fence.

New projects get their own ``.git`` outside the application install. Any
repository the path jail lets an agent touch may be used with git: a clone
from ``/projects`` into ``/me``, or a repo nested inside a project. Git never
discovers the application repository by walking up, because every shell
command runs with ``GIT_CEILING_DIRECTORIES`` set to the parents of the jail
roots. ``git -C /projects/<slug>`` still matches the plain ``git <sub>`` seed
rules, and operator CLI Deny rows are not rewritten.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli import filesystem, install_layout
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.locked_clone_outcome import PATH_JAIL_STEER
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.shell_executor import execute_shell_command
from db.cli_policy_rules import reconcile_hardened_seed_rules
from db.floors import LOBBY_ID


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _lobby_projects() -> Path:
    """The folder a Lobby agent sees as ``/projects``. New agents live in Lobby."""
    return floor_root(LOBBY_ID)


def _agent_and_state():
    agent = db.create_agent("Repo Clerk", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_app(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    init = _git(path, "init")
    assert init.returncode == 0, init.stderr
    _git(path, "config", "user.email", "app@example.test")
    _git(path, "config", "user.name", "App")
    (path / "README.md").write_text("application\n", encoding="utf-8")
    assert _git(path, "add", "README.md").returncode == 0
    assert _git(path, "commit", "-m", "app").returncode == 0


def _branches(path: Path) -> str:
    listed = _git(path, "branch", "--list")
    assert listed.returncode == 0, listed.stderr
    return listed.stdout


def _approve_and_run(agent, state, command: str):
    """Run *command*, which the default shell policy sends to approval, as approved."""
    pending = execute_bm_cli(agent, state, command)
    assert pending.approval_required is True, pending.detail
    return execute_approved_command(
        agent,
        state,
        command,
        approval_request_id=pending.approval_request_id or "",
    )


def _assert_path_jail_block(result) -> None:
    assert result.ok is False
    assert result.approval_required is False
    assert result.kind == "host_deny"
    assert PATH_JAIL_STEER in result.detail
    assert "resolves outside the allowed workspace roots" in result.detail


def test_create_project_gets_its_own_git_outside_the_install() -> None:
    agent, state = _agent_and_state()
    created = execute_bm_cli(agent, state, "mkdir /projects/poc-own")
    assert created.ok is True, created.detail

    project = _lobby_projects() / "poc-own"
    install = install_layout.app_install_root().resolve()
    assert (project / ".git").is_dir()
    toplevel = _git(project, "rev-parse", "--show-toplevel")
    assert toplevel.returncode == 0, toplevel.stderr
    assert Path(toplevel.stdout.strip()).resolve() == project.resolve()
    assert install not in project.resolve().parents
    assert project.resolve() != install
    assert not (_lobby_projects() / ".git").exists()


def test_existing_in_tree_projects_are_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "bossmod-install"
    legacy = install / "artifacts" / "projects" / "legacy-poc"
    legacy.mkdir(parents=True)
    (legacy / "keep.txt").write_text("operator data\n", encoding="utf-8")
    separated = tmp_path / "bossmod-data" / "company"
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(separated))

    agent, state = _agent_and_state()
    created = execute_bm_cli(agent, state, "mkdir /projects/fresh-poc")
    assert created.ok is True, created.detail

    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "operator data\n"
    assert not (legacy / ".git").exists()
    fresh = separated / LOBBY_ID / "fresh-poc"
    assert (fresh / ".git").is_dir()
    assert install.resolve() not in fresh.resolve().parents

    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(install / "artifacts" / "company"))
    with pytest.raises(ValueError, match="outside the BossMod application install"):
        _lobby_projects()
    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "operator data\n"


def test_git_dash_c_projects_path_is_that_project_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git -C /projects/<slug>`` rewrites onto that project's real root.

    The command is issued from ``/me``. Status, commit, and checkout stay
    inside the project. The application install and a real repository
    outside every jail root are refused by the path jail, and the parent of
    the projects mount is not a repository to git.
    """
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    other = tmp_path / "other-repo"
    _init_app(other)
    before_install = _branches(install)
    head = _git(install, "rev-parse", "HEAD").stdout.strip()
    before_other = _branches(other)
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    written = execute_bm_cli(
        agent,
        state,
        "write /projects/diablo-poc/readme.txt",
        content="project notes\n",
    )
    assert written.ok is True, written.detail
    project = _lobby_projects() / "diablo-poc"
    assert get_cli_cwd(agent.id) == "/me"

    status = execute_bm_cli(agent, state, "git -C /projects/diablo-poc status")
    assert status.ok is True, status.detail
    assert status.kind == "shell"
    assert "readme.txt" in (status.prompt_content or "")

    absolute = execute_bm_cli(agent, state, f"git -C {project} status --short")
    assert absolute.ok is True, absolute.detail
    assert "readme.txt" in (absolute.prompt_content or "")

    added = execute_bm_cli(agent, state, "git -C /projects/diablo-poc add readme.txt")
    assert added.ok is True, added.detail
    committed = execute_bm_cli(
        agent,
        state,
        'git -C /projects/diablo-poc commit -m "project note"',
    )
    assert committed.ok is True, committed.detail
    assert _git(project, "log", "-1", "--pretty=%s").stdout.strip() == "project note"
    assert "readme.txt" in _git(project, "ls-files").stdout

    checkout = execute_bm_cli(
        agent,
        state,
        "git -C /projects/diablo-poc checkout -b poc-branch",
    )
    assert checkout.ok is False
    assert checkout.approval_required is True
    assert "Switch branches" in (checkout.detail or "")
    approved = execute_approved_command(
        agent,
        state,
        "git -C /projects/diablo-poc checkout -b poc-branch",
        approval_request_id=checkout.approval_request_id or "",
    )
    assert approved.ok is True, approved.detail
    assert "poc-branch" in _branches(project)
    assert _branches(install) == before_install
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head

    # The floor folder is a jail root with no repository; the ceiling at the
    # company root stops discovery there.
    parent = _approve_and_run(agent, state, "git -C /projects/diablo-poc/.. status")
    assert parent.ok is False
    assert "not a git repository" in (parent.prompt_content or "")

    # Approval is not a jailbreak: the path jail refuses both at execution.
    app = _approve_and_run(agent, state, f"git -C {install} status")
    _assert_path_jail_block(app)

    other_repo = _approve_and_run(agent, state, f"git -C {other} status")
    _assert_path_jail_block(other_repo)
    assert _branches(other) == before_other
    assert _branches(install) == before_install
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head


def test_git_clone_between_projects_and_me_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone from the projects mount into ``/me`` crosses two jail roots."""
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    before_install = _branches(install)
    head = _git(install, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    written = execute_bm_cli(
        agent,
        state,
        "write /projects/src-poc/main.py",
        content="print('src')\n",
    )
    assert written.ok is True, written.detail
    added = execute_bm_cli(agent, state, "git -C /projects/src-poc add main.py")
    assert added.ok is True, added.detail
    committed = execute_bm_cli(
        agent,
        state,
        'git -C /projects/src-poc commit -m "source"',
    )
    assert committed.ok is True, committed.detail

    cloned = _approve_and_run(agent, state, "git clone /projects/src-poc /me/src-work")
    assert cloned.ok is True, cloned.detail

    work = agent_artifact_dir(agent.storage_key) / "src-work"
    assert (work / ".git").is_dir()
    assert (work / "main.py").read_text(encoding="utf-8") == "print('src')\n"
    assert _git(work, "log", "-1", "--pretty=%s").stdout.strip() == "source"
    assert _branches(install) == before_install
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head


def test_git_in_nested_repo_under_project_runs() -> None:
    """A repository one level inside a project is usable from its own cwd."""
    _enable_shell()
    agent, state = _agent_and_state()
    written = execute_bm_cli(
        agent,
        state,
        "write /projects/outer/source_code/app.py",
        content="print('nested')\n",
    )
    assert written.ok is True, written.detail
    nested = _lobby_projects() / "outer" / "source_code"
    init = _git(nested, "init")
    assert init.returncode == 0, init.stderr

    entered = execute_bm_cli(agent, state, "cd /projects/outer/source_code")
    assert entered.ok is True, entered.detail
    added = execute_bm_cli(agent, state, "git add app.py")
    assert added.ok is True, added.detail
    committed = execute_bm_cli(agent, state, 'git commit -m "nested work"')
    assert committed.ok is True, committed.detail

    # Plain ``git status`` / ``git log`` are virtual git pinned to ``/me``;
    # ``-C .`` sends them to shell git in the nested repository.
    status = execute_bm_cli(agent, state, "git -C . status --short")
    assert status.ok is True, status.detail
    assert status.kind == "shell"
    logged = execute_bm_cli(agent, state, "git -C . log --oneline")
    assert logged.ok is True, logged.detail
    assert "nested work" in (logged.prompt_content or "")

    toplevel = _git(nested, "rev-parse", "--show-toplevel")
    assert Path(toplevel.stdout.strip()).resolve() == nested.resolve()
    assert _git(nested, "log", "-1", "--pretty=%s").stdout.strip() == "nested work"


def test_git_never_discovers_the_application_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/me`` lives inside the app checkout; git stops at the top of ``/me``."""
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    before_install = _branches(install)
    head = _git(install, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", install / "artifacts" / "agents")

    agent, state = _agent_and_state()
    me = agent_artifact_dir(agent.storage_key)
    assert install.resolve() in me.resolve().parents
    shutil.rmtree(me / ".git", ignore_errors=True)
    assert not (me / ".git").exists()
    (me / "sub").mkdir(parents=True, exist_ok=True)
    # Without a ceiling, git from here discovers the application checkout.
    unceiled = _git(me / "sub", "rev-parse", "--show-toplevel")
    assert Path(unceiled.stdout.strip()).resolve() == install.resolve()

    status = _approve_and_run(agent, state, "git -C /me/sub status")
    assert status.ok is False
    assert "not a git repository" in (status.prompt_content or "")

    toplevel = _approve_and_run(agent, state, "git -C /me/sub rev-parse --show-toplevel")
    assert toplevel.ok is False
    assert "not a git repository" in (toplevel.prompt_content or "")

    assert _branches(install) == before_install
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head


def test_every_shell_command_gets_a_git_ceiling_at_the_jail_roots(tmp_path: Path) -> None:
    """The ceiling is set for any argv and cannot be widened by extra_env."""
    first = tmp_path / "company" / "floor-a"
    second = tmp_path / "agents" / "agent_0001"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    result = execute_shell_command(
        "env",
        cwd=second,
        allowed_roots=(first, second),
        extra_env={"GIT_CEILING_DIRECTORIES": ""},
    )

    assert result.exit_code == 0, result.stderr
    expected = os.pathsep.join(
        sorted({str(first.resolve().parent), str(second.resolve().parent)})
    )
    assert f"GIT_CEILING_DIRECTORIES={expected}" in result.stdout.splitlines()


def test_operator_deny_picks_stay_untouched() -> None:
    checkout = next(
        rule
        for rule in db.list_cli_policy_rules()
        if rule.pattern == "git checkout" and rule.agent_id is None
    )
    denied_checkout = db.update_cli_policy_rule(checkout.id, tier="never_allowed")
    assert denied_checkout is not None
    custom = db.create_cli_policy_rule(
        tier="never_allowed",
        pattern="curl http://deny-pick.example",
        match_mode="prefix",
        description="Operator deny pick",
    )
    policy_engine.reload()

    agent, state = _agent_and_state()
    _enable_shell()
    created = execute_bm_cli(agent, state, "mkdir /projects/deny-poc")
    assert created.ok is True, created.detail
    entered = execute_bm_cli(agent, state, "cd /projects/deny-poc")
    assert entered.ok is True
    blocked = execute_bm_cli(agent, state, "git checkout -b feature")
    assert blocked.ok is False

    dash_c_denied = execute_bm_cli(
        agent,
        state,
        "git -C /projects/deny-poc checkout -b feature",
    )
    assert dash_c_denied.ok is False

    reconcile_hardened_seed_rules()
    policy_engine.reload()
    checkout_after = db.get_cli_policy_rule(checkout.id)
    custom_after = db.get_cli_policy_rule(custom.id)
    assert checkout_after is not None
    assert checkout_after.tier == "never_allowed"
    assert checkout_after.pattern == "git checkout"
    assert custom_after is not None
    assert custom_after.tier == "never_allowed"
    assert custom_after.pattern == "curl http://deny-pick.example"
